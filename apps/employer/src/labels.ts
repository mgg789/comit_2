/** Human-readable labels for matching keys shared by employer screens; keys stay canonical. */

import { stackChoices, domainChoices, softChoices } from "@comit/shared";
export { stackChoices, domainChoices, softChoices };

/** Specialization as a short tag (cards, deck). */
export const specializationLabels: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  fullstack: "Fullstack",
  mobile: "Mobile",
  devops: "DevOps",
  data: "Data",
  ml: "ML",
  qa: "QA",
};

const featureLabels: Record<string, string> = Object.fromEntries([...stackChoices, ...domainChoices, ...softChoices]);

/** Label for a stack, domain or soft-skill key; unknown keys stay visible as-is. */
export function featureLabel(key: string): string {
  return featureLabels[key] ?? key;
}

/** Label for a specialization key; unknown keys stay visible as-is. */
export function specializationLabel(key: string): string {
  return specializationLabels[key] ?? key;
}

/** Strict-filter reason codes from matching-engine `strict_filter`, as employer-facing text. */
const filterReasonLabels: Record<string, string> = {
  selection_state_excluded: "Кандидат уже обработан в подборе этой вакансии",
  class_mismatch: "Другая специализация",
  salary_disjoint: "Зарплатные ожидания не пересекаются с вилкой",
  work_type_unknown: "Кандидат не указал формат работы",
  work_type_mismatch: "Не подходит формат работы",
  location_unknown: "Не указан город",
  relocation_unknown: "Другой город, готовность к переезду не указана",
  relocation_not_ready: "Другой город, кандидат не готов к переезду",
  relocation_destination_mismatch: "Другой город, переезд только в другие города",
  strict_schema_mismatch: "Профиль заполнен по устаревшей схеме требований",
};

/** Label for a strict-filter reason; unknown codes stay visible as-is. */
export function filterReasonLabel(code: string): string {
  return filterReasonLabels[code] ?? code;
}
