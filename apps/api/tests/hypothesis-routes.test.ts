/** Hypothesis controls exercise real account sessions and the generated matching gateway. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, HypothesisStatus, PreferenceCommand } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { accountRoutes } from "../src/routes/accounts.js";

const databaseUrl = process.env.HYPOTHESIS_ROUTE_TEST_DATABASE_URL;
const origin = "http://localhost:38088";
const secret = "synthetic-only-hypothesis-route-secret-32-bytes";
const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: origin, AUTH_COOKIE_SECURE: "false" });

test("hypothesis gateway scopes controls to the employer and preserves validation, CAS and replay", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts, auth_challenges, auth_issue_events, auth_verify_events RESTART IDENTITY CASCADE");
    const store = new AuthStore(pool, secret);
    const profiles = new ProfileStore(pool);
    const login = async (role: AccountRole, email: string) => {
      const receipt = await store.issue(role, email, "192.0.2.21");
      const mail = await store.claimMail();
      assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const verified = await store.verify(role, email, receipt.challenge_id, mail.code, "account-v1");
      return { id: verified.account.id, authorization: `Bearer ${await signSession(config, role, verified.account.id, verified.session_id, verified.expires_at)}` };
    };
    const employer = await login("employer", "hypothesis-owner@example.test");
    const foreign = await login("employer", "hypothesis-foreign@example.test");
    const candidate = await login("candidate", "hypothesis-candidate@example.test");
    const draftId = randomUUID();
    const vacancyId = randomUUID();
    const forwarded: Array<{ path: string; method: string; owner: string | null; body: unknown }> = [];
    let publicationAccepted = true;
    let returnedOwner = employer.id;
    let returnedDraft = draftId;
    let mutations = 0;
    const commands = new Map<string, string>();
    const status: HypothesisStatus = {
      vacancy_id: vacancyId, revision: 4, enabled: true, context_hash: "context-test",
      policy_hash: "hypothesis-policy-test", pairs: 2,
      search_report: { scope: "bounded_current_pool", exhaustive: false },
      hypotheses: [{ hypothesis_id: "H1", status: "unclear", supporting: 1, opposing: 0,
        decisive: 1, total: 2, observed_decisive: 1, post_batch_decisive: 0, batch_status: "collecting",
        confidence_interval: [0.01, 0.99], minimum_decisive_pairs: 20, familywise_alpha: 0.05,
        multiple_testing_families: 10, policy_hash: "hypothesis-policy-test", calibration_status: "uncalibrated_baseline" }],
    };
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      const owner = new Headers(init?.headers).get("x-owner-id");
      const method = init?.method ?? "GET";
      const body: unknown = init?.body ? JSON.parse(String(init.body)) : null;
      forwarded.push({ path: url.pathname, method, owner, body });
      if (url.pathname.startsWith("/internal/vacancy-drafts/")) {
        if (owner !== employer.id || url.pathname !== `/internal/vacancy-drafts/${draftId}`) {
          return Response.json({ detail: "owner_record_not_found" }, { status: 404 });
        }
        return Response.json({ draft_id: returnedDraft, vacancy_id: vacancyId, owner_id: returnedOwner,
          revision: 1, state: "created", business_status: "open", archived: false, fields: {}, publication: { status: publicationAccepted ? "accepted" : "queued", source_revision: 1 } });
      }
      if (url.pathname.startsWith(`/internal/vacancies/${vacancyId}/hypotheses`)) {
        assert.equal(owner, employer.id);
        if (method === "POST") {
          const command = body as PreferenceCommand;
          const action = url.pathname.split("/").at(-1)!;
          const previous = commands.get(command.idempotency_key);
          if (previous && previous !== action) return Response.json({ detail: "hypothesis command key belongs to another action" }, { status: 409 });
          if (!previous) {
            if (command.expected_revision !== status.revision) return Response.json({ detail: "hypothesis revision conflict" }, { status: 409 });
            commands.set(command.idempotency_key, action);
            status.revision += 1;
            status.enabled = action !== "pause";
            mutations += 1;
          }
        }
        return Response.json(status);
      }
      return Response.json({ detail: "not_found" }, { status: 404 });
    };
    await app.register(cookie);
    await app.register(accountRoutes, { store, config, profiles, mailConfigured: false,
      agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport });
    await app.ready();
    const path = `/employer/vacancy-drafts/${draftId}/hypotheses`;
    const employerHeaders = { authorization: employer.authorization, "x-owner-id": foreign.id };
    assert.equal((await app.inject({ url: path })).statusCode, 401);
    assert.equal((await app.inject({ url: path, headers: { authorization: candidate.authorization } })).statusCode, 401);
    assert.equal((await app.inject({ url: path, headers: { authorization: foreign.authorization } })).statusCode, 404);
    assert.equal((await app.inject({ url: `/employer/vacancy-drafts/${randomUUID()}/hypotheses`, headers: employerHeaders })).statusCode, 404);
    assert.equal((await app.inject({ url: `/employer/vacancy-drafts/not-a-uuid/hypotheses`, headers: employerHeaders })).statusCode, 400);

    const read = await app.inject({ url: path, headers: employerHeaders });
    assert.equal(read.statusCode, 200, read.body);
    assert.deepEqual(read.json<HypothesisStatus>(), status);
    assert.equal(read.headers["cache-control"], "no-store");
    assert.equal(forwarded.at(-1)?.owner, employer.id);
    assert.equal(forwarded.at(-1)?.path, `/internal/vacancies/${vacancyId}/hypotheses`);

    for (const action of ["pause", "resume", "reset"] as const) {
      const command = { idempotency_key: randomUUID(), expected_revision: status.revision };
      const response = await app.inject({ method: "POST", url: `${path}/${action}`, headers: { ...employerHeaders, origin }, payload: command });
      assert.equal(response.statusCode, 200, `${action}: ${response.body}`);
      assert.equal(response.json<HypothesisStatus>().enabled, action !== "pause");
      assert.equal(forwarded.at(-1)?.path, `/internal/vacancies/${vacancyId}/hypotheses/${action}`);
      assert.equal(forwarded.at(-1)?.method, "POST");
      assert.equal(forwarded.at(-1)?.owner, employer.id);
      assert.deepEqual(forwarded.at(-1)?.body, command);
      const replay = await app.inject({ method: "POST", url: `${path}/${action}`, headers: employerHeaders, payload: command });
      assert.equal(replay.statusCode, 200, replay.body);
      assert.deepEqual(replay.json<HypothesisStatus>(), response.json<HypothesisStatus>());
      const collision = await app.inject({ method: "POST", url: `${path}/${action === "pause" ? "resume" : "pause"}`, headers: employerHeaders, payload: command });
      assert.equal(collision.statusCode, 409, collision.body);
    }
    assert.equal(mutations, 3);
    const stale = await app.inject({ method: "POST", url: `${path}/pause`, headers: employerHeaders,
      payload: { idempotency_key: randomUUID(), expected_revision: 0 } });
    assert.equal(stale.statusCode, 409, stale.body);
    assert.equal(stale.json<{ detail: string }>().detail, "hypothesis revision conflict");

    for (const payload of [
      { idempotency_key: randomUUID(), expected_revision: status.revision, owner_id: foreign.id },
      { idempotency_key: "not-a-uuid", expected_revision: status.revision },
      { idempotency_key: randomUUID(), expected_revision: -1 },
      { idempotency_key: randomUUID(), expected_revision: 1.5 },
      { idempotency_key: randomUUID(), expected_revision: "7" },
      { idempotency_key: randomUUID() },
    ]) {
      const before = forwarded.length;
      const invalid = await app.inject({ method: "POST", url: `${path}/pause`, headers: employerHeaders, payload });
      assert.equal(invalid.statusCode, 400, invalid.body);
      assert.equal(forwarded.length, before, "invalid commands must never reach matching");
    }
    const command = { idempotency_key: randomUUID(), expected_revision: status.revision };
    const beforeInvalidAction = forwarded.length;
    const invalidAction = await app.inject({ method: "POST", url: `${path}/retry`, headers: employerHeaders, payload: command });
    assert.equal(invalidAction.statusCode, 400, invalidAction.body);
    assert.equal(forwarded.length, beforeInvalidAction, "unknown hypothesis actions must never reach matching");
    for (const headers of [{}, { authorization: candidate.authorization }, { authorization: foreign.authorization }]) {
      const rejected = await app.inject({ method: "POST", url: `${path}/pause`, headers, payload: command });
      assert.equal(rejected.statusCode, "authorization" in headers && headers.authorization === foreign.authorization ? 404 : 401);
    }
    const beforeUnpublished = forwarded.length;
    publicationAccepted = false;
    assert.equal((await app.inject({ method: "POST", url: `${path}/pause`, headers: employerHeaders, payload: command })).statusCode, 409);
    assert.equal(forwarded.length, beforeUnpublished + 1, "unpublished drafts must never reach matching");
    publicationAccepted = true;
    returnedOwner = foreign.id;
    assert.equal((await app.inject({ url: path, headers: employerHeaders })).statusCode, 404);
    returnedOwner = employer.id;
    returnedDraft = randomUUID();
    assert.equal((await app.inject({ url: path, headers: employerHeaders })).statusCode, 404);
    assert.equal(mutations, 3);
  } finally {
    await app.close();
    await pool.end();
  }
});
