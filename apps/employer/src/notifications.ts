import type { RecruitmentEvent } from "@comit/shared";

/** Per-browser cursor of the last recruitment event the employer has opened in the bell menu. */
export const SEEN_KEY = "comit.employer.seen_event_sequence";
/** Already seen history kept in the menu for context, newest first. */
export const NOTIFICATIONS_SHOWN = 20;

/** Minimal storage surface so the cursor logic stays testable without a browser. */
export type CursorStorage = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): CursorStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Last seen event sequence; blocked or corrupted storage reads as «nothing seen». */
export function readSeen(storage: CursorStorage | null = browserStorage()): number {
  try {
    const value = Number(storage?.getItem(SEEN_KEY));
    return Number.isSafeInteger(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

/** Persist the cursor; a blocked storage only means the badge reappears after reload. */
export function writeSeen(value: number, storage: CursorStorage | null = browserStorage()): void {
  try {
    storage?.setItem(SEEN_KEY, String(value));
  } catch {
    // Nothing to recover: the server stays the source of truth for events.
  }
}

/** First sequence to request: unseen events plus a short tail of seen ones for context. */
export function eventsCursor(seen: number): number {
  return Math.max(0, seen - NOTIFICATIONS_SHOWN);
}

/** Number of hiring status changes newer than the cursor. */
export function unseenCount(events: readonly RecruitmentEvent[], seen: number): number {
  return events.filter((event) => event.sequence > seen).length;
}

/** Highest known sequence, never moving the cursor backwards. */
export function latestSequence(events: readonly RecruitmentEvent[], seen: number): number {
  return events.reduce((max, event) => Math.max(max, event.sequence), seen);
}

/** Menu rows: newest first, bounded so a long history never grows the panel. */
export function recentEvents(events: readonly RecruitmentEvent[]): RecruitmentEvent[] {
  return [...events].sort((a, b) => b.sequence - a.sequence).slice(0, NOTIFICATIONS_SHOWN);
}

/** Badge text inside the fixed-width pill. */
export function badgeText(count: number): string {
  return count > 99 ? "99+" : String(count);
}

/** What happened, by event type; the process state alone repeats «Приглашение принято» for every later step. */
const EVENT_LABELS: Record<string, string> = {
  "invitation.sent": "Приглашение отправлено",
  view: "Кандидат открыл приглашение",
  accept: "Кандидат принял приглашение",
  decline: "Кандидат отклонил приглашение",
  withdraw: "Приглашение отозвано",
  reject: "Кандидату отказано",
  "interview.propose": "Предложено время собеседования",
  "interview.accept": "Кандидат подтвердил собеседование",
  "interview.decline": "Кандидат отклонил время собеседования",
  "interview.cancel": "Собеседование отменено",
  "interview.complete": "Собеседование проведено",
  "offer.send": "Оффер отправлен",
  "offer.accept": "Кандидат принял оффер",
  "offer.decline": "Кандидат отклонил оффер",
  "chat.first_employer_message": "Первое сообщение в чате",
  "vacancy.inactive": "Вакансия больше не активна",
};

/** Menu label for one event; unknown future types fall back to the resulting state label. */
export function eventLabel(event: Pick<RecruitmentEvent, "type" | "state">, stateLabels: Record<RecruitmentEvent["state"], string>): string {
  return EVENT_LABELS[event.type] ?? stateLabels[event.state];
}
