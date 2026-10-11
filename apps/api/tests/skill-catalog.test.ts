/** Canonical source validation and trusted same-coordinate projection regressions. */
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test, mock } from "node:test";
import { activeSkillCatalog } from "../src/profiles/skill-catalog.js";
import { Pool } from "pg";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { TEST_SKILL_COORDINATES } from "@comit/shared";
import { fixtureSkillCatalog } from "./helpers/skill-catalog.js";
import { ProfileStore, ProfileError } from "../src/profiles/store.js";
import { canonicalJson, projectCandidateIntake, withTrustedSkillResults, type ProfileEvidenceRow } from "../src/profiles/projection.js";
import { candidateProfileRoutes } from "../src/routes/candidate-profile.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore } from "../src/auth/store.js";
import { hashSessionId } from "../src/auth/crypto.js";
import { signSession } from "../src/auth/session.js";

test("shared labels and test bindings cover the canonical matching registry exactly", async () => {
  const source = await readFile(new URL("../../../services/matching-engine/src/matching_engine/strict.py", import.meta.url), "utf8");
  const catalog = await fixtureSkillCatalog();
  for (const [group, sourceGroup] of [["domains", "domain"], ["stacks", "stack"], ["soft_skills", "soft_skill"]]) {
    const block = new RegExp(`"${sourceGroup}": \\(([\\s\\S]*?)\\)`).exec(source)?.[1];
    assert.ok(block, `canonical tuple ${sourceGroup} exists`);
    const keys = [...block.matchAll(/"([a-z_]+)"/g)].map(match => match[1]).sort();
    assert.deepEqual(catalog.skills.filter(skill => skill.group === group).map(skill => skill.key).sort(), keys);
  }
  for (const coordinate of Object.values(TEST_SKILL_COORDINATES)) assert.ok(catalog.skills.some(skill => skill.group === coordinate.group && skill.key === coordinate.key));
});

test("active catalog fails closed, reads real registry contract and caches only successful results", async () => {
  const priorToken = process.env.CANDIDATE_PIPELINE_TOKEN, priorUrl = process.env.MATCHING_ENGINE_URL;
  let calls = 0;
  const transport = mock.method(globalThis, "fetch", async (input: unknown, init?: RequestInit) => {
    calls++; assert.equal(String(input), "http://synthetic-matching.test/internal/strict-schema");
    assert.equal(new Headers(init?.headers).get("x-internal-token"), "synthetic-registry-token");
    if (calls === 1) return Response.json({}, { status: 503 });
    if (calls === 2) return Response.json({ version: 1, coordinates: [] });
    return Response.json({ version: 2, schema_hash: "synthetic-active-schema", coordinates: [
      { index: 0, group: "stack", key: "react" }, { index: 1, group: "domain", key: "new_registered_domain" },
    ] });
  });
  try {
    delete process.env.CANDIDATE_PIPELINE_TOKEN;
    await assert.rejects(activeSkillCatalog(), /skill_catalog_unavailable/); assert.equal(calls, 0);
    process.env.CANDIDATE_PIPELINE_TOKEN = "synthetic-registry-token"; process.env.MATCHING_ENGINE_URL = "http://synthetic-matching.test";
    await assert.rejects(activeSkillCatalog(), /skill_catalog_unavailable/);
    await assert.rejects(activeSkillCatalog(), /skill_catalog_invalid/);
    const catalog = await activeSkillCatalog();
    assert.equal(catalog.version, 2); assert.equal(catalog.skills[0]?.test_subject, "react");
    assert.equal(catalog.skills[1]?.key, "new_registered_domain"); assert.equal(catalog.skills[1]?.test_subject, null);
    assert.deepEqual(await activeSkillCatalog(), catalog); assert.equal(calls, 3);
  } finally { transport.mock.restore();
    if (priorToken === undefined) delete process.env.CANDIDATE_PIPELINE_TOKEN; else process.env.CANDIDATE_PIPELINE_TOKEN = priorToken;
    if (priorUrl === undefined) delete process.env.MATCHING_ENGINE_URL; else process.env.MATCHING_ENGINE_URL = priorUrl;
  }
});

