/** Conservative local extraction; raw participant feedback never leaves this module. */
import { rejectionReasonCategories, type RecommendationEvidence, type RejectionReasonCategory } from "@comit/shared";

/** Private observation shape read only from the participant store. */
export interface FeedbackObservation { process_id: string; owner_id: string; categories: unknown; feedback: string | null; }
const phrases: Readonly<Partial<Record<RejectionReasonCategory, RegExp>>> = {
  technical_skills: /(?:не хватает|недостаточно|недостаточн[а-я]*) (?:техническ[а-я]* (?:знаний|навыков)|знаний|навыков)|техническ[а-я]* (?:навыки|знания) (?:не соответствуют|слабые)|insufficient technical skills/iu,
  experience: /(?:недостаточно|не хватает) опыта|недостаточный опыт|lack of experience|insufficient experience/iu,
  communication: /(?:трудности|проблемы) (?:с коммуникацией|в общении)|недостаточн[а-я]* коммуникативн[а-я]*|poor communication/iu,
  salary: /(?:зарплатн[а-я]* ожидания|ожидаемая зарплата) (?:слишком высок[а-я]*|выше бюджета)|не совпадают (?:зарплатные|финансовые) ожидания|salary expectations exceed/iu,
  work_format: /не подходит (?:формат работы|удалённая работа|удаленная работа)|несовместимый формат работы|work format mismatch/iu,
  location: /не готов[а-я]* к (?:переезду|релокации)|не подходит местоположение|location mismatch|unwilling to relocate/iu,
};

/** Deduplicate by process AND employer; suppress categories below three independent observations. */
export function aggregateFeedback(rows: FeedbackObservation[]): RecommendationEvidence[] {
  const groups = new Map<RejectionReasonCategory, { processes: Set<string>; employers: Set<string> }>();
  for (const row of rows) {
    const categories = new Set<RejectionReasonCategory>();
    if (Array.isArray(row.categories)) for (const value of row.categories) {
      if (rejectionReasonCategories.some(category => category === value)) categories.add(value as RejectionReasonCategory);
    }
    const prose = (row.feedback ?? "").slice(0, 10000).normalize("NFKC");
    for (const category of rejectionReasonCategories) if (phrases[category]?.test(prose)) categories.add(category);
    for (const category of categories) {
      const group = groups.get(category) ?? { processes: new Set<string>(), employers: new Set<string>() };
      group.processes.add(row.process_id); group.employers.add(row.owner_id); groups.set(category, group);
    }
  }
  return [...groups].flatMap(([category, value]) => {
    const count = Math.min(value.processes.size, value.employers.size);
    return count >= 3 ? [{ source: "employer_feedback" as const, category, count }] : [];
  }).sort((left, right) => left.category.localeCompare(right.category));
}

/** Honest offline guidance based only on disclosed categories, without manufactured LLM output. */
export function fallbackText(evidence: RecommendationEvidence[], unavailable = false): string {
  if (!evidence.length) return "Пока недостаточно независимых наблюдений для недельных рекомендаций. Единичные отказы и повторные запуски одной вакансии не используются для выводов.";
  const advice: Record<string, string> = {
    technical_skills: "В отзывах повторяется тема технических навыков. Проверьте, насколько ясно в профиле описаны подтверждённые навыки, и подготовьте примеры их применения.",
    experience: "В отзывах повторяется тема опыта. Уточните свой вклад и результат в описаниях проектов.",
    communication: "В отзывах повторяется тема общения. Подготовьте короткий рассказ о решениях и работе в команде.",
    salary: "В отзывах повторяется тема зарплатных ожиданий. Проверьте актуальность указанного диапазона.",
    work_format: "В отзывах повторяется тема формата работы. Проверьте выбранные форматы в профиле.",
    location: "В отзывах повторяется тема местоположения. Проверьте город и предпочтения по переезду.",
    other: "В отзывах повторяются другие причины. По этим отзывам нельзя понять, что именно стоит улучшить.",
    class_mismatch: "Специализация профиля не совпала с требованиями нескольких вакансий. Проверьте выбранное направление.",
    salary_disjoint: "Диапазон зарплаты не пересекался с несколькими вакансиями. Проверьте актуальность ожиданий.",
    work_type_unknown: "Не указан формат работы. Уточните допустимые форматы в профиле.",
    work_type_mismatch: "Формат работы не совпал с несколькими вакансиями. Проверьте свои предпочтения.",
    location_unknown: "Для нескольких вакансий не хватило данных о городе. Уточните местоположение.",
    relocation_unknown: "Не указана готовность к переезду. Проверьте предпочтения по релокации.",
    relocation_not_ready: "Несколько вакансий требовали другого города при отсутствии согласия на переезд.",
    relocation_destination_mismatch: "Города нескольких вакансий не совпали с выбранными направлениями переезда.",
    main_top_k_omission: "При большом числе подходящих людей ваш профиль не всегда показывался работодателям. Это не означает, что вам не хватает навыков. Уточните, какую работу ищете, и добавьте понятные примеры проектов.",
    unranked_by_reranker: "Ваш профиль не всегда попадал в список, который просматривает работодатель. Это не оценка ваших способностей. Проверьте, ясно ли описаны ваш опыт и желаемая работа.",
    reranker_reserve: "В некоторых случаях сначала показывали других подходящих людей. Это не означает, что вы плохо подготовлены. Добавьте короткие примеры своих задач и результатов.",
    system_fallback: "Часть подбора временно работала в упрощённом режиме. Это не связано с недостатками кандидата. Менять резюме только по этой причине не нужно.",
    strict_schema_mismatch: "Часть вакансий временно не участвовала в подборе из-за технической проблемы. Исправлять резюме из-за этого не нужно.",
    selection_state_excluded: "По некоторым вакансиям работодатель уже видел ваш профиль, поэтому он не показывался повторно. Это не говорит о ваших навыках.",
  };
  return [(unavailable ? "Генерация рекомендаций временно недоступна. Ниже — общие подсказки по известным причинам." : "Наблюдения за неделю:"),
    ...evidence.map(item => `${advice[item.category] ?? "Категория не позволяет сделать вывод о квалификации."} Случаев: ${item.count}.`),
    "Отзывы отражают мнения работодателей и не являются объективной оценкой квалификации."].join("\n\n").slice(0, 3000);
}
