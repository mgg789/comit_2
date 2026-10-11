/** Real PostgreSQL employer graph deletion and pending login role isolation. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import { AuthStore } from "../src/auth/store.js";
import { migrate } from "../src/auth/migrate.js";
const databaseUrl = process.env.EMPLOYER_DELETE_TEST_DATABASE_URL;
test("employer deletion removes API graph and preserves candidate and other-role challenges", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    await migrate(pool);
    const store = new AuthStore(pool, "synthetic-store-secret-with-enough-length");
    const owner = randomUUID(), candidate = randomUUID(), process = randomUUID();
    const email = `employer-delete-${owner}@example.test`;
    await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
      VALUES ($1,$3,'employer',true,'account-v1',now()),($2,'candidate-' || $3,'candidate',true,'account-v1',now())`, [owner,candidate,email]);
    await pool.query(`INSERT INTO sessions(id,account_id,role,session_hash,expires_at)
      VALUES ($1,$2,'employer',$3,now()+interval '1 day')`, [randomUUID(),owner,Buffer.from("employer-delete-session")]);
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state)
      VALUES ($1,$2,$3,$4,$5,1,'rejected')`, [process,owner,candidate,randomUUID(),randomUUID()]);
    await pool.query(`INSERT INTO recruitment_receipts(actor_id,idempotency_key,fingerprint,result) VALUES ($1,$2,repeat('a',64),'{}')`, [owner,randomUUID()]);
    await pool.query(`INSERT INTO vacancy_lifecycle_commands(draft_id,owner_id,expected_revision,action,result) VALUES ($1,$2,1,'delete','{}')`, [randomUUID(),owner]);
    await pool.query(`INSERT INTO employer_expenses(source_key,owner_id,category,amount_kopeks,occurred_at,policy,reason)
      VALUES ($1,$2,'ai',1,now(),'{}','synthetic')`, [`synthetic-${owner}`,owner]);
    await pool.query(`INSERT INTO employer_expense_ai_days(owner_id,moscow_date) VALUES ($1,current_date)`, [owner]);
    for (const role of ["candidate","employer"]) await pool.query(`INSERT INTO auth_challenges(id,email,role,ip_hmac,expires_at)
      VALUES ($1,$2,$3,$4,now()+interval '1 hour')`, [randomUUID(),email,role,Buffer.from("synthetic-ip")]);
    assert.equal(await store.deleteEmployerAccount(owner), true);
    for (const [table,column] of [["accounts","id"],["sessions","account_id"],["recruitment_processes","owner_id"],["recruitment_receipts","actor_id"],
      ["vacancy_lifecycle_commands","owner_id"],["employer_expenses","owner_id"],["employer_expense_ai_days","owner_id"]]) {
      assert.equal((await pool.query(`SELECT 1 FROM ${table} WHERE ${column}=$1`, [owner])).rowCount, 0, table);
    }
    assert.equal((await pool.query("SELECT 1 FROM accounts WHERE id=$1", [candidate])).rowCount, 1);
    assert.equal((await pool.query("SELECT 1 FROM auth_challenges WHERE email=$1 AND role='candidate'", [email])).rowCount, 1);
    assert.equal((await pool.query("SELECT 1 FROM auth_challenges WHERE email=$1 AND role='employer'", [email])).rowCount, 0);
    assert.equal(await store.deleteEmployerAccount(candidate), false);
    assert.equal(await store.deleteEmployerAccount(owner), false);
  } finally { await pool.end(); }
});
