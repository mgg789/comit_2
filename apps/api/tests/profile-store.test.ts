/** Source-profile CAS, evidence and transactional outbox checks. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { Pool } from "pg";
import { CandidateProfileStore, ProfileError } from "../src/profiles/store.js";
import { canonicalJson } from "../src/profiles/projection.js";

const databaseUrl = process.env.PROFILE_TEST_DATABASE_URL;
const candidateId = "00000000-0000-4000-8000-000000000001";

test("canonical evidence JSON sorts object keys but keeps array order", () => {
  assert.equal(canonicalJson({ b: 2, a: 1 }), '{"a":1,"b":2}');
  assert.equal(canonicalJson(["b", "a"]), '["b","a"]');
});

test("candidate source profile CAS preserves unchanged evidence and queues exact snapshots", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    await pool.query(await readFile(new URL("../migrations/001_accounts.sql", import.meta.url), "utf8"));
    await pool.query(await readFile(new URL("../migrations/002_candidate_profiles.sql", import.meta.url), "utf8"));
    await pool.query("TRUNCATE candidate_profile_outbox, candidate_profile_evidence, candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "candidate-profile@example.test"]);
    const store = new CandidateProfileStore(pool);
    const first = await store.save(candidateId, {
      expected_revision: 0,
      fields: { city: "Москва", domains: [{ key: "python", level: 4 }] },
    });
    assert.equal(first.revision, 1);
    assert.equal(first.projection.state, "queued");
    await pool.query(
      `UPDATE candidate_profile_evidence SET status='confirmed', verified_value='4'::jsonb, provenance='["test"]'::jsonb
        WHERE candidate_id=$1 AND evidence_key='domains:python'`, [candidateId],
    );
    const second = await store.save(candidateId, {
      expected_revision: 1,
      fields: { city: "Санкт-Петербург" },
      consent: { processing: true, pool: true, version: "profile-v1" },
    });
    assert.equal(second.revision, 2);
    assert.equal(second.evidence["domains:python"]?.status, "confirmed");
    assert.equal(second.projection.source_revision, 2);
    assert.equal((await pool.query("SELECT count(*) FROM candidate_profile_outbox WHERE candidate_id=$1 AND state='obsolete'", [candidateId])).rows[0]?.count, "1");
    const payload = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=2", [candidateId])).rows[0]?.payload;
    assert.equal(payload.source_revision, 2);
    assert.equal(payload.email, "candidate-profile@example.test");
    assert.equal(payload.email_verified, true);
    assert.equal(payload.domains[0].status, "confirmed");
    const lease = await store.claimProjection();
    assert.ok(lease);
    await store.retry(candidateId);
    assert.equal((await store.get(candidateId))?.projection.state, "processing");
    assert.equal(await store.acknowledgeProjection(lease.job_id, lease.lease_token, candidateId, 2, 10), true);
    await store.retry(candidateId);
    assert.equal((await store.get(candidateId))?.projection.state, "delivered");
    await assert.rejects(() => store.save(candidateId, { expected_revision: 1, fields: { city: "Казань" } }),
      (error: unknown) => error instanceof ProfileError && error.status === 409);
    await assert.rejects(() => store.save(candidateId, { expected_revision: 2, fields: { salary: { from_rub: 10, to_rub: 1 } } }),
      (error: unknown) => error instanceof ProfileError && error.code === "invalid_salary");
    const third = await store.save(candidateId, { expected_revision: 2, fields: {
      domains: [{ key: "python", level: 5 }], declared_grade: "middle", primary_direction: "backend",
      projects: [{ title: "Synthetic project", facts: { duration_months: 12, role: "developer" } }],
    } });
    assert.equal(third.evidence["domains:python"]?.status, "self_reported");
    assert.equal(third.evidence["domains:python"]?.verified_value, null);
    assert.deepEqual(third.evidence["domains:python"]?.provenance, []);
    const projectId = third.fields.projects?.[0]?.id;
    assert.ok(projectId);
    const nextLease = await store.claimProjection();
    assert.ok(nextLease);
    const snapshot = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=3", [candidateId])).rows[0]?.payload;
    assert.equal(snapshot.grade_result.status, "self_reported");
    assert.equal(snapshot.grade_result.test_session_id, null);
    const fourth = await store.save(candidateId, { expected_revision: 3, fields: {
      projects: [{ id: projectId, title: "Revised project", facts: { duration_months: 12, role: "developer" } }],
    } });
    assert.equal(fourth.fields.projects?.[0]?.id, projectId);
    assert.equal(await store.acknowledgeProjection(nextLease.job_id, nextLease.lease_token, candidateId, 3, 11), false);
    await assert.rejects(() => store.save(candidateId, { expected_revision: 4, fields: {
      projects: [{ id: "00000000-0000-4000-8000-000000000099", title: "Foreign narrative" }],
    } }), (error: unknown) => error instanceof ProfileError && error.code === "unknown_narrative_id");
  } finally {
    await pool.end();
  }
});
