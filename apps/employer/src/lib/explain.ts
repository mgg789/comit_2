import type { ClaimStatus, Grade, MatchedFeature, ScoreBreakdown } from "@comit/shared";

/**
 * Presentation of matching-engine explanations. Scores, weights and factor codes are computed
 * server-side (services/matching-engine); this module only translates them — it never re-scores.
 */

/** Main-v2 score is bounded to [0,1] (docs/matching.md); shown as a whole percent. */
export function matchPercent(explanation: ScoreBreakdown): number {
  return Math.round(Math.min(1, Math.max(0, explanation.score)) * 100);
}

/** Labels for strict-filter pass factors emitted by combine_scoring.strict_filter. */
const FILTER_FACTOR_LABEL: Record<string, string | null> = {
  class_match: "Направление совпадает",
  class_unrestricted: "Направление не ограничено",
  salary_overlap: "Зарплатные ожидания в вилке",
  work_type_accepted: "Подходит формат работы",
  remote_location: "Удалёнка — город не важен",
  same_city: "Тот же город",
  relocation_accepted: "Готов(а) к переезду",
  strict_schema_current: null,
};

/** Human-readable strict-filter factors; unknown codes are shown verbatim rather than hidden. */
export function filterFactorLabels(factors: readonly string[]): string[] {
  return factors.flatMap((code) => {
    const label = FILTER_FACTOR_LABEL[code];
    if (label === null) return [];
    return [label ?? code];
  });
}

export const CLAIM_STATUS_LABEL: Record<ClaimStatus, string> = {
  confirmed: "подтверждено",
  partial: "частично подтверждено",
  self_reported: "заявлено",
  pending: "на проверке",
  rejected: "не подтверждено",
};

export const GRADE_LABEL: Record<Grade, string> = {
  intern: "Intern",
  junior: "Junior",
  middle: "Middle",
  senior: "Senior",
};

/** Requirement weight scale 0..7 of the strict schema, named as in the vacancy screen. */
export function requirementWeightLabel(level: number): string {
  if (level >= 6) return "Обязателен";
  if (level >= 4) return "Важен";
  if (level >= 2) return "Средний вес";
  return "Желателен";
}

/** One explanation row per matched coordinate: candidate level vs vacancy target and evidence status. */
export function describeFeature(feature: MatchedFeature): string {
  const status = feature.status ? CLAIM_STATUS_LABEL[feature.status] : "нет данных";
  return `${feature.key}: ${feature.candidate_level}/7 при цели ${feature.target_level}/7 · ${status}`;
}

/** Contribution channels of the main score, in the order the policy sums them. */
export function scoreChannels(explanation: ScoreBreakdown): Array<{ key: string; label: string; value: number }> {
  return [
    { key: "rating", label: "Рейтинг в категории", value: explanation.rating_contribution },
    { key: "strict", label: "Навыки к требованиям", value: explanation.strict_contribution },
    { key: "semantic", label: "Смысловая близость", value: explanation.semantic_contribution },
  ];
}
