import type { AgentsDraftFields_Output, AgentsDraftView, InterviewView } from "@comit/shared";

/** Whole rubles → "180к" (thousands, rounded) — the compact form used across the design. */
export function formatThousands(rub: number): string {
  return `${Math.round(rub / 1000)}к`;
}

/** Mandatory salary interval (TZ §2.2) as "180–240к ₽"; a missing bound is shown honestly. */
export function formatSalaryRange(fromRub: number | null | undefined, toRub: number | null | undefined): string {
  if (fromRub == null && toRub == null) return "ЗП не указана";
  if (fromRub == null) return `до ${formatThousands(toRub ?? 0)} ₽`;
  if (toRub == null || toRub === fromRub) return `${formatThousands(fromRub)} ₽`;
  return `${Math.round(fromRub / 1000)}–${formatThousands(toRub)} ₽`;
}

export const WORK_TYPES = ["onsite", "remote", "hybrid"] as const;

export const WORK_TYPE_LABEL: Record<NonNullable<AgentsDraftFields_Output["type"]>, string> = {
  onsite: "Офис",
  remote: "Удалённо",
  hybrid: "Гибрид",
};

export const SPECIALIZATION_LABEL: Record<NonNullable<AgentsDraftFields_Output["class"]>, string> = {
  backend: "Backend",
  frontend: "Frontend",
  fullstack: "Fullstack",
  mobile: "Mobile",
  devops: "DevOps",
  data: "Data",
  ml: "ML",
  qa: "QA",
};

/** Role title per direction, as used in the design subtitles («Backend-разработчик Middle+»). */
export const ROLE_LABEL: Record<NonNullable<AgentsDraftFields_Output["class"]>, string> = {
  backend: "Backend-разработчик",
  frontend: "Frontend-разработчик",
  fullstack: "Fullstack-разработчик",
  mobile: "Mobile-разработчик",
  devops: "DevOps-инженер",
  data: "Data-аналитик",
  ml: "ML-инженер",
  qa: "QA-инженер",
};

/** Requirement keys with the highest declared level first; ties keep insertion order. */
export function topRequirements(levels: Record<string, number> | undefined, limit: number): string[] {
  return Object.entries(levels ?? {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, limit)
    .map(([key]) => key);
}

export type VacancyBadge = { label: string; tone: "success" | "warning" | "danger" | "muted" };

/** Human status of a vacancy from its business status, archive flag and publication handoff. */
export function vacancyBadge(vacancy: AgentsDraftView): VacancyBadge {
  if (vacancy.archived) return { label: "В архиве", tone: "muted" };
  if (vacancy.business_status === "closed") return { label: "Закрыта", tone: "muted" };
  if (vacancy.publication?.status === "failed" || vacancy.state === "failed") return { label: "Ошибка публикации", tone: "danger" };
  if (vacancy.business_status === "open") {
    return vacancy.state === "editing" || vacancy.state === "summary"
      ? { label: "Активная · есть правки", tone: "warning" }
      : { label: "Активная", tone: "success" };
  }
  if (vacancy.state === "creating" || vacancy.publication?.status === "pending") return { label: "Публикуется", tone: "warning" };
  return { label: "Черновик", tone: "muted" };
}

/** Title fallback for drafts created without one. */
export function vacancyTitle(vacancy: AgentsDraftView): string {
  return vacancy.fields.title?.trim() || "Вакансия без названия";
}

/** Interview date in the interview's own IANA zone, e.g. «среда, 8 октября в 15:00». */
export function formatInterviewTime(interview: InterviewView): string {
  const date = new Date(interview.starts_at);
  const day = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: interview.time_zone }).format(date);
  const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: interview.time_zone }).format(date);
  return `${day} в ${time}`;
}

/**
 * Public candidate label. Matching publishes an anonymous `Candidate <uuid>` (names and contacts stay
 * private until an invitation is accepted, TZ §2.2); show it as a short, readable handle instead.
 */
export function candidateLabel(displayName: string, candidateId: string): string {
  if (!displayName.startsWith("Candidate ")) return displayName;
  return `Кандидат ${candidateId.replace(/-/g, "").slice(0, 4).toUpperCase()}`;
}
