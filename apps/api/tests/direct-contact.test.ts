/** Direct contact uses real PostgreSQL ownership and the authoritative expense ledger. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore, RecruitmentError } from "../src/recruitment/store.js";
import { directContactBodySchema } from "../src/recruitment/schemas.js";

const url = process.env.DIRECT_CONTACT_TEST_DATABASE_URL;
test("contact transport requires explicit salary and description and rejects forged identities", async () => {
  const app = Fastify({ajv: {customOptions: {removeAdditional: false, coerceTypes: false}}});
  app.post("/contact", {schema: {body: directContactBodySchema}}, async () => ({ok: true}));
  const body = {candidate_id: randomUUID(), idempotency_key: randomUUID(), profile_revision: 11, description: "Discuss backend opportunities", salary: {from_rub: 100000, to_rub: 200000}};
  assert.equal((await app.inject({method: "POST", url: "/contact", payload: body})).statusCode, 200);
  for (const invalid of [{...body, salary: undefined}, {...body, description: " "}, {...body, owner_id: randomUUID()}]) assert.equal((await app.inject({method: "POST", url: "/contact", payload: invalid})).statusCode, 400);
  await app.close();
});

test("direct chat bills once, never reveals contacts or enables hiring and both roles can close", {skip: !url}, async () => {
  const pool = new Pool({connectionString: url});
  try {
    await migrate(pool);
    const store = new RecruitmentStore(pool, true, undefined, async () => {throw new Error("No vacancy source exists");});
    const profiles = new ProfileStore(pool);
    for (const closer of ["candidate", "employer"] as const) {
      const owner = randomUUID(), candidate = randomUUID(), foreign = randomUUID();
      for (const [id, role] of [[owner, "employer"], [candidate, "candidate"], [foreign, "employer"]]) await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)", [id, `${id}@example.test`, role]);
      await profiles.save(candidate, {expected_revision: 0, fields: {display_name: "Synthetic candidate"}, consent: {processing: true, pool: true, version: "profile-v1"}});
      await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=$1", [candidate]);
      const request = {candidate_id: candidate, idempotency_key: randomUUID(), profile_revision: 11, description: "Discuss backend opportunities", salary: {from_rub: 100000, to_rub: 200000}};
      const view = await store.startContact(owner, request, "Synthetic company");
      assert.equal(view.kind, "contact_only");
      assert.equal(view.chat_writable, true);
      assert.equal(view.deadline, null);
      assert.deepEqual(view.allowed_actions, ["contact_close"]);
      assert.deepEqual(await store.startContact(owner, request, "Synthetic company"), view);
      assert.deepEqual((await store.get("candidate", candidate, view.id)).allowed_actions, ["contact_close"]);
      assert.equal((await store.list("candidate", candidate)).items[0]?.kind, "contact_only");
      await assert.rejects(() => store.get("employer", foreign, view.id), error => error instanceof RecruitmentError && error.status === 404);
      await assert.rejects(() => store.contacts(owner, view.id), error => error instanceof RecruitmentError && error.status === 403);
      await assert.rejects(() => store.act("candidate", candidate, view.id, {action: "accept", expected_revision: view.revision, idempotency_key: randomUUID()}), error => error instanceof RecruitmentError && error.code === "recruitment_action_unavailable");
      await assert.rejects(() => store.act("employer", owner, view.id, {action: "offer_send", expected_revision: view.revision, idempotency_key: randomUUID(), salary_rub: 100000, description: "Offer", expires_at: new Date(Date.now()+86400000).toISOString()}), error => error instanceof RecruitmentError && error.code === "recruitment_action_unavailable");
      await assert.rejects(() => store.act("employer", owner, view.id, {action: "interview_propose", expected_revision: view.revision, idempotency_key: randomUUID(), kind: "technical", starts_at: new Date(Date.now()+86400000).toISOString(), time_zone: "Europe/Moscow", duration_minutes: 60, format: "remote", location: "https://example.test/meeting"}), error => error instanceof RecruitmentError && error.code === "recruitment_action_unavailable");
      assert.equal(await store.withChatGrant("employer", owner, view.id, true, async () => "delivered", true), "delivered");
      assert.equal(await store.withChatGrant("candidate", candidate, view.id, true, async () => "delivered"), "delivered");
      const closed = await store.act(closer, closer === "candidate" ? candidate : owner, view.id, {action: "contact_close", expected_revision: view.revision, idempotency_key: randomUUID(), reason: "Conversation finished"});
      assert.equal(closed.chat_writable, false);
      assert.equal(closed.chat_available, true);
      assert.equal(closed.closure?.actor, closer);
      assert.deepEqual(closed.allowed_actions, []);
      await assert.rejects(() => store.withChatGrant("candidate", candidate, view.id, true, async () => "forbidden"), error => error instanceof RecruitmentError && error.code === "chat_read_only");
      const charges = await pool.query("SELECT amount_kopeks,vacancy_id,policy FROM employer_expenses WHERE owner_id=$1", [owner]);
      assert.equal(charges.rows.length, 1);
      assert.equal(Number(charges.rows[0].amount_kopeks), 4900);
      assert.equal(charges.rows[0].vacancy_id, null);
      assert.equal(charges.rows[0].policy.total_kopeks, 4900);
      await pool.query("DELETE FROM employer_expenses WHERE owner_id=$1", [owner]);
      await pool.query("DELETE FROM recruitment_receipts WHERE actor_id=ANY($1::uuid[])", [[owner, candidate]]);
      await pool.query("DELETE FROM recruitment_processes WHERE owner_id=$1", [owner]);
      await pool.query("DELETE FROM accounts WHERE id=ANY($1::uuid[])", [[owner,candidate,foreign]]);
    }
  } finally {await pool.end();}
});
