/** Bell cursor and counters; storage is injected, no browser required. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RecruitmentEvent } from "@comit/shared";
import {
  NOTIFICATIONS_SHOWN,
  SEEN_KEY,
  badgeText,
  eventsCursor,
  latestSequence,
  readSeen,
  recentEvents,
  unseenCount,
  writeSeen,
  type CursorStorage,
} from "../src/notifications";

const event = (sequence: number, state: RecruitmentEvent["state"] = "accepted"): RecruitmentEvent => ({
  event_id: `e${sequence}`, sequence, process_id: `p${sequence}`, revision: 1,
  type: "state_changed", state, created_at: "2026-10-09T10:00:00Z",
});

function memory(initial?: string): CursorStorage & { value: string | null } {
  return {
    value: initial ?? null,
    getItem() { return this.value; },
    setItem(_key, value) { this.value = value; },
  };
}

test("counts only events newer than the seen cursor", () => {
  const events = [event(3), event(4), event(5)];
  assert.equal(unseenCount(events, 0), 3);
  assert.equal(unseenCount(events, 4), 1);
  assert.equal(unseenCount(events, 5), 0);
});

test("never moves the cursor backwards", () => {
  assert.equal(latestSequence([event(2), event(7), event(4)], 3), 7);
  assert.equal(latestSequence([event(2)], 9), 9);
  assert.equal(latestSequence([], 0), 0);
});

test("requests unseen events plus a bounded tail of seen history", () => {
  assert.equal(eventsCursor(0), 0);
  assert.equal(eventsCursor(5), 0);
  assert.equal(eventsCursor(100), 100 - NOTIFICATIONS_SHOWN);
});

test("shows newest events first and caps the list", () => {
  const events = Array.from({ length: NOTIFICATIONS_SHOWN + 5 }, (_, index) => event(index + 1));
  const rows = recentEvents(events);
  assert.equal(rows.length, NOTIFICATIONS_SHOWN);
  assert.equal(rows[0]?.sequence, NOTIFICATIONS_SHOWN + 5);
  assert.equal(events[0]?.sequence, 1, "input order is not mutated");
});

test("cursor survives corrupted and blocked storage", () => {
  const store = memory();
  writeSeen(42, store);
  assert.equal(store.value, "42");
  assert.equal(readSeen(store), 42);
  assert.equal(readSeen(memory("not-a-number")), 0);
  assert.equal(readSeen(memory("-3")), 0);
  assert.equal(readSeen(null), 0);
  const blocked: CursorStorage = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
  };
  assert.equal(readSeen(blocked), 0);
  assert.doesNotThrow(() => writeSeen(1, blocked));
  assert.equal(SEEN_KEY, "comit.employer.seen_event_sequence");
});

test("badge fits the pill", () => {
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(99), "99");
  assert.equal(badgeText(150), "99+");
});

test("menu rows say what happened, not the repeated process state", async () => {
  const { eventLabel } = await import("../src/notifications");
  const states = { sent: "Приглашение отправлено", viewed: "Просмотрено", accepted: "Приглашение принято", declined: "", rejected: "", offer_sent: "", hired: "", offer_declined: "", withdrawn: "", expired: "" };
  assert.equal(eventLabel({ type: "interview.propose", state: "accepted" }, states), "Предложено время собеседования");
  assert.equal(eventLabel({ type: "accept", state: "accepted" }, states), "Кандидат принял приглашение");
  assert.equal(eventLabel({ type: "future.event", state: "accepted" }, states), "Приглашение принято");
});
