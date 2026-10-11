import type { PublicTestingResult } from "@comit/shared";
/** Candidate-facing subject names are also used in the employer's verified test section. */
export function testingSubjectLabel(subject: string): string {
  const names: Record<string,string> = { algorithms:"Алгоритмы",sql:"SQL",rest_api:"REST API",python:"Python",react:"React",docker:"Docker",teamwork:"Работа в команде" };
  return names[subject] ?? "Проверка навыков";
}
/** Explain camera choice separately from uncertain technical detection; no cheating verdict is inferred. */
export function testingPresenceLabel(result: PublicTestingResult): string {
  if (result.camera_status === "denied") return "Кандидат прошёл тест без камеры";
  if (result.camera_status === "unavailable") return "Видеоподтверждение недоступно по техническим причинам";
  if (result.camera_status === "unknown") return "Данных о видеоподтверждении нет";
  const labels: Record<string,string> = { "хороший":"Лицо видно почти всё время", "нормальный":"Лицо видно большую часть времени", "средний":"Присутствие в кадре подтверждено частично", "низкий":"Лицо часто отсутствовало в кадре", "не определён":"Не удалось определить присутствие в кадре" };
  return labels[result.supervision_badge ?? ""] ?? "Не удалось определить присутствие в кадре";
}
