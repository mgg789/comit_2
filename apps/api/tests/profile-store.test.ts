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


test("training consent is optional, versioned, preserved on field edits and independently revocable", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "training-consent@example.test"]);
    const store = new CandidateProfileStore(pool);
    let view = await store.save(candidateId, { expected_revision: 0, fields: {}, consent: { processing: true, pool: true, version: "profile-v1" } });
    assert.equal(view.consents.training, false);
    assert.equal(view.consents.training_accepted_at, null);
    await assert.rejects(pool.query("UPDATE candidate_profiles SET training_consent=true,training_consent_accepted_at=now() WHERE candidate_id=$1", [candidateId]));
    await assert.rejects(() => store.save(candidateId, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true } }),
      (error: unknown) => error instanceof ProfileError && error.code === "invalid_training_consent");
    view = await store.save(candidateId, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true, training_version: "training-v1" } });
    assert.equal(view.consents.training, true);
    const stamp = view.consents.training_accepted_at;
    assert.ok(stamp);
    view = await store.save(candidateId, { expected_revision: 2, fields: { city: "Москва" } });
    assert.equal(view.consents.training_accepted_at, stamp);
    view = await store.save(candidateId, { expected_revision: 3, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: false } });
    assert.equal(view.consents.training, false);
    assert.equal(view.consents.processing, true);
    assert.equal(view.consents.pool, true);
    assert.equal(view.consents.training_version, null);
    const payload = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=4", [candidateId])).rows[0]?.payload;
    assert.equal(payload.consents.training, false);
    assert.equal(payload.consents.training_accepted_at, null);
  } finally {
    await pool.end();
  }
});

test("declared fsp_id is verified server-side, confirms algorithms and rides the snapshot", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    await pool.query(await readFile(new URL("../migrations/001_accounts.sql", import.meta.url), "utf8"));
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_profile_outbox, candidate_profile_evidence, candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true)", [candidateId, "candidate-fsp@example.test"]);
    const registry = new Map([["fsp-001", { fsp_id: "fsp-001", rank: "I", rating: 1876,
      events: [{ title: "Кубок ФСП", year: 2025, place: 7 }], certificates: ["Школа ФСП"] }]]);
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input: unknown) => {
      const url = String(input);
      const id = url.split("/fsp/participants/")[1] ?? "";
      const body = registry.get(decodeURIComponent(id));
      return { ok: Boolean(body), status: body ? 200 : 404, json: async () => body } as Response;
    }) as typeof fetch;
    try {
      const store = new CandidateProfileStore(pool);
      const view = await store.save(candidateId, {
        expected_revision: 0,
        fields: { city: "Москва", domains: [{ key: "algorithms", level: 6 }], fsp_id: "fsp-001" },
        consent: { processing: true, pool: true, version: "profile-v1" },
      });
      assert.equal(view.evidence["fsp:fsp-001"]?.status, "confirmed");
      assert.equal(view.evidence["domains:algorithms"]?.status, "confirmed");
      assert.deepEqual(view.evidence["domains:algorithms"]?.verified_value, { key: "algorithms", level: 6 });
      const payload = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 ORDER BY source_revision DESC LIMIT 1", [candidateId])).rows[0]?.payload;
      assert.equal(payload.fsp.fsp_id, "fsp-001");
      assert.equal(payload.fsp.rank, "I");
      assert.equal(payload.fsp.rating, 1876);
      assert.equal(payload.domains[0].status, "confirmed");
      const absent = await store.save(candidateId, {
        expected_revision: 1,
        fields: { fsp_id: "fsp-none" },
      });
      assert.equal(absent.evidence["fsp:fsp-none"]?.status, "pending");
      assert.equal(absent.evidence["domains:algorithms"]?.status, "self_reported");
    } finally {
      globalThis.fetch = originalFetch;
    }
  } finally {
    await pool.end();
  }
});
