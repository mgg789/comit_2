import type { RecruitmentView } from "@comit/shared";

type StageProcess = Pick<RecruitmentView, "state" | "allowed_actions" | "chat_available"> & { offers: readonly unknown[]; interviews: ReadonlyArray<Pick<RecruitmentView["interviews"][number], "state">> };

/** Historical progress of a saved candidate, including an interview proposal. */
export function recruitmentStage(process?: StageProcess): number {
  if (process?.state === "hired") return 4;
  if (process?.offers.length) return 3;
  if (process?.interviews.some(item => ["proposed", "scheduled", "completed"].includes(item.state))) return 2;
  return process ? 1 : 0;
}

/** Primary tile action; backend permissions still gate every progression command. */
export function recruitmentTileAction(process?: StageProcess): { label: string; command: "invite" | "interview_propose" | "offer_send" | "chat"; disabled: boolean } {
  const stage = recruitmentStage(process);
  if (!process) return { label: "Пригласить", command: "invite", disabled: false };
  if (!["sent", "viewed", "accepted", "offer_sent", "hired"].includes(process.state) || stage >= 3) return { label: "Открыть чат", command: "chat", disabled: !process.chat_available };
  const command = stage === 2 ? "offer_send" : "interview_propose";
  return { label: stage === 2 ? "Сделать оффер" : "Собеседование", command, disabled: !process.allowed_actions.includes(command) };
}
