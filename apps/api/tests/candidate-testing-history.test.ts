/** Real PostgreSQL producer, owner isolation and Moscow calendar boundary regression. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore } from "../src/auth/store.js";
import { hashSessionId } from "../src/auth/crypto.js";
import { signSession } from "../src/auth/session.js";
import { ProfileStore } from "../src/profiles/store.js";
import { CandidateTestingHistoryStore } from "../src/profiles/testing-history.js";
import { candidateTestingHistoryRoutes } from "../src/routes/candidate-testing-history.js";
import { testingIntegrationRoutes } from "../src/routes/testing-integration.js";

const databaseUrl = process.env.TESTING_HISTORY_TEST_DATABASE_URL;
test("trusted results are idempotent, private and bucketed over 30 Moscow days", { skip: !databaseUrl }, async () => {
  const schema = `testing_history_${randomUUID().replaceAll("-", "")}`;
  const admin = new Pool({ connectionString: databaseUrl });
  const pool = new Pool({ connectionString: databaseUrl, options: `-c search_path=${schema}` });
  const app = Fastify();
  try {
    await admin.query(`CREATE SCHEMA ${schema}`);
    for (const name of ["001_accounts", "002_candidate_profiles", "005_training_consent", "019_testing_grades"]) {
      await pool.query(await readFile(new URL(`../migrations/${name}.sql`, import.meta.url), "utf8"));
    }
    const first = randomUUID(), second = randomUUID(), employer = randomUUID();
    for (const [id, role] of [[first,"candidate"],[second,"candidate"],[employer,"employer"]]) {
      await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
        VALUES ($1,$2,$3,true,'account-v1',now())`, [id,`${id}@example.test`,role]);
    }
    const profiles = new ProfileStore(pool);
    await profiles.save(first!, { expected_revision: 0, fields: {} });
    await profiles.save(second!, { expected_revision: 0, fields: {} });
    const config = loadAuthConfig({ AUTH_SECRET: "synthetic-testing-history-secret-32-bytes", AUTH_ALLOWED_ORIGINS: "http://localhost", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, config.secret);
    await app.register(cookie);
    const history = new CandidateTestingHistoryStore(pool);
    await app.register(candidateTestingHistoryRoutes, { config, store, mailConfigured: false, testingHistory: history });
    await app.register(testingIntegrationRoutes, { profiles, token: "synthetic-testing-token" });
    const token = async (id: string, role: "candidate" | "employer") => {
      const sid = Buffer.from(randomUUID()+randomUUID()).toString("base64url").slice(0,43);
      const expires = new Date(Date.now()+60_000).toISOString();
      await pool.query("INSERT INTO sessions(id,account_id,role,session_hash,expires_at) VALUES($1,$2,$3,$4,$5)",
        [randomUUID(),id,role,hashSessionId(sid),expires]);
      return signSession(config,role,id,sid,expires);
    };
    const headers = { authorization: `Bearer ${await token(first!,"candidate")}` };
    assert.equal((await app.inject({ url: "/candidate/testing-history" })).statusCode,401);
    assert.equal((await app.inject({ url: "/candidate/testing-history", headers: {
      authorization: `Bearer ${await token(employer!,"employer")}` } })).statusCode,401);
    const payload = { candidate_id:first,session_id:"accepted-test",specialization:"backend",decided_grade:"middle",tasks_answered:12 };
    assert.equal((await app.inject({ method:"POST",url:"/internal/testing/session-result",payload })).statusCode,401);
    const publish = (body = payload) => app.inject({ method:"POST",url:"/internal/testing/session-result",
      headers:{"x-internal-token":"synthetic-testing-token"},payload:body });
    assert.equal((await app.inject({ method:"POST",url:"/internal/testing/session-result",
      headers:{"x-internal-token":"synthetic-testing-token"},payload:{ ...payload,tasks_answered:0 } })).statusCode,400);
    assert.equal((await publish()).statusCode,200);
    const acceptedRevision = (await profiles.get(first!))?.revision;
    assert.equal((await publish()).statusCode,200);
    assert.equal((await profiles.get(first!))?.revision,acceptedRevision);
    assert.equal((await profiles.get(first!))?.fields.declared_grade,"middle");
    assert.equal((await publish({ ...payload,candidate_id:second })).statusCode,409);
    assert.equal((await pool.query("SELECT count(*)::integer AS count FROM candidate_grade_results")).rows[0].count,1);
    assert.equal((await app.inject({ url:"/candidate/testing-history",headers })).json().total,1);
    await pool.query("DELETE FROM candidate_grade_results");
    const now = new Date("2026-10-10T21:05:00Z"); // Moscow Oct 11, first day Sep 12.
    for (const [id,at] of [[first,"2026-09-11T21:00:00Z"],[first,"2026-09-11T20:59:59Z"],
      [first,"2026-10-10T21:00:00Z"],[first,"2026-10-10T21:06:00Z"],[second,"2026-10-10T21:00:00Z"]]) {
      await pool.query(`INSERT INTO candidate_grade_results(id,candidate_id,session_id,specialization,grade,tasks_answered,decided_at)
        VALUES($1,$2,$3,'backend','middle',12,$4)`,[randomUUID(),id,randomUUID(),at]);
    }
    const calendar = await history.get(first!,now);
    assert.equal(calendar.daily.length,30); assert.equal(calendar.period.from,"2026-09-12");
    assert.equal(calendar.period.to,"2026-10-11"); assert.equal(calendar.total,2);
    assert.equal(calendar.daily[0]?.count,1); assert.equal(calendar.daily[29]?.count,1);
    const empty = await history.get(employer!,now); assert.equal(empty.total,0);
    assert.ok(empty.daily.every(day => day.count === 0));
  } finally {
    await app.close(); await pool.end();
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`); await admin.end();
  }
});
