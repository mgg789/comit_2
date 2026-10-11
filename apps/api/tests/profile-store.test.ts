import { fixtureSkillCatalog } from "./helpers/skill-catalog.js";
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
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_profile_outbox, candidate_profile_evidence, candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "candidate-profile@example.test"]);
    const store = new CandidateProfileStore(pool, fixtureSkillCatalog);
    const first = await store.save(candidateId, {
      expected_revision: 0,
      fields: { city: "Москва", domains: [{ key: "sql", level: 4 }] },
    });
    assert.equal(first.revision, 1);
    assert.equal(first.projection.state, "queued");
    await pool.query(
      `UPDATE candidate_profile_evidence SET status='confirmed', verified_value='4'::jsonb, provenance='["test"]'::jsonb
        WHERE candidate_id=$1 AND evidence_key='domains:sql'`, [candidateId],
    );
    const second = await store.save(candidateId, {
      expected_revision: 1,
      fields: { city: "Санкт-Петербург" },
      consent: { processing: true, pool: true, version: "profile-v1" },
    });
    assert.equal(second.revision, 2);
    assert.equal(second.evidence["domains:sql"]?.status, "confirmed");
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
      domains: [{ key: "sql", level: 5 }], declared_grade: "middle", primary_direction: "backend",
      projects: [{ title: "Synthetic project", facts: { duration_months: 12, role: "developer" } }],
    } });
    assert.equal(third.evidence["domains:sql"]?.status, "self_reported");
    assert.equal(third.evidence["domains:sql"]?.verified_value, null);
    assert.deepEqual(third.evidence["domains:sql"]?.provenance, []);
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


test("training consent is optional, versioned, preserved on field edits and independently revocable", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "training-consent@example.test"]);
    const store = new CandidateProfileStore(pool, fixtureSkillCatalog);
    let view = await store.save(candidateId, { expected_revision: 0, fields: {}, consent: { processing: true, pool: true, version: "profile-v1" } });
    assert.equal(view.consents.training, false);
    assert.equal(view.consents.training_accepted_at, null);
    await assert.rejects(pool.query("UPDATE candidate_profiles SET training_consent=true,training_consent_accepted_at=now() WHERE candidate_id=$1", [candidateId]));
    await assert.rejects(() => store.save(candidateId, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true } }),
      (error: unknown) => error instanceof ProfileError && error.code === "invalid_training_consent");
    view = await store.save(candidateId, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true, training_version: "training-v1" } });
    assert.equal(view.consents.training, true);
    assert.equal(view.consents.training_version, "training-v1");
    // Recommendation consent requires a new explicit v2 choice; field edits retain v1.
    const stamp = view.consents.training_accepted_at;
    assert.ok(stamp);
    view = await store.save(candidateId, { expected_revision: 2, fields: { city: "Москва" } });
    assert.equal(view.consents.training_accepted_at, stamp);
    assert.equal(view.consents.training_version, "training-v1");
    view = await store.save(candidateId, { expected_revision: 3, fields: {},
      consent: { processing: true, pool: true, version: "profile-v1", training: true, training_version: "training-v2" } });
    assert.equal(view.consents.training_version, "training-v2");
    assert.ok(view.consents.training_accepted_at);
    const v2stamp = view.consents.training_accepted_at;
    view = await store.save(candidateId, { expected_revision: 4, fields: { city: "Тула" } });
    assert.equal(view.consents.training_version, "training-v2");
    assert.equal(view.consents.training_accepted_at, v2stamp);
    view = await store.save(candidateId, { expected_revision: 5, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: false } });
    assert.equal(view.consents.training, false);
    assert.equal(view.consents.processing, true);
    assert.equal(view.consents.pool, true);
    assert.equal(view.consents.training_version, null);
    const payload = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=6", [candidateId])).rows[0]?.payload;
    assert.equal(payload.consents.training, false);
    assert.equal(payload.consents.training_accepted_at, null);
  } finally {
    await pool.end();
  }
});

test("demo FSP state is candidate-bound, one-time and bulk imports achievements", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "fsp-demo@example.test"]);
    const otherCandidate = "00000000-0000-4000-8000-000000000002";
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [otherCandidate, "fsp-other@example.test"]);
    const store = new CandidateProfileStore(pool, fixtureSkillCatalog);
    const initial = await store.save(candidateId, {
      expected_revision: 0, fields: { achievements: [{ title: "Existing", description: "Keep" }] },
      consent: { processing: true, pool: true, version: "profile-v1" },
    });
    const state = await store.startFspDemoAuthorization(candidateId, initial.revision, "fsp-001");
    await assert.rejects(() => store.assertFspDemoState(otherCandidate, initial.revision, state.state),
      (error: unknown) => error instanceof ProfileError && error.code === "demo_state_invalid_or_replayed");
    const participant = {
      fsp_id: "fsp-001", rank: "I", rating: 1876,
      achievements: [{ claim_id: "fsp-001-achievement-2026-cup", title: "Кубок ФСП", year: 2026, place: 3 }],
      events: [], certificates: [], provider: "fsp_hackathon_registry", demo: true,
      account_ref: "demo-person-001", provider_version: "demo-2026-10",
    };
    await assert.rejects(() => store.completeFspDemoAuthorization(candidateId, initial.revision, state.state, { ...participant, fsp_id: "fsp-002" }),
      (error: unknown) => error instanceof ProfileError && error.code === "demo_fixture_mismatch");
    const completed = await store.completeFspDemoAuthorization(candidateId, initial.revision, state.state, participant);
    assert.equal(completed.fields.fsp_id, "fsp-001");
    assert.equal(completed.fields.fsp_claim_id, "fsp-001-achievement-2026-cup");
    assert.equal(completed.fields.achievements?.length, 2);
    assert.equal((await pool.query("SELECT count(*) FROM candidate_verification_ledger WHERE candidate_id=$1", [candidateId])).rows[0]?.count, "1");
    await assert.rejects(() => store.completeFspDemoAuthorization(candidateId, initial.revision, state.state, participant),
      (error: unknown) => error instanceof ProfileError && error.code === "demo_state_invalid_or_replayed");
    const expired = await store.startFspDemoAuthorization(candidateId, completed.revision, "fsp-001");
    await pool.query("UPDATE candidate_fsp_demo_auth_states SET expires_at=now()-interval '1 second' WHERE candidate_id=$1 AND used_at IS NULL", [candidateId]);
    await assert.rejects(() => store.assertFspDemoState(candidateId, completed.revision, expired.state),
      (error: unknown) => error instanceof ProfileError && error.code === "demo_state_invalid_or_replayed");
  } finally {
    await pool.end();
  }
});
