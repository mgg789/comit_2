/** Public discovery tests exercise real sessions and isolated private-service HTTP responses. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, PublicCandidateDetail, ShowcaseLikeReceipt } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { accountRoutes } from "../src/routes/accounts.js";

const databaseUrl = process.env.SHOWCASE_ROUTE_TEST_DATABASE_URL;
const secret = "synthetic-only-showcase-gateway-secret-32-bytes";
const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:38088", AUTH_COOKIE_SECURE: "false" });

test("showcase public boundary, PDF watermark, ownership and exact manual-like replay", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts, auth_challenges, auth_issue_events, auth_verify_events RESTART IDENTITY CASCADE");
    const store = new AuthStore(pool, secret);
    const profiles = new ProfileStore(pool);
    let sourceCurrent = true;
    profiles.isSelectionCurrent = async () => sourceCurrent;
    const login = async (role: AccountRole, email: string) => {
      const issued = await store.issue(role, email, "192.0.2.51");
      const mail = await store.claimMail(); assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const verified = await store.verify(role, email, issued.challenge_id, mail.code, "account-v1");
      return { id: verified.account.id, authorization: `Bearer ${await signSession(config, role, verified.account.id, verified.session_id, verified.expires_at)}` };
    };
    const owner = await login("employer", "showcase-owner@example.test");
    const foreign = await login("employer", "showcase-foreign@example.test");
    const candidate = await login("candidate", "showcase-candidate@example.test");
    const id = randomUUID(), draftId = randomUUID(), vacancyId = randomUUID();
    const salary = { from_rub: 100000, to_rub: 150000 };
    const detail: PublicCandidateDetail = {
      candidate_id: id, profile_revision: 1, display_name: `Candidate ${id}`, city: "Moscow", salary,
      category: { specialization: "backend", grade: "middle", grade_status: "self_reported" }, rank: 1,
      confirmed_strength: 0, resume_url: "/internal/candidates/private/resume.pdf",
      search_factors: { lexical_rank: 1, semantic_rank: null, rrf_score: 1 / 61 },
      public_profile: { projection_version: 1, display_name: `Candidate ${id}`, city: "Moscow", salary,
        description: "Public description", work_types: ["remote"], relocation_ready: false, relocation_cities: [],
        preferences: [], domains: [], stacks: [], soft_skills: [], projects: [], experience: [], achievements: [] },
    };
    let receipt: ShowcaseLikeReceipt | null = null;
    let malicious = false, changeDuringPdf = false, warn = false, writes = 0;
    const mismatchToken = "a".repeat(64);
    const calls: Array<{ path: string; owner: string | null }> = [];
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      const account = new Headers(init?.headers).get("x-owner-id");
      calls.push({ path: url.pathname, owner: account });
      if (url.pathname === `/internal/vacancy-drafts/${draftId}`) {
        if (account !== owner.id) return Response.json({}, { status: 404 });
        return Response.json({ draft_id: draftId, vacancy_id: vacancyId, owner_id: owner.id, revision: 1,
          state: "created", fields: {}, publication: { status: "accepted", source_revision: 1 } });
      }
      if (url.pathname === "/internal/employer/candidates/search") {
        const { public_profile: _profile, ...card } = detail;
        return Response.json({ items: [card], total: 1, limit: 50, has_more: false, next_cursor: null,
          mode: "lexical", fallback_reason: "query_encoder_unavailable" });
      }
      if (url.pathname === `/internal/employer/candidates/${id}`) return Response.json(malicious ? { ...detail, contacts: { email: "private@example.test" } } : detail);
      if (url.pathname === `/internal/pool/${id}/resume.pdf`) {
        if (changeDuringPdf) sourceCurrent = false;
        return new Response("%PDF-synthetic-public", { headers: { "content-type": "application/pdf" } });
      }
      if (url.pathname.startsWith(`/internal/vacancies/${vacancyId}/showcase-likes/`)) return receipt ? Response.json(receipt) : Response.json({}, { status: 404 });
      if (url.pathname === `/internal/vacancies/${vacancyId}/showcase-likes`) {
        const body = JSON.parse(String(init?.body)) as { candidate_id: string; profile_revision: number; idempotency_key: string; mismatch_token?: string };
        if (warn) return Response.json({ detail: { code: "strict_filter_confirmation_required", candidate_id: id,
          profile_revision: 1, vacancy_revision: 1, warnings: ["salary_disjoint"], mismatch_token: mismatchToken } }, { status: 409 });
        writes++;
        receipt ??= { receipt_id: randomUUID(), exposure_id: randomUUID(), vacancy_id: vacancyId, candidate_id: body.candidate_id,
          profile_revision: body.profile_revision, selection_source: "showcase", action: "like", idempotency_key: body.idempotency_key,
          saved: true, visited: false, replayed: false, history_badges: [],
          strict_filter_warnings: body.mismatch_token ? ["salary_disjoint"] : [], mismatch_token: body.mismatch_token ?? null };
        return Response.json({ ...receipt, replayed: writes > 1 });
      }
      return Response.json({}, { status: 404 });
    };
    await app.register(cookie);
    await app.register(accountRoutes, { store, config, profiles, mailConfigured: false, agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport });
    await app.ready();
    const headers = { authorization: owner.authorization, "x-owner-id": foreign.id };
    const search = "/employer/candidates/search";
    assert.equal((await app.inject({ method: "POST", url: search, payload: {} })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: search, headers: { authorization: candidate.authorization }, payload: {} })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: search, headers, payload: { owner_id: foreign.id } })).statusCode, 400);
    const page = await app.inject({ method: "POST", url: search, headers, payload: {} });
    assert.equal(page.statusCode, 200, page.body);
    assert.equal(calls.at(-1)?.owner, owner.id);
    assert.equal(page.json().items[0].resume_url, `/api/employer/candidates/${id}/resume.pdf?profile_revision=1`);
    assert.equal((await app.inject({ url: `/employer/candidates/${id}`, headers })).statusCode, 200);
    malicious = true;
    assert.equal((await app.inject({ url: `/employer/candidates/${id}`, headers })).statusCode, 503);
    malicious = false;
    const pdfPath = `/employer/candidates/${id}/resume.pdf?profile_revision=1`;
    assert.equal((await app.inject({ url: pdfPath, headers })).statusCode, 200);
    assert.ok(calls.some(call => call.path === `/internal/pool/${id}/resume.pdf`));
    assert.ok(!calls.some(call => call.path === "/internal/candidates/private/resume.pdf"));
    assert.equal((await app.inject({ url: pdfPath.replace("=1", "=2"), headers })).statusCode, 409);
    changeDuringPdf = true;
    assert.equal((await app.inject({ url: pdfPath, headers })).statusCode, 409);
    changeDuringPdf = false;
    sourceCurrent = true;
    const likePath = `/employer/vacancy-drafts/${draftId}/showcase-likes`;
    const body = { candidate_id: id, profile_revision: 1, idempotency_key: randomUUID() };
    assert.equal((await app.inject({ method: "POST", url: likePath, headers: { authorization: foreign.authorization }, payload: body })).statusCode, 404);
    const liked = await app.inject({ method: "POST", url: likePath, headers, payload: body });
    assert.equal(liked.statusCode, 200, liked.body);
    sourceCurrent = false;
    assert.equal((await app.inject({ method: "POST", url: search, headers, payload: {} })).statusCode, 409);
    const replayed = await app.inject({ method: "POST", url: likePath, headers, payload: body });
    assert.equal(replayed.statusCode, 200, replayed.body);
    assert.equal(replayed.json().replayed, true);
    assert.equal((await app.inject({ method: "POST", url: likePath, headers, payload: { ...body, profile_revision: 2 } })).statusCode, 409);
    assert.equal(writes, 2);
    receipt = null;
    sourceCurrent = true;
    warn = true;
    const warning = await app.inject({ method: "POST", url: likePath, headers, payload: body });
    assert.equal(warning.statusCode, 409);
    assert.deepEqual(warning.json().detail.warnings, ["salary_disjoint"]);
    assert.equal(warning.json().detail.mismatch_token, mismatchToken);
    assert.equal(writes, 2);
    warn = false;
    const acknowledged = { ...body, mismatch_token: mismatchToken };
    const override = await app.inject({ method: "POST", url: likePath, headers, payload: acknowledged });
    assert.equal(override.statusCode, 200, override.body);
    assert.deepEqual(override.json().strict_filter_warnings, ["salary_disjoint"]);
    assert.equal((await app.inject({ method: "POST", url: likePath, headers, payload: body })).statusCode, 409);
    sourceCurrent = false;
    assert.equal((await app.inject({ method: "POST", url: likePath, headers, payload: acknowledged })).statusCode, 200);
    receipt = null;
    assert.equal((await app.inject({ method: "POST", url: likePath, headers, payload: { ...body, idempotency_key: randomUUID() } })).statusCode, 409);
    assert.equal(writes, 4);
  } finally { await app.close(); await pool.end(); }
});
