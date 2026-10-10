/** SQL keysets preserve microsecond ordering and authenticated actor/filter scope. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { RecruitmentStore } from "../src/recruitment/store.js";

const databaseUrl = process.env.RECRUITMENT_CURSOR_TEST_DATABASE_URL;
test("recruitment cursor is stable through edits/new insertions and exact across PostgreSQL microseconds", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const secret = "synthetic-only-shared-keyset-secret";
  const store = new RecruitmentStore(pool, false, secret), secondInstance = new RecruitmentStore(pool, false, secret);
  try {
    await migrate(pool); await pool.query("TRUNCATE accounts CASCADE");
    const owner = randomUUID(), candidate = randomUUID(), foreign = randomUUID(), draft = randomUUID();
    const terms = { company_name: "Synthetic", description: "Invitation", salary: { from_rub: 100, to_rub: 200 }, contact_method: "platform",
      vacancy_title: "Backend", city: "Москва", daily: "5/2", work_type: "remote" };
    for (const [id, role] of [[owner, "employer"], [candidate, "candidate"], [foreign, "employer"]]) {
      await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)", [id, `${id}@example.test`, role]);
    }
    const ids: string[] = [];
    for (const microseconds of [123400, 123300, 123200, 123100]) {
      const id = randomUUID(); ids.push(id);
      await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,created_at,terms)
        VALUES($1,$2,$3,$4,$5,1,'sent',now(),$6::timestamptz,$7)`, [id, owner, candidate, randomUUID(), draft, `2026-10-07T00:00:00.${microseconds}Z`, terms]);
    }
    const first = await store.list("employer", owner, 2, 0, draft);
    assert.deepEqual(first.items.map(item => item.id), ids.slice(0, 2));
    assert.ok(first.next_cursor);
    await pool.query("UPDATE recruitment_processes SET updated_at=now() WHERE id=$1", [ids[3]]);
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,terms)
      VALUES($1,$2,$3,$4,$5,1,'sent',now(),$6)`, [randomUUID(), owner, candidate, randomUUID(), draft, terms]);
    const next = await secondInstance.list("employer", owner, 2, 0, draft, first.next_cursor);
    assert.deepEqual(next.items.map(item => item.id), ids.slice(2));
    assert.equal(next.next_cursor, null);
    for (const operation of [
      () => store.list("employer", foreign, 2, 0, draft, first.next_cursor!),
      () => store.list("candidate", candidate, 2, 0, draft, first.next_cursor!),
      () => store.list("employer", owner, 2, 0, undefined, first.next_cursor!),
      () => store.list("employer", owner, 2, 1, draft, first.next_cursor!),
      () => store.list("employer", owner, 2, 0, draft, `${first.next_cursor!.slice(0, -1)}${first.next_cursor!.endsWith("f") ? "e" : "f"}`),
    ]) await assert.rejects(operation(), { status: 409 });
    assert.equal((await store.list("candidate", candidate, 2)).items.length, 2);
    assert.equal((await store.list("employer", owner, 2, 2, draft)).items.length, 2);
  } finally { await pool.end(); }
});
