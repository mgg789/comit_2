import type { RecruitmentView } from "@comit/shared";

/** States in which the existing contacts endpoint may authorize candidate identity. */
export function mayReadCandidateName(state: RecruitmentView["state"]): boolean {
  return ["accepted", "offer_sent", "hired", "offer_declined"].includes(state);
}

/** Append only a name supplied by the authorized contacts endpoint after acceptance. */
export function recruitmentChatTitle(process: Pick<RecruitmentView, "state" | "terms" | "kind">, name?: string): string {
  const title = process.terms?.vacancy_title || "Вакансия";
  if (process.kind === "contact_only" && process.terms?.candidate_display_name?.trim()) return `${title} — ${process.terms.candidate_display_name.trim()}`;
  return mayReadCandidateName(process.state) && name?.trim() ? `${title} — ${name.trim()}` : title;
}
