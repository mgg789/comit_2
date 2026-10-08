/** Real PostgreSQL regression coverage for candidate-owned verification state. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { Pool } from "pg";
import { CandidateProfileStore, ProfileError } from "../src/profiles/store.js";

const databaseUrl = process.env.VERIFICATION_TEST_DATABASE_URL;
const candidateId = "00000000-0000-4000-8000-000000000021";
const otherId = "00000000-0000-4000-8000-000000000022";

test("verification flow is bound to PG state, ledger, outbox and encrypted ownership", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const oldKey = process.env.VERIFICATION_DOCUMENT_KEY;
  process.env.VERIFICATION_DOCUMENT_KEY = "verification-test-key";
  try {
    await pool.query(await readFile(new URL("../migrations/001_accounts.sql", import.meta.url), "utf8"));
    const { migrate } = await import("../src/auth/migrate.js");
    await migrate(pool);
    await pool.query("TRUNCATE candidate_fsp_demo_auth_states, candidate_verification_ledger, candidate_verification_documents, candidate_profile_outbox, candidate_profile_evidence, candidate_profiles, accounts CASCADE");
    await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES ($1,$2,'candidate',true),($3,$4,'candidate',true)", [candidateId, "verification-flow@example.test", otherId, "verification-other@example.test"]);
    const store = new CandidateProfileStore(pool);
    const initial = await store.save(candidateId, {
      expected_revision: 0,
      fields: {
        projects: [{ title: "Public adapter", description: "Implemented adapter", link: "https://github.com/acme/adapter" }],
        experience: [{ title: "Backend engineer", organization: "Acme" }],
      },
      consent: { processing: true, pool: true, version: "profile-v1" },
    });
    const projectId = initial.fields.projects?.[0]?.id;
    assert.ok(projectId);
    const state = await store.startFspDemoAuthorization(candidateId, initial.revision, "fsp-001");
    await assert.rejects(() => store.assertFspDemoState(otherId, initial.revision, state.state),
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
    const importedId = completed.fields.achievements?.find(item => item.organization === "ФСП")?.id;
    assert.ok(importedId);
    assert.equal(completed.evidence[`achievements:${importedId}`]?.status, "confirmed");
    assert.equal(completed.evidence[`external:fsp:fsp-001:fsp-001-achievement-2026-cup`]?.status, "partial");
    assert.equal((await pool.query("SELECT count(*) FROM candidate_verification_ledger WHERE candidate_id=$1", [candidateId])).rows[0]?.count, "1");
    assert.ok((await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=$2", [candidateId, completed.revision])).rows[0]?.payload.external_evidence);

    const repo = await store.applyVerification(candidateId, completed.revision, `external:repository:projects:${projectId}`,
      { url: "https://github.com/acme/adapter", contribution_narrative: "Implemented adapter" },
      { kind: "repository", subject: "projects", claim_id: projectId, url: "https://github.com/acme/adapter", narrative: "Implemented adapter" },
      { status: "partial", verified_value: { url: "https://github.com/acme/adapter", repository_nonempty: true, contribution_narrative: "Implemented adapter", authorship_proven: false }, provenance: ["repo:github.com:public_git_tree"] });
    assert.equal(repo.evidence[`external:repository:projects:${projectId}`]?.status, "partial");
    const changed = await store.save(candidateId, { expected_revision: repo.revision, fields: { projects: [{ id: projectId, title: "Public adapter", description: "Changed narrative", link: "https://github.com/acme/adapter" }] } });
    assert.equal(changed.evidence[`external:repository:projects:${projectId}`], undefined);

    const claim = changed.fields.projects?.[0];
    assert.ok(claim?.id);
    const uploaded = await store.uploadVerificationDocument(candidateId, { filename: "Диплом.pdf", content_type: "application/pdf", content_base64: "", claim_kind: "projects", claim_id: claim.id }, Buffer.from("encrypted document"));
    assert.equal(uploaded.status, "pending");
    assert.equal(uploaded.filename, "Диплом.pdf");
    await assert.rejects(() => store.uploadVerificationDocument(candidateId, { filename: "../proof.pdf", content_type: "application/pdf", content_base64: "", claim_kind: "projects", claim_id: claim.id }, Buffer.from("x")),
      (error: unknown) => error instanceof ProfileError && error.code === "invalid_document_metadata");
    await assert.rejects(() => store.uploadVerificationDocument(candidateId, { filename: "bad\r\n.pdf", content_type: "application/pdf", content_base64: "", claim_kind: "projects", claim_id: claim.id }, Buffer.from("x")),
      (error: unknown) => error instanceof ProfileError && error.code === "invalid_document_metadata");
    assert.deepEqual((await store.downloadVerificationDocument(candidateId, uploaded.id)).content, Buffer.from("encrypted document"));
    await assert.rejects(() => store.downloadVerificationDocument(otherId, uploaded.id), (error: unknown) => error instanceof ProfileError && error.code === "document_not_found");
    await pool.query("UPDATE candidate_verification_documents SET ciphertext=decode('00','hex') WHERE id=$1", [uploaded.id]);
    await assert.rejects(() => store.downloadVerificationDocument(candidateId, uploaded.id), (error: unknown) => error instanceof ProfileError && ["document_decryption_failed", "document_ciphertext_invalid"].includes(error.code));

    const fresh = await store.startFspDemoAuthorization(candidateId, changed.revision, "fsp-001");
    const concurrent = await Promise.allSettled([
      store.completeFspDemoAuthorization(candidateId, changed.revision, fresh.state, participant),
      store.completeFspDemoAuthorization(candidateId, changed.revision, fresh.state, participant),
    ]);
    assert.equal(concurrent.filter(result => result.status === "fulfilled").length, 1);
    assert.equal(concurrent.filter(result => result.status === "rejected").length, 1);

    const relinked = await store.get(candidateId);
    assert.ok(relinked);
    await assert.rejects(() => store.save(candidateId, { expected_revision: relinked.revision, fields: { fsp_id: "fsp-002" } }),
      (error: unknown) => error instanceof ProfileError && error.code === "server_managed_field");
    const unlinked = await store.save(candidateId, { expected_revision: relinked.revision, fields: { achievements: relinked.fields.achievements ?? [] } }, true);
    assert.equal(unlinked.fields.fsp_id, undefined);
    assert.equal(Object.values(unlinked.evidence).some(row => row.status === "confirmed" && row.provenance.some(item => item.startsWith("fsp_hackathon_registry"))), false);
  } finally {
    if (oldKey === undefined) delete process.env.VERIFICATION_DOCUMENT_KEY;
    else process.env.VERIFICATION_DOCUMENT_KEY = oldKey;
    await pool.end();
  }
});