const proof: ProfileEvidenceRow = { evidence_key: "grade:python", input_hash: "old", status: "confirmed", verified_value: { grade: "middle", session_id: "trusted-session" }, provenance: ["testing-engine: adaptive session"], checked_at: "2026-10-10T00:00:00Z" };
test("legacy subject evidence confirms precisely its coordinate and projects measured level without rewriting declaration", () => {
  const fields = { declared_grade: "senior" as const, stacks: [{ key: "python", level: 7 }], domains: [{ key: "sql", level: 3 }] };
  const normalized = withTrustedSkillResults(fields, [proof]);
  assert.equal(normalized.fields.stacks?.[0]?.level, 7);
  const mapped = normalized.rows.find(row => row.evidence_key === "stacks:python")!;
  assert.equal(mapped.input_hash, createHash("sha256").update(canonicalJson(fields.stacks[0])).digest("hex"));
  const projected = projectCandidateIntake({ id: randomUUID(), email: "synthetic@example.test", email_verified: true }, fields, [proof], 1,
    { processing: true, pool: true, version: "profile-v1", accepted_at: proof.checked_at });
  assert.equal(projected.stacks?.[0]?.level, 4); assert.equal(projected.stacks?.[0]?.status, "confirmed");
  assert.equal(projected.domains?.[0]?.status, "self_reported"); assert.equal(projected.grade_result?.grade, "senior");
  assert.equal(projected.grade_result?.status, "self_reported");
  assert.equal(withTrustedSkillResults({ stacks: [] }, [proof]).fields.stacks?.[0]?.key, "python", "legacy default empty groups recover accepted results");
  const removed: ProfileEvidenceRow = { ...proof, evidence_key: "external:testing_removed:python", status: "self_reported", verified_value: { removed: true } };
  assert.deepEqual(withTrustedSkillResults({ stacks: [] }, [proof, removed]).fields.stacks, [], "explicit removal marker is respected");
  assert.equal(withTrustedSkillResults({}, [proof]).fields.stacks?.[0]?.key, "python", "legacy absent groups gain their accepted subject");
  assert.equal(withTrustedSkillResults({}, [{ ...proof, provenance: [] }]).fields.stacks, undefined);
});

