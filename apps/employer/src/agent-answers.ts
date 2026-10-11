import type { AgentsPendingAnswer, AgentsPendingAnswerRequest } from "@comit/shared";

/** Employer-confirmed interval entered as wall-clock time in the browser's displayed timezone. */
export interface InterviewAvailabilityDraft {
  starts_at: string;
  ends_at: string;
  duration_minutes: string;
  location: string;
  format: "remote" | "onsite";
  kind: "technical" | "general";
}

/** Detect the server's structured availability question, without inferring authority from its text. */
export function asksForAvailability(question: AgentsPendingAnswer): boolean {
  return !!question.task_id && Array.isArray(question.answer_schema?.required) && question.answer_schema.required.includes("availability");
}

/** Build the existing pending-answer contract; local dates use the same timezone as the form label. */
export function organizationQuestionAnswer(question: AgentsPendingAnswer, text: string,
  slot: InterviewAvailabilityDraft): AgentsPendingAnswerRequest["answer"] {
  if (!question.task_id) return text;
  if (!asksForAvailability(question)) {
    if (!text.trim() || text.length > 8000) throw new Error("Укажите уточнение длиной до 8000 символов.");
    return { instruction: text.trim() };
  }
  const start = new Date(slot.starts_at), end = new Date(slot.ends_at), duration = Number(slot.duration_minutes);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || !Number.isInteger(duration) ||
      duration < 5 || duration > 240 || end.getTime() - start.getTime() < duration * 60_000 ||
      end.getTime() - start.getTime() > 7 * 86_400_000) {
    throw new Error("Укажите интервал, в который помещается собеседование длительностью от 5 до 240 минут.");
  }
  if (start.getTime() <= Date.now()) throw new Error("Начало свободного интервала должно быть в будущем.");
  if (!slot.location.trim() || slot.location.length > 2000) throw new Error("Укажите ссылку или адрес встречи длиной до 2000 символов.");
  return { availability: [{ starts_at: start.toISOString(), ends_at: end.toISOString(),
    duration_minutes: duration, time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    location: slot.location.trim(), format: slot.format, kind: slot.kind }] };
}
