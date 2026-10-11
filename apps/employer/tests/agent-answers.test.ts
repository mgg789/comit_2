import assert from "node:assert/strict";
import { test } from "node:test";
import type { AgentsPendingAnswer } from "@comit/shared";
import { asksForAvailability, organizationQuestionAnswer, type InterviewAvailabilityDraft } from "../src/agent-answers";

const question: AgentsPendingAnswer = { answer_id: "answer", chat_id: "chat", task_id: "task", prompt: "Уточните", answer_schema: { required: ["instruction"] } };
const start = new Date(Date.now() + 86_400_000);
const slot: InterviewAvailabilityDraft = { starts_at: start.toISOString(), ends_at: new Date(start.getTime() + 3_600_000).toISOString(), duration_minutes: "30", format: "remote", kind: "technical", location: "meeting" };

test("task clarifications use the server object contract and legacy turn answers remain strings", () => {
  assert.deepEqual(organizationQuestionAnswer(question, "  Продолжить опрос  ", slot), { instruction: "Продолжить опрос" });
  assert.equal(organizationQuestionAnswer({ ...question, task_id: null }, "Ответ", slot), "Ответ");
  assert.throws(() => organizationQuestionAnswer(question, " ", slot));
});

test("availability uses numeric duration, explicit timezone and exact employer interval", () => {
  const availability = { ...question, answer_schema: { required: ["availability"] } };
  assert.equal(asksForAvailability(availability), true);
  assert.deepEqual(organizationQuestionAnswer(availability, "", slot), { availability: [{
    starts_at: slot.starts_at, ends_at: slot.ends_at, duration_minutes: 30,
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone, location: "meeting", format: "remote", kind: "technical",
  }] });
  for (const change of [{ ends_at: slot.starts_at }, { duration_minutes: "2" }, { duration_minutes: "30.5" },
    { starts_at: "invalid" }, { starts_at: "2020-01-01T10:00:00Z" }, { location: " " }]) {
    assert.throws(() => organizationQuestionAnswer(availability, "", { ...slot, ...change }));
  }
});