const databaseUrl = process.env.SKILL_CATALOG_TEST_DATABASE_URL;
test("PG registry validation, legacy edits, trusted results, profile reads and catalog ownership", { skip: !databaseUrl }, async () => {
  const schema = `skill_catalog_${randomUUID().replaceAll("-", "")}`;
  const admin = new Pool({ connectionString: databaseUrl }), pool = new Pool({ connectionString: databaseUrl, options: `-c search_path=${schema}` });
  const app = Fastify();
  try {
    await admin.query(`CREATE SCHEMA ${schema}`);
    for (const name of ["001_accounts", "002_candidate_profiles", "005_training_consent", "019_testing_grades"]) await pool.query(await readFile(new URL(`../migrations/${name}.sql`, import.meta.url), "utf8"));
    const id = randomUUID(), other = randomUUID();
    for (const candidateId of [id, other]) await pool.query("INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at) VALUES($1,$2,'candidate',true,'account-v1',now())", [candidateId, `${candidateId}@example.test`]);
    const catalog = await fixtureSkillCatalog(); let available = true;
    const profiles = new ProfileStore(pool, async () => { if (!available) throw new Error("registry_down"); return catalog; });
    let profile = await profiles.save(id, { expected_revision: 0, fields: { declared_grade: "senior", stacks: [{ key: "python", level: 7 }] } });
    for (const fields of [{ domains: [{ key: "python", level: 4 }] }, { stacks: [{ key: "invented", level: 4 }] }, { stacks: [{ key: "python", level: 0 }] }]) {
      await assert.rejects(profiles.save(id, { expected_revision: profile.revision, fields }), error => error instanceof ProfileError && error.status === 400);
    }
    await pool.query("UPDATE candidate_profiles SET fields=fields||$2::jsonb WHERE candidate_id=$1", [id, JSON.stringify({ domains: [{ key: "legacy_custom", level: 3 }] })]);
    available = false;
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { city: "Москва" } });
    assert.equal(profile.fields.domains?.[0]?.key, "legacy_custom");
    await assert.rejects(profiles.save(id, { expected_revision: profile.revision, fields: { stacks: [{ key: "react", level: 4 }] } }), /registry_down/);
    available = true;
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { domains: [{ key: "sql", level: 3 }] } });
    const result = { sessionId: "same-coordinate", grade: "middle" as const, specialization: "python", supervisionBadge: null, tasksAnswered: 12 };
    await profiles.recordTrustedGrade(id, result); await profiles.recordTrustedGrade(id, result);
    assert.equal((await pool.query("SELECT count(*) FROM candidate_grade_results WHERE candidate_id=$1", [id])).rows[0].count, "1");
    profile = (await profiles.get(id))!;
    assert.equal(profile.fields.declared_grade, "senior"); assert.equal(profile.fields.stacks?.[0]?.level, 7);
    assert.equal(profile.evidence["stacks:python"]?.status, "confirmed");
    const snapshot = (await pool.query("SELECT payload FROM candidate_profile_outbox WHERE candidate_id=$1 ORDER BY source_revision DESC LIMIT 1", [id])).rows[0].payload;
    assert.equal(snapshot.stacks[0].level, 4); assert.equal(snapshot.stacks[0].status, "confirmed");
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { city: "Казань" } });
    assert.equal(profile.evidence["stacks:python"]?.status, "confirmed");
    await pool.query("DELETE FROM candidate_profile_evidence WHERE candidate_id=$1 AND (evidence_key LIKE 'grade:%' OR evidence_key='stacks:python')", [id]);
    assert.equal((await profiles.get(id))?.evidence["stacks:python"]?.status, "confirmed", "old dropped evidence recovers from accepted ledger");
    // Simulate a delivered pre-mapping snapshot; backfill must invalidate it exactly once.
    await pool.query("UPDATE candidate_profile_outbox SET state='delivered', payload=jsonb_set(payload,'{stacks,0,level}','7'::jsonb) WHERE candidate_id=$1 AND source_revision=$2", [id,profile.revision]);
    const before = profile.revision;
    assert.deepEqual(await profiles.reconcileTrustedSkillResults(1), { processed: 1, reprojected: 1 });
    profile = (await profiles.get(id))!;
    assert.equal(profile.revision, before+1); assert.equal(profile.fields.stacks?.[0]?.level, 7);
    assert.deepEqual(await profiles.reconcileTrustedSkillResults(1), { processed: 0, reprojected: 0 });
    assert.equal((await profiles.get(id))?.revision, before+1);
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { city: "Самара" } });
    assert.deepEqual(await profiles.reconcileTrustedSkillResults(1), { processed: 0, reprojected: 0 }, "marker survives unrelated source edits");
    const config = loadAuthConfig({ AUTH_SECRET: "synthetic-skill-catalog-secret-32bytes", AUTH_ALLOWED_ORIGINS: "http://localhost", AUTH_COOKIE_SECURE: "false" });
    await app.register(cookie); await app.register(candidateProfileRoutes, { profiles, config, store: new AuthStore(pool, config.secret), mailConfigured: false });
    const auth = async (candidateId: string) => { const sid = Buffer.from(randomUUID()+randomUUID()).toString("base64url").slice(0,43), expires = new Date(Date.now()+60000).toISOString();
      await pool.query("INSERT INTO sessions(id,account_id,role,session_hash,expires_at) VALUES($1,$2,'candidate',$3,$4)", [randomUUID(), candidateId, hashSessionId(sid), expires]);
      return { authorization: `Bearer ${await signSession(config, "candidate", candidateId, sid, expires)}` }; };
    assert.equal((await app.inject({ url: "/candidate/skills/catalog" })).statusCode, 401);
    const headers = await auth(id), otherHeaders = await auth(other);
    const response = await app.inject({ url: "/candidate/skills/catalog", headers }); assert.equal(response.statusCode, 200, response.body);
    const python = response.json().skills.find((skill: { key: string }) => skill.key === "python");
    assert.equal(python.confirmed_level, 4); assert.ok(Date.parse(python.upgrade_at) > Date.now());
    assert.equal((await app.inject({ url: "/candidate/skills/catalog", headers: otherHeaders })).json().skills.find((skill: { key: string }) => skill.key === "python").confirmed_level, null);
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { stacks: [] } });
    assert.deepEqual(profile.fields.stacks, []); assert.ok(profile.evidence["external:testing_removed:python"]);
    assert.deepEqual((await profiles.get(id))?.fields.stacks, []);
    // Removing the one-time rollout marker cannot resurrect a candidate's explicit removal.
    await pool.query("DELETE FROM candidate_profile_evidence WHERE candidate_id=$1 AND evidence_key='external:testing_mapping:v1'", [id]);
    await profiles.reconcileTrustedSkillResults(1);
    profile = (await profiles.get(id))!; assert.deepEqual(profile.fields.stacks, []);
    profile = await profiles.save(id, { expected_revision: profile.revision, fields: { stacks: [{ key: "python", level: 6 }] } });
    assert.equal(profile.evidence["external:testing_removed:python"], undefined); assert.equal(profile.evidence["stacks:python"]?.status, "confirmed");
    available = false; assert.equal((await app.inject({ url: "/candidate/skills/catalog", headers })).statusCode, 503);
  } finally { await app.close(); await pool.end(); await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`); await admin.end(); }
});
