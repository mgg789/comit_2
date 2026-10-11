/** Regression coverage for legacy clocks and read-only degradation on an isolated PostgreSQL instance. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import type { AgentsDraftView } from "@comit/shared";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore, type InvitationGrant } from "../src/recruitment/store.js";
import { VacancyLifecycle } from "../src/recruitment/vacancy-lifecycle.js";

const databaseUrl = process.env.RECRUITMENT_DEADLINES_TEST_DATABASE_URL;
if (databaseUrl && (process.env.COMIT_DISPOSABLE_DATABASE_TESTS !== "1" ||
    !/^\/hiring_test_[0-9]+_[a-f0-9]+$/.test(new URL(databaseUrl).pathname))) throw new Error("isolated_database_required");

test("legacy cutover, authority degradation and closed-vacancy exits", { skip: !databaseUrl }, async t => {
  const pool = new Pool({ connectionString: databaseUrl });
  const profiles = new ProfileStore(pool), store = new RecruitmentStore(pool, true);
  const fixture = async () => {
    const owner = randomUUID(), candidate = randomUUID();
    for (const [id, role] of [[owner, "employer"], [candidate, "candidate"]])
      await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)", [id, `${id}@example.invalid`, role]);
    await profiles.save(candidate, { expected_revision: 0, fields: {}, consent: { processing: true, pool: true, version: "profile-v1" } });
    await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=$1", [candidate]);
    const grant: InvitationGrant = { draft_id: randomUUID(), vacancy_id: randomUUID(), candidate_id: candidate, profile_revision: 11,
      terms: { company_name: "Synthetic authority test", description: "Invitation", salary: { from_rub: 100000, to_rub: 200000 },
        contact_method: "platform", vacancy_title: "Backend", city: "Москва", daily: "5/2", work_type: "remote" } };
    const invitation = await store.invite(owner, { candidate_id: candidate, idempotency_key: randomUUID(), description: "Invitation" }, grant);
    const view = await store.act("candidate", candidate, invitation.id, { action: "accept", expected_revision: invitation.revision, idempotency_key: randomUUID() });
    return { owner, candidate, view, grant };
  };
  try {
    await migrate(pool);
    await t.test("NULL deadline grants a fresh full period instead of expiring legacy updated_at", async () => {
      const f = await fixture();
      await pool.query("UPDATE recruitment_processes SET deadline=NULL,updated_at=now()-interval '1 year' WHERE id=$1", [f.view.id]);
      const view = await store.get("candidate", f.candidate, f.view.id);
      assert.equal(view.state, "accepted"); assert.equal(view.deadline?.stage, "initial");
      assert.ok(Date.parse(view.deadline!.due_at!) > Date.now());
      assert.equal(view.deadline?.business_days, 4);
    });
    await t.test("legacy proposed and completed meetings restore stage without retroactive penalties", async () => {
      for (const state of ["proposed", "completed"] as const) {
        const f = await fixture();
        await pool.query(`INSERT INTO recruitment_interviews(id,process_id,kind,state,starts_at,time_zone,duration_minutes,format,location,completed_at,outcome,feedback)
          VALUES($1,$2,'technical',$3,now()-interval '1 month','Europe/Moscow',30,'remote','https://example.invalid/meeting',CASE WHEN $3='completed' THEN now() ELSE NULL END,CASE WHEN $3='completed' THEN 'passed' ELSE NULL END,CASE WHEN $3='completed' THEN 'Technical discussion completed successfully' ELSE NULL END)`, [randomUUID(), f.view.id, state]);
        await pool.query("UPDATE recruitment_processes SET deadline=NULL,updated_at=now()-interval '1 year' WHERE id=$1", [f.view.id]);
        const view = await store.get("candidate", f.candidate, f.view.id);
        assert.equal(view.state, "accepted");
        assert.equal(view.deadline?.stage, state === "proposed" ? "conversation" : "after_interview");
        assert.equal(view.deadline?.business_days, state === "proposed" ? 7 : 3);
        assert.ok(Date.parse(view.deadline!.due_at!) > Date.now());
      }
    });
    await t.test("legacy contact evidence gets seven days and a future agreed meeting keeps its pause", async () => {
      const f = await fixture();
      await pool.query("UPDATE recruitment_processes SET deadline=NULL,employer_contacted_at=now()-interval '1 month' WHERE id=$1", [f.view.id]);
      const conversation = await store.get("candidate", f.candidate, f.view.id);
      assert.equal(conversation.deadline?.stage, "conversation"); assert.equal(conversation.deadline?.business_days, 7);
      await pool.query(`INSERT INTO recruitment_interviews(id,process_id,kind,state,starts_at,time_zone,duration_minutes,format,location)
        VALUES($1,$2,'technical','scheduled',now()+interval '2 days','Europe/Moscow',30,'remote','https://example.invalid/meeting')`, [randomUUID(), f.view.id]);
      await pool.query("UPDATE recruitment_processes SET deadline=NULL WHERE id=$1", [f.view.id]);
      const scheduled = await store.get("candidate", f.candidate, f.view.id);
      assert.equal(scheduled.deadline?.stage, "interview_wait"); assert.equal(scheduled.deadline?.due_at, null);
    });
    await t.test("a closure racing the outside-transaction probe fences its stale open result", async () => {
      const f = await fixture();
      const raced = new RecruitmentStore(pool, true, "test", async () => {
        await pool.query("INSERT INTO vacancy_lifecycle_commands(draft_id,owner_id,expected_revision,action,phase,result) VALUES($1,$2,1,'close','completed','{}')", [f.grant.draft_id, f.owner]);
        return true;
      });
      const view = await raced.get("candidate", f.candidate, f.view.id);
      assert.equal(view.chat_writable, false); assert.equal(view.deadline, null);
      assert.deepEqual(view.allowed_actions, ["withdraw"]);
    });
    await t.test("a list probes one unique vacancy outside transactions and survives an agents outage", async () => {
      const f = await fixture();
      for (let i = 0; i < 4; i++) await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at)
        SELECT $2,owner_id,candidate_id,$3,draft_id,profile_revision,'sent',terms,now() FROM recruitment_processes WHERE id=$1`, [f.view.id, randomUUID(), randomUUID()]);
      let calls = 0;
      const down = new RecruitmentStore(pool, true, "test", async (_owner, _draft, client) => {
        calls++; assert.equal(client, pool); throw new Error("agents unavailable");
      });
      const page = await down.list("employer", f.owner);
      assert.equal(page.items.length, 5); assert.equal(calls, 1);
      assert.ok(page.items.every(item => !item.chat_writable && item.allowed_actions.includes("reject")));
      const persisted = (await pool.query("SELECT deadline_suspended FROM recruitment_processes WHERE id=$1", [f.view.id])).rows[0];
      assert.equal(persisted.deadline_suspended, false);
      const strict = new RecruitmentStore(pool, true, "test", async () => { throw new Error("agents unavailable"); });
      await assert.rejects(strict.act("employer", f.owner, f.view.id, { action: "offer_send", salary_rub: 150000, description: "Offer",
        expires_at: new Date(Date.now() + 86400000).toISOString(), expected_revision: f.view.revision, idempotency_key: randomUUID() }));
      const exit = await strict.act("candidate", f.candidate, f.view.id, { action: "withdraw", reason: "No longer looking",
        expected_revision: f.view.revision, idempotency_key: randomUUID() });
      assert.equal(exit.state, "withdrawn");
    });
    await t.test("candidate can decline an outstanding offer after vacancy closure", async () => {
      const f = await fixture();
      const offered = await store.act("employer", f.owner, f.view.id, { action: "offer_send", salary_rub: 150000, description: "Offer",
        expires_at: new Date(Date.now() + 86400000).toISOString(), expected_revision: f.view.revision, idempotency_key: randomUUID() });
      const closed = new RecruitmentStore(pool, true, "test", async () => false);
      const view = await closed.get("candidate", f.candidate, offered.id);
      assert.ok(view.allowed_actions.includes("offer_decline")); assert.ok(!view.allowed_actions.includes("offer_accept"));
      const result = await closed.act("candidate", f.candidate, offered.id, { action: "offer_decline", offer_id: offered.offers[0]!.id,
        reason: "Vacancy is closed", expected_revision: view.revision, idempotency_key: randomUUID() });
      assert.equal(result.state, "offer_declined");
    });
    await t.test("editorial draft keeps the accepted publication live, but republishing after withdrawal remains closed", async () => {
      const f = await fixture();
      const priorToken = process.env.AGENTS_SERVICE_TOKEN;
      process.env.AGENTS_SERVICE_TOKEN = "synthetic-authority-token";
      try {
        const draft: AgentsDraftView = { draft_id: f.grant.draft_id, vacancy_id: f.grant.vacancy_id, owner_id: f.owner,
          revision: 3, state: "editing", fields: {}, business_status: "open", published_at: new Date(Date.now() - 86400000).toISOString(), publication: null };
        const lifecycle = new VacancyLifecycle(pool, async () => Response.json(draft));
        const active = new RecruitmentStore(pool, true, "test", (owner, id, client) => lifecycle.writable(owner, id, client));
        assert.equal(await lifecycle.writable(f.owner, draft.draft_id, pool), true);
        assert.equal(await active.withChatGrant("employer", f.owner, f.view.id, true, async () => "delivered"), "delivered");
        await pool.query("INSERT INTO vacancy_lifecycle_commands(draft_id,owner_id,expected_revision,action,phase,result) VALUES($1,$2,1,'withdraw','completed',$3)", [draft.draft_id, f.owner, draft]);
        draft.state = "creating"; draft.publication = { status: "pending", source_revision: 3 };
        assert.equal(await lifecycle.writable(f.owner, draft.draft_id, pool), false);
        draft.publication = { status: "accepted", source_revision: 3 };
        assert.equal(await lifecycle.writable(f.owner, draft.draft_id, pool), true);
      } finally { if (priorToken === undefined) delete process.env.AGENTS_SERVICE_TOKEN; else process.env.AGENTS_SERVICE_TOKEN = priorToken; }
    });
  } finally { await pool.end(); }
});
