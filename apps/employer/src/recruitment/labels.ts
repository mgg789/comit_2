import type { InterviewKind, InterviewOutcome, RecruitmentState } from "@comit/shared";

/** Process states as both sides see them (TZ §2.2: statuses visible to employer and candidate). */
export const RECRUITMENT_STATE_LABEL: Record<RecruitmentState, string> = {
  sent: "Приглашение отправлено",
  viewed: "Приглашение просмотрено",
  accepted: "Приглашение принято",
  declined: "Кандидат отказался",
  rejected: "Отказ работодателя",
  offer_sent: "Оффер отправлен",
  hired: "Принят на работу",
  offer_declined: "Оффер отклонён",
  withdrawn: "Кандидат вышел из процесса",
};

/** Terminal states: no further transitions are allowed by api-core. */
export const TERMINAL_STATES: ReadonlySet<RecruitmentState> = new Set(["declined", "rejected", "hired", "offer_declined", "withdrawn"]);

export const INTERVIEW_KINDS: readonly InterviewKind[] = ["technical", "general"];
export const INTERVIEW_OUTCOMES: readonly InterviewOutcome[] = ["passed", "failed", "neutral"];

export const INTERVIEW_KIND_LABEL: Record<InterviewKind, string> = {
  technical: "Техническое",
  general: "Общее",
};

export const INTERVIEW_OUTCOME_LABEL: Record<InterviewOutcome, string> = {
  passed: "Пройдено",
  failed: "Не пройдено",
  neutral: "Без решения",
};

/** Funnel steps of the «Резюме» stepper, from the design: Подбор → Лента → Собеседование → Оффер → Приём. */
export const FUNNEL_STEPS = ["Подбор", "Лента", "Приглашение", "Собеседование", "Оффер", "Приём"] as const;

/**
 * Index of the furthest reached funnel step for a process. Every process already passed
 * selection and the feed (it starts from a saved like), so the minimum is «Приглашение».
 */
export function funnelStep(state: RecruitmentState, hasInterview: boolean): number {
  if (state === "hired") return 5;
  if (state === "offer_sent" || state === "offer_declined") return 4;
  if (hasInterview) return 3;
  return 2;
}
