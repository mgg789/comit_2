/** Development delivery is explicit, role-bound and unavailable outside development. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { migrate } from "../src/auth/migrate.js";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore, type AccountRole, type ChallengeIssued } from "../src/auth/store.js";
import { authRoutes } from "../src/routes/auth.js";

const secret = "synthetic-only-auth-secret-at-least-32bytes";
const origin = "http://localhost:5173";
const settings = { AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: origin };

test("development delivery requires exact opt-in and rejects every other environment", () => {
  assert.equal(loadAuthConfig({ ...settings, NODE_ENV: "development" }).developmentCodeDelivery, false);
  assert.equal(loadAuthConfig({ ...settings, NODE_ENV: "development", DEV_AUTH_EXPOSE_CODE: "true" }).developmentCodeDelivery, true);
  for (const NODE_ENV of [undefined, "test", "production"]) {
    assert.throws(() => loadAuthConfig({ ...settings, NODE_ENV, DEV_AUTH_EXPOSE_CODE: "true" }), /auth_dev_code_forbidden/);
  }
  assert.equal(loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "https://example.com", NODE_ENV: "production" }).developmentCodeDelivery, false);
});

class ReceiptStore extends AuthStore {
  readonly calls: Array<{ role: AccountRole; developmentDelivery: boolean }> = [];
  override async issue(role: AccountRole, _email: string, _ip: string, developmentDelivery = false): Promise<ChallengeIssued> {
    this.calls.push({ role, developmentDelivery });
    return { challenge_id: "12345678-1234-4234-8234-123456789abc", expires_at: "2026-01-01T00:10:00Z",
      resend_after: "2026-01-01T00:01:00Z", dev_code: "012345" };
  }
}

test("both role routes expose a dev receipt without SMTP and strip codes in production", async () => {
  const previous = process.env.NODE_ENV;
  const pool = new Pool();
  try {
    for (const environment of ["development", "production"]) {
      process.env.NODE_ENV = environment;
      const development = environment === "development";
      const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: development ? origin : "https://example.com",
        NODE_ENV: environment, DEV_AUTH_EXPOSE_CODE: development ? "true" : "false" });
      const store = new ReceiptStore(pool, secret);
      const app = Fastify();
      await app.register(authRoutes, { config, store, mailConfigured: !development });
      try {
        for (const role of ["candidate", "employer"] as const) {
          const response = await app.inject({ method: "POST", url: `/auth/${role}/code`,
            headers: { origin: development ? origin : "https://example.com" }, payload: { email: "user@example.com" } });
          assert.equal(response.statusCode, 202);
          assert.equal(response.headers["cache-control"], "no-store");
          assert.equal(response.json().dev_code, development ? "012345" : undefined);
          assert.deepEqual(store.calls.at(-1), { role, developmentDelivery: development });
        }
        if (development) {
          process.env.NODE_ENV = "production";
          const response = await app.inject({ method: "POST", url: "/auth/candidate/code", headers: { origin }, payload: { email: "user@example.com" } });
          assert.equal(response.statusCode, 503);
          assert.equal(store.calls.length, 2);
        }
      } finally { await app.close(); }
    }
    process.env.NODE_ENV = "production";
    await assert.rejects(new AuthStore(pool, secret).issue("candidate", "user@example.com", "127.0.0.1", true), /auth_dev_code_forbidden/);
  } finally {
    if (previous === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previous;
    await pool.end();
  }
});


const databaseUrl = process.env.AUTH_TEST_DATABASE_URL;
test("dev delivery persists a real single-use challenge without an SMTP job", { skip: !databaseUrl }, async () => {
  const previous = process.env.NODE_ENV;
  process.env.NODE_ENV = "development";
  const pool = new Pool({ connectionString: databaseUrl });
  const email = `dev-${randomUUID()}@example.com`;
  try {
    await migrate(pool);
    const store = new AuthStore(pool, secret);
    const receipt = await store.issue("candidate", email, "192.0.2.15", true);
    assert.match(receipt.dev_code ?? "", /^[0-9]{6}$/);
    const jobs = await pool.query("SELECT id FROM auth_mail_outbox WHERE challenge_id = $1", [receipt.challenge_id]);
    assert.equal(jobs.rowCount, 0);
    const session = await store.verify("candidate", email, receipt.challenge_id, receipt.dev_code ?? "", "account-v1", "192.0.2.15");
    assert.equal(session.account.email, email);
    await assert.rejects(() => store.verify("candidate", email, receipt.challenge_id, receipt.dev_code ?? "", "account-v1", "192.0.2.15"));
  } finally {
    await pool.query("DELETE FROM accounts WHERE email = $1", [email]);
    await pool.query("DELETE FROM auth_challenges WHERE email = $1", [email]);
    await pool.query("DELETE FROM auth_issue_events WHERE email = $1", [email]);
    await pool.end();
    if (previous === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previous;
  }
});
