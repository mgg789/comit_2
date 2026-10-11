/** Real authenticated FSP demo routes are development-only and cannot mutate production state. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { candidateVerificationRoutes } from "../src/routes/candidate-verification.js";

import { startPythonHttpFixture } from "./helpers/python-http-fixture.js";

const databaseUrl = process.env.VERIFICATION_ROUTE_TEST_DATABASE_URL ?? process.env.FSP_TEST_DATABASE_URL;

test("FSP demo requires explicit development opt-in and leaves disabled routes without durable writes", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false } } });
  const savedNodeEnv = process.env.NODE_ENV, savedDemo = process.env.FSP_DEMO_ENABLED;
  try {
    await migrate(pool);
    const secret = "synthetic-only-fsp-route-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39882", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret), profiles = new ProfileStore(pool);
    const email = `${randomUUID()}@example.test`;
    const challenge = await store.issue("candidate", email, "192.0.2.89");
    const mail = await store.claimMail();
    assert.ok(mail);
    await store.finishMail(mail.job_id, mail.lease_token);
    const verified = await store.verify("candidate", email, challenge.challenge_id, mail.code, "account-v1");
    const candidateId = verified.account.id;
    const headers = { authorization: `Bearer ${await signSession(config, "candidate", candidateId, verified.session_id, verified.expires_at)}` };
    const initial = await profiles.save(candidateId, { expected_revision: 0, fields: {},
      consent: { processing: true, pool: true, version: "profile-v1" } });
    let upstreamCalls = 0;
    const transport: typeof fetch = async () => { upstreamCalls += 1; throw new Error("disabled demo must not contact a provider"); };
    await app.register(cookie);
    await app.register(candidateVerificationRoutes, { store, config, profiles, mailConfigured: false,
      verificationTransport: transport, integrationToken: "synthetic-fsp-token" });
    const authorize = "/candidate/profile/fsp/demo/authorize", callback = "/candidate/profile/fsp/demo/callback";
    const payload = { expected_revision: initial.revision, demo_fixture_id: "fsp-001" };
    const stateRows = async () => (await pool.query("SELECT state_hash,used_at FROM candidate_fsp_demo_auth_states WHERE candidate_id=$1 ORDER BY state_hash", [candidateId])).rows;
    const unchanged = async () => {
      assert.deepEqual(await profiles.get(candidateId), initial);
      assert.equal(upstreamCalls, 0);
    };
    for (const [mode, enabled] of [["production", "true"], ["test", "true"], ["development", "false"], ["development", undefined]] as const) {
      process.env.NODE_ENV = mode;
      if (enabled === undefined) delete process.env.FSP_DEMO_ENABLED; else process.env.FSP_DEMO_ENABLED = enabled;
      for (const [url, body] of [[authorize, payload], [callback, { expected_revision: initial.revision, state: "s".repeat(32), code: "c".repeat(32) }], ["/candidate/profile/fsp/demo/approve", { expected_revision: initial.revision, state: "s".repeat(32), import_consent: true }]] as const) {
        const response = await app.inject({ method: "POST", url, headers, payload: body });
        assert.equal(response.statusCode, 503, response.body);
        assert.equal(response.json().detail, "fsp_demo_disabled");
      }
      assert.deepEqual(await stateRows(), []);
      await unchanged();
    }
    process.env.NODE_ENV = "development";
    process.env.FSP_DEMO_ENABLED = "true";
    const allowed = await app.inject({ method: "POST", url: authorize, headers, payload });
    assert.equal(allowed.statusCode, 200, allowed.body);
    assert.equal(allowed.json().demo, true);
    assert.equal(new URL(allowed.json().authorize_url).searchParams.get("state"), allowed.json().state);
    const before = await stateRows();
    assert.equal(before.length, 1);
    assert.equal(before[0].used_at, null);
    // A state created in development remains unconsumed if the mode changes before callback.
    process.env.NODE_ENV = "production";
    const blocked = await app.inject({ method: "POST", url: callback, headers,
      payload: { expected_revision: initial.revision, state: allowed.json().state, code: "c".repeat(32) } });
    assert.equal(blocked.statusCode, 503, blocked.body);
    assert.deepEqual(await stateRows(), before);
    await unchanged();
  } finally {
    if (savedNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = savedNodeEnv;
    if (savedDemo === undefined) delete process.env.FSP_DEMO_ENABLED; else process.env.FSP_DEMO_ENABLED = savedDemo;
    await app.close();
    await pool.end();
  }
});


test("same-origin FSP approval imports through real registry with owner, replay and revision fences", { skip: !databaseUrl }, async () => {
  const registry = await startPythonHttpFixture("integration-engine", { APP_ENV: "development", FSP_DEMO_ENABLED: "true", FSP_DEMO_INTERNAL_TOKEN: "synthetic-resume-fsp-token" });
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false } } });
  const savedNodeEnv = process.env.NODE_ENV, savedDemo = process.env.FSP_DEMO_ENABLED;
  try {
    await migrate(pool);
    process.env.NODE_ENV = "development"; process.env.FSP_DEMO_ENABLED = "true";
    const config = loadAuthConfig({ AUTH_SECRET: "synthetic-only-fsp-ui-secret-32bytes", AUTH_ALLOWED_ORIGINS: "http://localhost:39882", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, config.secret), profiles = new ProfileStore(pool);
    async function candidate() {
      const email = `${randomUUID()}@example.invalid`;
      const challenge = await store.issue("candidate", email, "192.0.2.99");
      const mail = await store.claimMail(); assert.ok(mail); await store.finishMail(mail.job_id, mail.lease_token);
      const account = await store.verify("candidate", email, challenge.challenge_id, mail.code, "account-v1");
      const token = await signSession(config, "candidate", account.account.id, account.session_id, account.expires_at);
      return { id: account.account.id, headers: { authorization: `Bearer ${token}`, origin: "http://localhost:39882" } };
    }
    const owner = await candidate(), other = await candidate();
    const initial = await profiles.save(owner.id, { expected_revision: 0, fields: { display_name: "Synthetic FSP UI" }, consent: { processing: true, pool: true, version: "profile-v1" } });
    await app.register(cookie);
    await app.register(candidateVerificationRoutes, { store, config, profiles, mailConfigured: false, integrationUrl: registry.url, integrationToken: "synthetic-resume-fsp-token" });
    const post = (path: string, payload: unknown, headers = owner.headers) => app.inject({ method: "POST", url: `/candidate/profile/fsp/demo/${path}`, headers, payload });
    const status = await app.inject({ url: "/candidate/profile/fsp/status", headers: { authorization: owner.headers.authorization } });
    assert.equal(status.statusCode, 200); assert.deepEqual(status.json(), { demo_available: true, real_available: false });
    assert.equal(status.headers["cache-control"], "no-store");
    const start = await post("authorize", { expected_revision: initial.revision, demo_fixture_id: "fsp-001" });
    assert.equal(start.statusCode, 200, start.body);
    const payload = { expected_revision: initial.revision, state: start.json().state, import_consent: true };
    assert.equal((await post("approve", { ...payload, import_consent: false })).statusCode, 400);
    assert.equal((await post("approve", payload, other.headers)).statusCode, 409);
    assert.equal((await post("approve", payload, { ...owner.headers, origin: "https://evil.invalid" })).statusCode, 403);
    const approved = await post("approve", payload);
    assert.equal(approved.statusCode, 200, approved.body);
    assert.equal(approved.json().fields.fsp_id, "fsp-001");
    assert.equal(approved.json().fields.achievements.length, 2);
    assert.equal(approved.json().revision, initial.revision + 1);
    assert.equal((await post("approve", payload)).statusCode, 409);
    assert.equal(JSON.stringify(approved.json()).includes("ownership_proven"), true);
    const repeated = await post("authorize", { expected_revision: approved.json().revision, demo_fixture_id: "fsp-001" });
    const again = await post("approve", { expected_revision: approved.json().revision, state: repeated.json().state, import_consent: true });
    assert.equal(again.statusCode, 200, again.body); assert.equal(again.json().fields.achievements.length, 2);
    assert.deepEqual(again.json().fields.achievements.map((item: { id: string }) => item.id), approved.json().fields.achievements.map((item: { id: string }) => item.id));
  } finally {
    if (savedNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = savedNodeEnv;
    if (savedDemo === undefined) delete process.env.FSP_DEMO_ENABLED; else process.env.FSP_DEMO_ENABLED = savedDemo;
    await app.close(); await pool.end(); await registry.close();
  }
});
