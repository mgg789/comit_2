/** Real shared-schema PostgreSQL projection and employer-session boundary regressions. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { EmployerStatisticsStore, statisticsPeriod } from "../src/statistics/store.js";
import { statisticsRoutes } from "../src/routes/statistics.js";

test("statistics calendar validation is inclusive, UTC, bounded and rejects normalized invalid dates", () => {
  const now = new Date("2026-10-07T12:00:00Z");
  assert.equal(statisticsPeriod({}, now).days, 30);
  assert.deepEqual(statisticsPeriod({ from: "2026-10-07", to: "2026-10-07" }, now), {
    from: "2026-10-07", to: "2026-10-07", start: "2026-10-07T00:00:00.000Z", end: "2026-10-08T00:00:00.000Z",
    previous_from: "2026-10-06", previous_to: "2026-10-06", days: 1,
  });
  for (const query of [{ from: "2026-02-30" }, { to: "2026-10-08" }, { from: "2026-01-01" }, { from: "2026-10-07", to: "2026-10-06" }]) {
    assert.throws(() => statisticsPeriod(query, now), { code: "invalid_statistics_period" });
  }
});

const databaseUrl = process.env.STATISTICS_TEST_DATABASE_URL;
test("statistics use real milestones, deduplicate pairs, fill days and isolate owners including HTTP auth", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false } } });
  try {
    await migrate(pool);
    // Initialize the actual source migrations only on this explicitly isolated test database.
    for (const [table, folder] of [
      ["feed_pipeline.sessions", "../../../services/matching-engine/src/matching_engine/migrations/"],
      ["agents_service.drafts", "../../../services/agents-service/src/agents_service/migrations/"],
    ]) {
      if (!(await pool.query("SELECT to_regclass($1) AS table", [table])).rows[0]?.table) {
        const directory = new URL(folder!, import.meta.url);
        for (const name of (await readdir(directory)).filter(name => name.endsWith(".sql")).sort()) await pool.query(await readFile(new URL(name, directory), "utf8"));
      }
    }
    await pool.query(await readFile(new URL("../../../services/matching-engine/src/matching_engine/migrations/015_statistics_reads.sql", import.meta.url), "utf8"));
    const secret = "synthetic-only-statistics-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39880", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret);
    const login = async (role: "employer" | "candidate") => {
      const email = `${randomUUID()}@example.test`;
      const challenge = await store.issue(role, email, randomUUID());
      const mail = await store.claimMail(); assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const result = await store.verify(role, email, challenge.challenge_id, mail.code, "account-v1");
      return { id: result.account.id, headers: { authorization: `Bearer ${await signSession(config, role, result.account.id, result.session_id, result.expires_at)}` } };
    };
    const owner = await login("employer"), other = await login("employer"), candidate = await login("candidate");
    const vacancy = randomUUID(), foreign = randomUUID(), draft = randomUUID();
    for (const [id, ownerId, draftId] of [[vacancy, owner.id, draft], [foreign, other.id, randomUUID()]]) {
      await pool.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,business_status,fields) VALUES($1,$2,$3,'open',$4)", [draftId, id, ownerId, { title: "Backend engineer" }]);
      await pool.query(`INSERT INTO vacancy_pipeline.vacancies(vacancy_id,owner_id,revision,source_revision,profile_hash,profile,raw_input,business_status,state,missing_requirements)
        VALUES($1,$2,1,1,'fixture','{}','{}','open','draft','[]')`, [id, ownerId]);
    }
    await pool.query(`INSERT INTO candidate_pipeline.candidates(candidate_id,revision,source_revision,profile_hash,profile,state,missing_requirements)
      VALUES($1,1,1,'fixture','{}','draft','[]')`, [candidate.id]);
    const addCard = async (ownerId: string, vacancyId: string, at: string, viewed: boolean) => {
      const session = randomUUID(), exposure = randomUUID();
      await pool.query(`INSERT INTO feed_pipeline.sessions(session_id,owner_id,vacancy_id,vacancy_revision,schema_version,schema_hash,pool_generation,main_policy_hash,policy_hash,feature_schema_hash,tail_policy_hash,expires_at,source)
        VALUES($1,$2,$3,1,1,'fixture',1,'fixture','fixture','fixture','fixture',now()+interval '1 day','showcase')`, [session, ownerId, vacancyId]);
      await pool.query(`INSERT INTO feed_pipeline.exposures(exposure_id,session_id,owner_id,vacancy_id,candidate_id,profile_revision,position,kind,card,served_at)
        VALUES($1,$2,$3,$4,$5,1,1,'showcase','{}',$6)`, [exposure, session, ownerId, vacancyId, candidate.id, at]);
      await pool.query(`INSERT INTO feed_pipeline.decisions(receipt_id,owner_id,vacancy_id,exposure_id,candidate_id,idempotency_key,action,profile_revision,baseline,baseline_origin,frozen_policy,frozen_policy_hash,created_at)
        VALUES($1,$2,$3,$4,$5,$6,'like',1,'{}','exposure','{}','fixture',$7)`, [randomUUID(), ownerId, vacancyId, exposure, candidate.id, randomUUID(), at]);
      if (viewed) await pool.query("INSERT INTO learning_pipeline.events(event_id,exposure_id,owner_id,vacancy_id,event_type,recorded_at) VALUES($1,$2,$3,$4,'impression',$5)", [randomUUID(), exposure, ownerId, vacancyId, at]);
      return exposure;
    };
    await addCard(owner.id, vacancy, "2026-10-06T23:59:59Z", true);
    const exposure = await addCard(owner.id, vacancy, "2026-10-07T00:00:00Z", true);
    await addCard(owner.id, vacancy, "2026-10-07T01:00:00Z", false);
    await addCard(other.id, foreign, "2026-10-07T01:00:00Z", true);
    await pool.query("INSERT INTO feed_pipeline.saved(owner_id,vacancy_id,candidate_id,profile_revision,exposure_id,card) VALUES($1,$2,$3,1,$4,'{}')", [owner.id, vacancy, candidate.id, exposure]);
    const process = randomUUID();
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,invited_at,terms)
      VALUES($1,$2,$3,$4,$5,1,'hired','2026-10-07T02:00:00Z','{}')`, [process, owner.id, candidate.id, vacancy, draft]);
    let revision = 0;
    for (const type of ["invitation.sent", "accept", "interview.complete", "interview.complete", "offer.send", "offer.accept"]) {
      await pool.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state,created_at) VALUES($1,$2,$3,$4,'hired','2026-10-07T03:00:00Z')", [randomUUID(), process, ++revision, type]);
    }
    const statistics = new EmployerStatisticsStore(pool, () => new Date("2026-10-07T12:00:00Z"));
    await app.register(cookie);
    await app.register(statisticsRoutes, { config, store, statistics });
    const url = "/employer/statistics?from=2026-10-07&to=2026-10-07";
    assert.equal((await app.inject({ url })).statusCode, 401);
    assert.equal((await app.inject({ url, headers: candidate.headers })).statusCode, 401);
    const response = await app.inject({ url, headers: owner.headers });
    assert.equal(response.statusCode, 200, response.body);
    const value = response.json();
    assert.deepEqual(value.current, { delivered: 1, viewed: 1, liked: 1, disliked: 0, invited: 1, accepted: 1, interviewed: 1, offered: 1, hired: 1 });
    assert.equal(value.previous.liked, 1);
    assert.equal(value.daily[0].counts.liked, 2);
    assert.equal(value.daily[0].counts.interviewed, 2);
    assert.equal(value.unique_candidates, 1);
    assert.equal(value.inventory.open, 1);
    assert.equal(value.inventory.saved_pairs, 1);
    assert.equal(value.inventory.active_processes, 0);
    assert.equal(value.top_vacancies.length, 1);
    assert.equal(value.top_vacancies[0].vacancy_id, vacancy);
    assert.ok(!response.body.includes(candidate.id));
    assert.ok(!response.body.includes(foreign));
    const detail = await app.inject({ url: `${url}&vacancy_id=${vacancy}`, headers: owner.headers });
    assert.deepEqual(detail.json().current, value.current);
    for (const id of [foreign, randomUUID()]) assert.equal((await app.inject({ url: `${url}&vacancy_id=${id}`, headers: owner.headers })).statusCode, 404);
    for (const suffix of ["&owner_id=" + other.id, "&vacancy_id=bad", "&from=2026-02-30"]) assert.equal((await app.inject({ url: url + suffix, headers: owner.headers })).statusCode, 400);
    const blank = (await app.inject({ url: "/employer/statistics?from=2026-10-01&to=2026-10-07", headers: owner.headers })).json();
    assert.equal(blank.daily.length, 7);
    assert.equal(blank.daily[0].counts.liked, 0);
    const secondVacancy = randomUUID();
    await pool.query("INSERT INTO agents_service.drafts(draft_id,vacancy_id,owner_id,business_status) VALUES($1,$2,$3,'closed')", [randomUUID(), secondVacancy, owner.id]);
    await pool.query(`INSERT INTO vacancy_pipeline.vacancies(vacancy_id,owner_id,revision,source_revision,profile_hash,profile,raw_input,business_status,state,missing_requirements)
      VALUES($1,$2,1,1,'fixture','{}','{}','closed','draft','[]')`, [secondVacancy, owner.id]);
    await addCard(owner.id, secondVacancy, "2026-10-07T01:00:00Z", true);
    const multi = (await app.inject({ url, headers: owner.headers })).json();
    assert.equal(multi.current.liked, 2); // One person, two vacancy/candidate pairs.
    assert.equal(multi.unique_candidates, 1);
    assert.equal(multi.inventory.closed, 1);
    await pool.query("UPDATE agents_service.drafts SET deleted_at=now() WHERE vacancy_id=$1", [secondVacancy]);
    const archived = (await app.inject({ url, headers: owner.headers })).json();
    assert.equal(archived.inventory.archived, 1);
    assert.equal(archived.inventory.closed, 0);
    assert.equal(archived.current.liked, 2); // Historical activity survives archival.
    const emptyOwner = await login("employer");
    const empty = (await app.inject({ url, headers: emptyOwner.headers })).json();
    assert.equal(empty.current.liked, 0);
    assert.equal(empty.unique_candidates, 0);
    assert.deepEqual(empty.top_vacancies, []);
    // Historical exposures outside the period must not require scanning the global feed.
    const sessionId = (await pool.query("SELECT session_id FROM feed_pipeline.exposures WHERE exposure_id=$1", [exposure])).rows[0].session_id;
    await pool.query(`WITH candidates AS (
      INSERT INTO candidate_pipeline.candidates(candidate_id,revision,source_revision,profile_hash,profile,state,missing_requirements)
      SELECT gen_random_uuid(),1,1,'fixture','{}','draft','[]' FROM generate_series(1,10000) RETURNING candidate_id
    ) INSERT INTO feed_pipeline.exposures(exposure_id,session_id,owner_id,vacancy_id,candidate_id,profile_revision,position,kind,card,served_at)
      SELECT gen_random_uuid(),$1,$2,$3,candidate_id,1,row_number() OVER()+100,'showcase','{}','2026-09-01T00:00:00Z' FROM candidates`, [sessionId, owner.id, vacancy]);
    await pool.query("ANALYZE feed_pipeline.exposures");
    const plan = (await pool.query(`EXPLAIN (FORMAT JSON) SELECT candidate_id FROM feed_pipeline.exposures
      WHERE owner_id=$1 AND served_at >= '2026-10-07' AND served_at < '2026-10-08'`, [owner.id])).rows[0]["QUERY PLAN"];
    assert.ok(JSON.stringify(plan).includes("feed_exposures_statistics_time"));
    assert.deepEqual((await app.inject({ url, headers: owner.headers })).json().current, archived.current);
    await pool.query("UPDATE sessions SET revoked_at=now() WHERE account_id=$1", [owner.id]);
    assert.equal((await app.inject({ url, headers: owner.headers })).statusCode, 401);
  } finally { await app.close(); await pool.end(); }
});
