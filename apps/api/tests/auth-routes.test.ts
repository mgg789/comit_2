/** Real PostgreSQL HTTP checks for passwordless accounts and ownership boundaries. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, AuthenticatedSession, EmailCodeReceipt } from "@comit/shared";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore } from "../src/auth/store.js";
import { migrate } from "../src/auth/migrate.js";
import { authRoutes, normalizeEmail } from "../src/routes/auth.js";
import { accountRoutes } from "../src/routes/accounts.js";

const origin = "http://localhost:38088";
const secret = "synthetic-only-auth-secret-32-bytes-minimum";
const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: origin, AUTH_COOKIE_SECURE: "false" });

test("auth configuration fails closed and email identities are canonical", () => {
  assert.throws(() => loadAuthConfig({ JWT_SECRET: "changeme" }));
  assert.equal(loadAuthConfig({ AUTH_SECRET: "", JWT_SECRET: secret, AUTH_ALLOWED_ORIGINS: `${origin}, ` }).secret, secret);
  assert.throws(() => loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: origin,
    AUTH_COOKIE_SECURE: "false", NODE_ENV: "production" }));
  assert.equal(normalizeEmail(" User@BÜCHER.example "), "user@xn--bcher-kva.example");
  assert.throws(() => normalizeEmail("not-an-email"));
});

const databaseUrl = process.env.AUTH_ROUTE_TEST_DATABASE_URL;
test("HTTP auth enforces one email/one role, consent, cookies, revocation and gateway ownership", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify();
  try {
    await migrate(pool);
    await pool.query("TRUNCATE auth_mail_outbox, auth_verify_events, auth_issue_events, sessions, auth_challenges, accounts CASCADE");
    const store = new AuthStore(pool, secret);
    await app.register(cookie);
    await app.register(authRoutes, { config, store, mailConfigured: true });
    const forwarded: Array<{ pathname: string; owner: string | null }> = [];
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      forwarded.push({ pathname: url.pathname, owner: new Headers(init?.headers).get("x-owner-id") });
      if (url.pathname.endsWith("resume.pdf")) return new Response(Buffer.from("%PDF-test-only"), { headers: { "content-type": "application/pdf" } });
      return Response.json([]);
    };
    await app.register(accountRoutes, { config, store, mailConfigured: true, transport,
      agentsToken: "synthetic-agents", matchingToken: "synthetic-pipeline" });
    const issue = async (role: AccountRole, email: string) => {
      const response = await app.inject({ method: "POST", url: `/auth/${role}/code`, headers: { origin }, payload: { email } });
      assert.equal(response.statusCode, 202, response.body);
      const receipt = response.json<EmailCodeReceipt>();
      const job = await store.claimMail();
      assert.ok(job);
      assert.equal(job.challenge_id, receipt.challenge_id);
      await store.finishMail(job.job_id, job.lease_token);
      return { email, code: job.code, challenge_id: receipt.challenge_id };
    };
    const candidateCode = await issue("candidate", " Candidate@Example.com ");
    const noConsent = await app.inject({ method: "POST", url: "/auth/candidate/verify", headers: { origin }, payload: candidateCode });
    assert.equal(noConsent.statusCode, 409);
    assert.equal(noConsent.json().detail, "consent_required");
    const verified = await app.inject({ method: "POST", url: "/auth/candidate/verify", headers: { origin },
      payload: { ...candidateCode, consent: { processing: true, version: "account-v1" } } });
    assert.equal(verified.statusCode, 200, verified.body);
    const candidate = verified.json<AuthenticatedSession>();
    assert.equal(candidate.account.email, "candidate@example.com");
    assert.equal(candidate.account.consent.processing, true);
    const candidateCookie = String(verified.headers["set-cookie"]).split(";")[0];
    assert.match(String(verified.headers["set-cookie"]), /HttpOnly/);
    assert.match(String(verified.headers["set-cookie"]), /SameSite=Lax/);
    assert.equal((await app.inject({ url: "/auth/candidate/me", headers: { cookie: candidateCookie } })).statusCode, 200);
    assert.equal((await app.inject({ url: "/auth/employer/me", headers: { authorization: `Bearer ${candidate.access_token}` } })).statusCode, 401);
    assert.equal((await app.inject({ url: "/auth/candidate/me", headers: { cookie: candidateCookie, authorization: "Bearer invalid" } })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: "/auth/candidate/verify", headers: { origin },
      payload: { ...candidateCode, consent: { processing: true, version: "account-v1" } } })).statusCode, 409);

    await pool.query("UPDATE auth_issue_events SET created_at=now()-interval '2 minutes'");
    const wrongRole = await app.inject({ method: "POST", url: "/auth/employer/code", headers: { origin },
      payload: { email: "candidate@example.com" } });
    assert.equal(wrongRole.statusCode, 409);
    assert.equal(wrongRole.json().detail, "role_mismatch");
    assert.equal((await pool.query("SELECT count(*) FROM accounts WHERE email='candidate@example.com'")).rows[0].count, "1");

    const employerCode = await issue("employer", "employer@example.com");
    const employerResult = await app.inject({ method: "POST", url: "/auth/employer/verify", headers: { origin },
      payload: { ...employerCode, consent: { processing: true, version: "account-v1" } } });
    assert.equal(employerResult.statusCode, 200, employerResult.body);
    const employer = employerResult.json<AuthenticatedSession>();
    const employerCookie = String(employerResult.headers["set-cookie"]).split(";")[0];
    assert.equal((await app.inject({ url: "/employer/vacancy-drafts", headers: { cookie: employerCookie, "x-owner-id": randomUUID() } })).statusCode, 200);
    assert.equal(forwarded.at(-1)?.owner, employer.account.id);
    const before = forwarded.length;
    assert.equal((await app.inject({ url: "/employer/vacancy-drafts", headers: { cookie: candidateCookie } })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancy-drafts", headers: { cookie: employerCookie,
      origin: "https://attacker.invalid" }, payload: { fields: {} } })).statusCode, 403);
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancy-drafts", headers: { cookie: employerCookie }, payload: { fields: {} } })).statusCode, 403);
    assert.equal(forwarded.length, before);
    const ownResume = await app.inject({ url: `/candidate/resume.pdf?candidate_id=${randomUUID()}`, headers: { cookie: candidateCookie } });
    assert.equal(ownResume.statusCode, 200, ownResume.body);
    assert.equal(forwarded.at(-1)?.pathname, `/internal/candidates/${candidate.account.id}/resume.pdf`);
    assert.equal((await app.inject({ url: "/candidate/resume.pdf", headers: { cookie: employerCookie } })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: "/auth/candidate/logout", headers: { cookie: candidateCookie } })).statusCode, 403);
    assert.equal((await app.inject({ method: "POST", url: "/auth/candidate/logout", headers: { cookie: candidateCookie, origin } })).statusCode, 200);
    assert.equal((await app.inject({ url: "/auth/candidate/me", headers: { authorization: `Bearer ${candidate.access_token}` } })).statusCode, 401);
    assert.equal((await app.inject({ url: "/auth/employer/me", headers: { cookie: employerCookie } })).statusCode, 200);
  } finally { await app.close(); await pool.end(); }
});
