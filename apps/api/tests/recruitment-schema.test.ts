/** Transport-level tests ensure each recruitment command rejects unrelated client fields. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import type { RecruitmentAction } from "@comit/shared";
import { invitationBodySchema, rejectSavedBodySchema, recruitmentActionSchema, recruitmentEventPageSchema,
  recruitmentViewSchema } from "../src/recruitment/schemas.js";

test("recruitment commands accept every action and reject extra fields without stripping", async () => {
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  app.post("/actions", { schema: { body: recruitmentActionSchema } }, async () => ({ ok: true }));
  app.post("/invitations", { schema: { body: invitationBodySchema } }, async () => ({ ok: true }));
  app.post("/reject-saved", { schema: { body: rejectSavedBodySchema } }, async () => ({ ok: true }));
  try {
    const identity = { expected_revision: 1, idempotency_key: randomUUID() };
    const interview_id = randomUUID(), offer_id = randomUUID();
    const commands: RecruitmentAction[] = [
      { ...identity, action: "view" }, { ...identity, action: "accept" },
      { ...identity, action: "decline", reason: "Salary" }, { ...identity, action: "withdraw", reason: "Closed" },
      { ...identity, action: "reject", reason: "The candidate needs more production experience with distributed services and stronger technical skills in API design before this role would be suitable.", categories: ["technical_skills"] },
      { ...identity, action: "interview_propose", kind: "technical", starts_at: "2026-10-07T10:00:00Z",
        time_zone: "Europe/Moscow", duration_minutes: 60, format: "remote", location: "Meeting link" },
      { ...identity, action: "interview_accept", interview_id },
      { ...identity, action: "interview_decline", interview_id, reason: "Unavailable" },
      { ...identity, action: "interview_cancel", interview_id, reason: "Changed schedule" },
      { ...identity, action: "interview_complete", interview_id, outcome: "passed", feedback: "Passed all tasks" },
      { ...identity, action: "offer_send", salary_rub: 200000, description: "Backend engineer", expires_at: "2026-10-10T12:00:00Z" },
      { ...identity, action: "offer_accept", offer_id },
      { ...identity, action: "offer_decline", offer_id, reason: "Accepted another offer" },
    ];
    for (const command of commands) {
      const valid = await app.inject({ method: "POST", url: "/actions", payload: command });
      assert.equal(valid.statusCode, 200, `${command.action}: ${valid.body}`);
      const invalid = await app.inject({ method: "POST", url: "/actions", payload: { ...command, owner_id: randomUUID() } });
      assert.equal(invalid.statusCode, 400, command.action);
    }
    const invitation = { candidate_id: randomUUID(), idempotency_key: randomUUID(), company_name: "Comit", description: "Position" };
    assert.equal((await app.inject({ method: "POST", url: "/invitations", payload: invitation })).statusCode, 200);
    assert.equal((await app.inject({ method: "POST", url: "/invitations", payload: { ...invitation, salary: { from_rub: 1, to_rub: 2 } } })).statusCode, 200);
    for (const payload of [
      { ...identity, action: "decline", reason: "   " },
      { ...identity, action: "reject", reason: "Too short", categories: ["experience"] },
      { ...identity, action: "reject", reason: "The candidate needs more production experience with distributed services and stronger technical skills in API design before this role would be suitable." },
      { ...identity, action: "offer_accept", offer_id: "invalid" },
      { ...identity, action: "view", expected_revision: 0 },
      { ...identity, action: "view", expected_revision: "1" },
      { ...identity, action: "offer_send", salary_rub: -1, description: "Position", expires_at: "2026-10-10T12:00:00Z" },
      { ...identity, action: "offer_send", salary_rub: 0, description: "Position", expires_at: "2026-10-10T12:00:00Z" },
      { ...identity, action: "offer_send", salary_rub: 1, description: "Position", expires_at: "tomorrow" },
      { ...commands[5], duration_minutes: 0 },
      { ...commands[5], duration_minutes: 481 },
      { ...identity, action: "interview_complete", interview_id, outcome: "passed", feedback: "" },
      { ...identity, action: "interview_accept" },
    ]) assert.equal((await app.inject({ method: "POST", url: "/actions", payload })).statusCode, 400);
    for (const payload of [{ ...invitation, company_name: " " }, { ...invitation, candidate_id: "invalid" },
      { ...invitation, salary: { from_rub: 1, to_rub: 2, currency: "USD" } },
      { ...invitation, contacts: { email: "candidate@example.test" } },
    ]) assert.equal((await app.inject({ method: "POST", url: "/invitations", payload })).statusCode, 400);
    const rejection = { candidate_id: randomUUID(), idempotency_key: randomUUID(), reason: "The candidate needs more production experience with distributed services and stronger technical skills in API design before this role would be suitable.", categories: ["experience"] };
    assert.equal((await app.inject({ method: "POST", url: "/reject-saved", payload: rejection })).statusCode, 200);
    for (const payload of [{ ...rejection, reason: " " }, { ...rejection, company_name: "Comit" },
      { candidate_id: rejection.candidate_id, idempotency_key: rejection.idempotency_key },
    ]) assert.equal((await app.inject({ method: "POST", url: "/reject-saved", payload })).statusCode, 400);
  } finally { await app.close(); }
});

test("employer-only rejection permits null invitation terms and requires structured closure", async () => {
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  app.post("/snapshot", { schema: { body: recruitmentViewSchema } }, async () => ({ ok: true }));
  try {
    const at = "2026-10-06T12:00:00Z";
    const view = { id: randomUUID(), owner_id: randomUUID(), candidate_id: randomUUID(), vacancy_id: randomUUID(),
      draft_id: randomUUID(), profile_revision: 1, revision: 1, state: "rejected", terms: null, invited_at: null,
      closure: { reason: "Required experience missing", actor: "employer", at },
      interviews: [], offers: [], allowed_actions: [], chat_available: false, chat_writable: false, created_at: at, updated_at: at };
    assert.equal((await app.inject({ method: "POST", url: "/snapshot", payload: view })).statusCode, 200);
    for (const payload of [{ ...view, closure: { ...view.closure, actor: "admin" } },
      { ...view, closure: { ...view.closure, email: "candidate@example.test" } },
      { ...view, invited_at: "yesterday" }, { ...view, email: "candidate@example.test" },
    ]) assert.equal((await app.inject({ method: "POST", url: "/snapshot", payload })).statusCode, 400);
  } finally { await app.close(); }
});

test("process and event response contracts exclude contact channels", () => {
  assert.equal(recruitmentViewSchema.additionalProperties, false);
  assert.equal("email" in recruitmentViewSchema.properties, false);
  assert.equal("phone" in recruitmentViewSchema.properties, false);
  assert.equal(recruitmentEventPageSchema.additionalProperties, false);
});
