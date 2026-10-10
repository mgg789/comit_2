/** Preference gateway checks use an isolated HTTP transport and the real account session boundary. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, PreferenceStatus } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { accountRoutes } from "../src/routes/accounts.js";

const databaseUrl = process.env.PREFERENCE_ROUTE_TEST_DATABASE_URL ?? process.env.AUTH_ROUTE_TEST_DATABASE_URL;
const origin = "http://localhost:38088";
const secret = "synthetic-only-preference-route-secret-32-bytes";
const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: origin, AUTH_COOKIE_SECURE: "false" });

test("preference gateway enforces employer auth, draft ownership, forwarding and command schema", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts, auth_challenges, auth_issue_events, auth_verify_events RESTART IDENTITY CASCADE");
    const store = new AuthStore(pool, secret);
    const profiles = new ProfileStore(pool);
    const login = async (role: AccountRole, email: string) => {
      const receipt = await store.issue(role, email, "192.0.2.20");
      const mail = await store.claimMail();
      assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const verified = await store.verify(role, email, receipt.challenge_id, mail.code, "account-v1");
      return { id: verified.account.id, authorization: `Bearer ${await signSession(config, role, verified.account.id, verified.session_id, verified.expires_at)}` };
    };
    const employer = await login("employer", "preference-owner@example.test");
    const foreign = await login("employer", "preference-foreign@example.test");
    const candidate = await login("candidate", "preference-candidate@example.test");
    const draftId = randomUUID();
    const vacancyId = randomUUID();
    const forwarded: Array<{ path: string; method: string; owner: string | null; body: unknown }> = [];
    const status: PreferenceStatus = {
      vacancy_id: vacancyId, revision: 4, enabled: true, state: "collecting", active_model_hash: null,
      eligible_reactions: 12, fresh_reactions: 2, job_id: null, job_state: null, error_code: null,
      policy_hash: "preference-policy-test", selection_bias: "deterministic_feedback_unknown_propensity",
    };
    let published = true;
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      const owner = new Headers(init?.headers).get("x-owner-id");
      forwarded.push({ path: url.pathname, method: init?.method ?? "GET", owner, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (url.pathname.startsWith("/internal/vacancy-drafts/")) {
        if (owner !== employer.id) return Response.json({ detail: "owner_record_not_found" }, { status: 404 });
        return Response.json({ draft_id: draftId, vacancy_id: vacancyId, owner_id: employer.id, revision: 1,
          state: "created", business_status: published ? "open" : "draft", archived: false, fields: {}, publication: { status: "accepted", source_revision: 1 } });
      }
      if (url.pathname.startsWith(`/internal/vacancies/${vacancyId}/preferences`)) return Response.json(status);
      return Response.json({ detail: "not_found" }, { status: 404 });
    };
    await app.register(cookie);
    await app.register(accountRoutes, { store, config, profiles, mailConfigured: false,
      agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport });
    await app.ready();
    const path = `/employer/vacancy-drafts/${draftId}/preferences`;
    assert.equal((await app.inject({ url: path })).statusCode, 401);
    assert.equal((await app.inject({ url: path, headers: { authorization: candidate.authorization } })).statusCode, 401);
    assert.equal((await app.inject({ url: `/employer/vacancy-drafts/${randomUUID()}/preferences`, headers: { authorization: foreign.authorization } })).statusCode, 404);
    assert.equal((await app.inject({ url: `/employer/vacancy-drafts/not-a-uuid/preferences`, headers: { authorization: employer.authorization } })).statusCode, 400);

    const employerHeaders = { authorization: employer.authorization, "x-owner-id": foreign.id };
    const read = await app.inject({ url: path, headers: employerHeaders });
    assert.equal(read.statusCode, 200, read.body);
    assert.deepEqual(read.json<PreferenceStatus>(), status);
    assert.equal(forwarded.at(-1)?.owner, employer.id);
    assert.equal(forwarded.at(-1)?.path, `/internal/vacancies/${vacancyId}/preferences`);

    published = false;
    const beforeUnpublished = forwarded.length;
    const unavailable = await app.inject({ url: path, headers: employerHeaders });
    assert.equal(unavailable.statusCode, 409, unavailable.body);
    assert.equal(unavailable.json().detail, "vacancy_not_published");
    assert.equal(forwarded.length, beforeUnpublished + 1, "unpublished source must not reach matching");
    published = true;

    const command = { idempotency_key: randomUUID(), expected_revision: status.revision };
    for (const action of ["retry", "reset", "rollback"] as const) {
      const response = await app.inject({ method: "POST", url: `${path}/${action}`, headers: { ...employerHeaders, origin }, payload: command });
      assert.equal(response.statusCode, 200, `${action}: ${response.body}`);
      assert.equal(forwarded.at(-1)?.owner, employer.id);
      assert.deepEqual(forwarded.at(-1)?.body, command);
    }
    const invalidCommand = await app.inject({ method: "POST", url: `${path}/retry`, headers: { ...employerHeaders, origin },
      payload: { ...command, owner_id: foreign.id } });
    assert.equal(invalidCommand.statusCode, 400, invalidCommand.body);
    // Bearer clients are allowed to omit Origin; the owner still comes from the verified session.
    const bearerWithoutOrigin = await app.inject({ method: "POST", url: `${path}/reset`, headers: { authorization: employer.authorization }, payload: command });
    assert.equal(bearerWithoutOrigin.statusCode, 200, bearerWithoutOrigin.body);
  } finally {
    await app.close();
    await pool.end();
  }
});
