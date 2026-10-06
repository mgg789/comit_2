/** Real PostgreSQL/auth checks; the upstream HTTP transport is explicitly an isolated test double. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, CandidateProfileView, SelectionPage } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { candidateProfileRoutes } from "../src/routes/candidate-profile.js";
import { accountRoutes } from "../src/routes/accounts.js";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";

const databaseUrl = process.env.PROFILE_ROUTE_TEST_DATABASE_URL;
test("source profile ownership, proof rejection, CAS and selected PDF consent watermarks", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts, auth_challenges, auth_issue_events, auth_verify_events RESTART IDENTITY CASCADE");
    const secret = "synthetic-only-profile-route-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39580", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret);
    const profiles = new ProfileStore(pool);
    const login = async (role: AccountRole, email: string) => {
      const receipt = await store.issue(role, email, "192.0.2.10");
      const mail = await store.claimMail();
      assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const verified = await store.verify(role, email, receipt.challenge_id, mail.code, "account-v1");
      return { id: verified.account.id, authorization: `Bearer ${await signSession(config, role, verified.account.id, verified.session_id, verified.expires_at)}` };
    };
    const candidate = await login("candidate", "candidate-profile@example.test");
    const otherCandidate = await login("candidate", "candidate-other@example.test");
    const employer = await login("employer", "employer-profile@example.test");
    const foreignEmployer = await login("employer", "employer-other@example.test");
    const draftId = randomUUID(), vacancyId = randomUUID();
    const page: SelectionPage = {
      run: { run_id: randomUUID(), vacancy_id: vacancyId, vacancy_revision: 1, strict_schema_version: 1,
        strict_schema_hash: "schema-test", pool_generation: 1, policy_version: "main-v2", policy_hash: "policy-test",
        stage: "main_selection", state: "ready", top_k: 1, attempt_count: 1, error_code: null,
        scanned_count: 1, passed_count: 1, selected_count: 1, filter_counts: {}, published_at: new Date().toISOString() },
      items: [{ candidate_id: candidate.id, profile_revision: 11, position: 1, display_name: "Candidate anonymous", city: "Moscow",
        salary: { from_rub: 100, to_rub: 200 }, category: { specialization: "backend", grade: "middle", grade_status: "self_reported" },
        rank: 1, confirmed_strength: 0, resume_url: `/internal/pool/${candidate.id}/resume.pdf`, filter_factors: ["class_match"],
        explanation: { grade_status: "self_reported", grade_confidence: 0.6, rating_score: 1, strict_score: null,
          semantic_cosine: null, semantic_unavailable_reason: "vacancy_bio_absent", rating_contribution: 0.6,
          strict_contribution: 0, semantic_contribution: 0, score: 0.6, top_matched_features: [] } }], limit: 1, offset: 0,
    };
    const exposureId = randomUUID(), feedSessionId = randomUUID();
    const decisionKey = randomUUID(), receiptId = randomUUID();
    const feedPage = { session_id: feedSessionId, vacancy_id: vacancyId, vacancy_revision: 1, policy_hash: "feed-policy",
      expires_at: new Date(Date.now() + 86400000).toISOString(), cursor: null, next_cursor: null, has_more: false,
      items: [{ ...page.items[0], exposure_id: exposureId, session_id: feedSessionId, kind: "recommended", main_position: 1,
        rerank_position: null, mode: "main_fallback", rerank_score: null, rerank_contributions: {}, artifact_hash: null,
        served_at: new Date().toISOString(), decision: null, history_badges: [], change_assessment: null }] };
    let decisionCalls = 0;
    let impressionCalls = 0;
    let mutateExposureAfterPdf = false, exposureReads = 0;
    const forwarded: Array<{ path: string; owner: string | null }> = [];
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      const owner = new Headers(init?.headers).get("x-owner-id");
      forwarded.push({ path: url.pathname, owner });
      if (url.pathname.startsWith("/internal/vacancy-drafts/")) {
        if (owner !== employer.id) return Response.json({ detail: "owner_record_not_found" }, { status: 404 });
        return Response.json({ draft_id: draftId, vacancy_id: vacancyId, owner_id: employer.id, revision: 1,
          state: "created", fields: {}, publication: { status: "accepted", source_revision: 1 } });
      }
      if (url.pathname.includes("/feed/exposures/")) {
        exposureReads += 1;
        return Response.json(mutateExposureAfterPdf && exposureReads > 1 ? { ...feedPage.items[0], candidate_id: otherCandidate.id } : feedPage.items[0]);
      }
      if (url.pathname.includes("/feed/decisions/")) return Response.json({ detail: "not_found" }, { status: 404 });
      if (url.pathname.includes("/feed/impressions/")) return Response.json({ detail: "not_found" }, { status: 404 });
      if (url.pathname.endsWith("/feed/impressions")) {
        impressionCalls += 1;
        return Response.json({ event_id: receiptId, exposure_id: exposureId, recorded_at: new Date().toISOString(), replayed: false });
      }
      if (url.pathname.endsWith("/feed/decisions")) {
        decisionCalls += 1;
        return Response.json({ receipt_id: receiptId, exposure_id: exposureId, vacancy_id: vacancyId, candidate_id: candidate.id,
          action: "like", idempotency_key: decisionKey, saved: true, visited: false, replayed: false, history_badges: [] });
      }
      if (url.pathname.includes("/feed")) return Response.json(feedPage);
      if (url.pathname.endsWith("/selections")) return Response.json(page);
      if (url.pathname.endsWith("/resume.pdf")) return new Response("%PDF-1.4 isolated-test-only", { headers: { "content-type": "application/pdf" } });
      return Response.json(page.run);
    };
    const options = { store, config, profiles, mailConfigured: false, agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport };
    await app.register(cookie);
    await app.register(candidateProfileRoutes, options);
    await app.register(accountRoutes, options);
    await app.ready();
    const headers = { authorization: candidate.authorization };
    assert.equal(await profiles.get(candidate.id), null);
    const absent = await app.inject({ url: "/candidate/profile", headers });
    assert.equal(absent.statusCode, 404, absent.body);
    assert.equal((await app.inject({ method: "PATCH", url: "/candidate/profile", payload: { expected_revision: 0, fields: {} } })).statusCode, 401);
    assert.equal((await app.inject({ method: "PATCH", url: "/candidate/profile", headers: { authorization: employer.authorization },
      payload: { expected_revision: 0, fields: {} } })).statusCode, 401);
    for (const fields of [{ email_verified: true }, { candidate_id: otherCandidate.id }, { grade_result: { status: "approved" } },
      { stacks: [{ key: "python", level: 5, status: "confirmed" }] }, { projects: [{ title: "A", facts: { duration_months: { value: 12, status: "confirmed" } } }] }]) {
      assert.equal((await app.inject({ method: "PATCH", url: "/candidate/profile", headers, payload: { expected_revision: 0, fields } })).statusCode, 400);
    }
    const saved = await app.inject({ method: "PATCH", url: "/candidate/profile", headers: { ...headers, "x-owner-id": otherCandidate.id },
      payload: { expected_revision: 0, fields: { display_name: "Private", city: "Moscow", salary: { from_rub: 100, to_rub: 200 },
        primary_direction: "backend", declared_grade: "middle", stacks: [{ key: "python", level: 5 }],
        projects: [{ title: "Portfolio", link: "https://example.test/project", facts: { duration_months: 12 } }] },
        consent: { processing: true, pool: true, version: "profile-v1" } } });
    assert.equal(saved.statusCode, 200, saved.body);
    const profile = saved.json<CandidateProfileView>();
    assert.equal(profile.candidate_id, candidate.id);
    assert.ok(profile.consents.accepted_at);
    assert.ok(profile.fields.projects?.[0].id);
    assert.ok(Object.values(profile.evidence).every(item => item.status === "self_reported" && item.verified_value === null));
    assert.equal((await app.inject({ url: "/candidate/profile", headers: { authorization: otherCandidate.authorization } })).statusCode, 404);
    assert.equal((await app.inject({ url: "/candidate/pipeline", headers })).statusCode, 409);
    assert.equal((await app.inject({ method: "PATCH", url: "/candidate/profile", headers,
      payload: { expected_revision: 0, fields: { description: "stale" } } })).statusCode, 409);
    assert.equal((await app.inject({ method: "PATCH", url: "/candidate/profile", headers: { ...headers, origin: "https://foreign.example" },
      payload: { expected_revision: 1, fields: {} } })).statusCode, 403);
    const job = await profiles.claimProjection();
    assert.ok(job);
    assert.equal(await profiles.acknowledgeProjection(job.job_id, job.lease_token, candidate.id, 1, 11), true);
    const path = `/employer/vacancy-drafts/${draftId}`;
    const employerHeaders = { authorization: employer.authorization, "x-owner-id": foreignEmployer.id };
    assert.equal((await app.inject({ url: `${path}/selections`, headers: { authorization: foreignEmployer.authorization, "x-owner-id": employer.id } })).statusCode, 404);
    const selection = await app.inject({ url: `${path}/selections?limit=1&offset=0`, headers: employerHeaders });
    assert.equal(selection.statusCode, 200, selection.body);
    assert.equal(selection.json<SelectionPage>().items[0].resume_url, `/api${path}/candidates/${candidate.id}/resume.pdf`);
    assert.equal(forwarded.at(-1)?.owner, employer.id);
    assert.equal((await app.inject({ url: `${path}/candidates/${otherCandidate.id}/resume.pdf`, headers: employerHeaders })).statusCode, 404);
    assert.equal((await app.inject({ url: `${path}/candidates/${candidate.id}/resume.pdf`, headers: employerHeaders })).statusCode, 200);
    assert.equal((await app.inject({ url: `${path}/selections?limit=501`, headers: employerHeaders })).statusCode, 400);
    assert.equal((await app.inject({ method: "POST", url: `${path}/feed`, headers: { authorization: foreignEmployer.authorization }, payload: {} })).statusCode, 404);
    const feed = await app.inject({ method: "POST", url: `${path}/feed`, headers: employerHeaders, payload: {} });
    assert.equal(feed.statusCode, 200, feed.body);
    assert.equal(feed.json().items[0].resume_url, `/api${path}/feed/exposures/${exposureId}/resume.pdf`);
    const impression = await app.inject({ method: "POST", url: `${path}/feed/impressions`, headers: employerHeaders,
      payload: { exposure_id: exposureId, idempotency_key: randomUUID() } });
    assert.equal(impression.statusCode, 200, impression.body);
    assert.equal(impressionCalls, 1);
    assert.equal((await app.inject({ method: "POST", url: `${path}/feed/impressions`,
      headers: { authorization: foreignEmployer.authorization }, payload: { exposure_id: exposureId, idempotency_key: randomUUID() } })).statusCode, 404);
    const decision = await app.inject({ method: "POST", url: `${path}/feed/decisions`, headers: employerHeaders,
      payload: { exposure_id: exposureId, action: "like", idempotency_key: decisionKey } });
    assert.equal(decision.statusCode, 200, decision.body);
    assert.equal(decisionCalls, 1);
    const feedPdf = await app.inject({ url: `${path}/feed/exposures/${exposureId}/resume.pdf`, headers: employerHeaders });
    assert.equal(feedPdf.statusCode, 200, feedPdf.body);
    assert.equal(forwarded.at(-1)?.owner, employer.id);
    mutateExposureAfterPdf = true; exposureReads = 0;
    assert.equal((await app.inject({ url: `${path}/feed/exposures/${exposureId}/resume.pdf`, headers: employerHeaders })).statusCode, 409);
    mutateExposureAfterPdf = false;
    assert.equal((await app.inject({ url: `${path}/feed/${feedSessionId}?limit=100`, headers: employerHeaders })).statusCode, 200);
    assert.equal((await app.inject({ url: `${path}/feed/${feedSessionId}?limit=101`, headers: employerHeaders })).statusCode, 400);
    assert.equal((await app.inject({ url: `${path}/saved?limit=101`, headers: employerHeaders })).statusCode, 400);
    assert.equal((await app.inject({ url: `${path}/visited?limit=101`, headers: employerHeaders })).statusCode, 400);
    const revoke = await app.inject({ method: "PATCH", url: "/candidate/profile", headers,
      payload: { expected_revision: 1, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } } });
    assert.equal(revoke.statusCode, 200, revoke.body);
    assert.equal((await app.inject({ url: `${path}/selections`, headers: employerHeaders })).statusCode, 409);
    assert.equal((await app.inject({ url: `${path}/feed/exposures/${exposureId}/resume.pdf`, headers: employerHeaders })).statusCode, 409);
    const staleDecision = await app.inject({ method: "POST", url: `${path}/feed/decisions`, headers: employerHeaders,
      payload: { exposure_id: exposureId, action: "like", idempotency_key: randomUUID() } });
    assert.equal(staleDecision.statusCode, 409, staleDecision.body);
    assert.equal(decisionCalls, 1);
    assert.equal((await app.inject({ method: "POST", url: `${path}/feed`, headers: employerHeaders, payload: {} })).statusCode, 409);
    assert.equal((await app.inject({ method: "POST", url: `${path}/feed/impressions`, headers: employerHeaders,
      payload: { exposure_id: exposureId, idempotency_key: randomUUID() } })).statusCode, 409);
    assert.equal(impressionCalls, 1);
    assert.equal((await app.inject({ url: `${path}/candidates/${candidate.id}/resume.pdf`, headers: employerHeaders })).statusCode, 409);
  } finally { await app.close(); await pool.end(); }
});
