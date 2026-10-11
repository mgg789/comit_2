/** OAuth acceptance uses synthetic provider responses; no live Yandex credentials or email. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore, type AccountRole, type VerificationResult } from "../src/auth/store.js";
import { authRoutes } from "../src/routes/auth.js";
import { yandexIdentity } from "../src/auth/yandex.js";
import { verificationHtml, mailDigits } from "../src/auth/email-template.js";
import { migrate } from "../src/auth/migrate.js";

const env = { AUTH_SECRET: "synthetic-only-auth-secret-at-least-32bytes", NODE_ENV: "production",
  AUTH_ALLOWED_ORIGINS: "https://comit.example.test", YANDEX_OAUTH_ENABLED: "true",
  YANDEX_OAUTH_CLIENT_ID: "synthetic-client", YANDEX_OAUTH_CLIENT_SECRET: "synthetic-secret",
  YANDEX_OAUTH_SITE_ORIGIN: "https://comit.example.test" };
const config = loadAuthConfig(env);

class OAuthStore extends AuthStore {
  readonly pending = new Set<string>();
  readonly logins: string[] = [];
  override async issueOAuthState(role: AccountRole, state: string): Promise<void> { this.pending.add(`${role}:${state}`); }
  override async consumeOAuthState(role: AccountRole, state: string): Promise<boolean> { return this.pending.delete(`${role}:${state}`); }
  override async loginYandex(role: AccountRole, subject: string, email: string): Promise<VerificationResult> {
    this.logins.push(`${role}:${subject}:${email}`);
    return { account: { id: "12345678-1234-4234-8234-123456789abc", email, role, email_verified: true,
      created_at: "2030-01-01T00:00:00Z", consent: { processing: true, version: "account-v1", accepted_at: "2030-01-01T00:00:00Z" } },
      session_id: "s".repeat(43), expires_at: "2030-01-02T00:00:00Z" };
  }
}

const provider: typeof fetch = async (input, init) => {
  if (String(input).endsWith("/token")) {
    assert.equal(init?.method, "POST");
    assert.equal(init?.redirect, "error");
    assert.match(String(init?.body), /grant_type=authorization_code/);
    return Response.json({ access_token: "synthetic-provider-token" });
  }
  assert.equal(new Headers(init?.headers).get("authorization"), "OAuth synthetic-provider-token");
  return Response.json({ id: "123456", client_id: env.YANDEX_OAUTH_CLIENT_ID, default_email: "synthetic@example.test" });
};

test("provider readiness requires explicit production opt-in and trusted HTTPS origin", () => {
  assert.ok(config.yandex);
  for (const overrides of [{ NODE_ENV: "development" }, { YANDEX_OAUTH_ENABLED: "false" },
    { YANDEX_OAUTH_CLIENT_SECRET: "" }, { YANDEX_OAUTH_SITE_ORIGIN: "https://untrusted.example.test" }]) {
    assert.equal(loadAuthConfig({ ...env, ...overrides }).yandex, undefined);
  }
});

test("OAuth requires consent, trusted origin and one-time state bound to the same browser and role", async () => {
  const pool = new Pool();
  const store = new OAuthStore(pool, config.secret);
  const app = Fastify();
  try {
    await app.register(cookie);
    await app.register(authRoutes, { config, store, mailConfigured: false, oauthTransport: provider });
    assert.deepEqual((await app.inject("/auth/candidate/providers")).json(), { yandex: true });
    const consent = { consent: { processing: true, version: "account-v1" } };
    assert.equal((await app.inject({ method: "POST", url: "/auth/candidate/yandex/start", payload: consent })).statusCode, 403);
    assert.equal((await app.inject({ method: "POST", url: "/auth/candidate/yandex/start", headers: { origin: env.AUTH_ALLOWED_ORIGINS }, payload: {} })).statusCode, 422);
    const start = await app.inject({ method: "POST", url: "/auth/candidate/yandex/start", headers: { origin: env.AUTH_ALLOWED_ORIGINS }, payload: consent });
    assert.equal(start.statusCode, 200);
    const url = new URL(start.json().authorization_url);
    assert.equal(url.searchParams.get("redirect_uri"), "https://comit.example.test/api/auth/candidate/yandex/callback");
    const state = url.searchParams.get("state")!;
    const browserCookie = start.cookies.map(value => `${value.name}=${value.value}`).join("; ");
    const callback = `/auth/candidate/yandex/callback?code=synthetic&state=${state}`;
    assert.match((await app.inject(callback)).headers.location ?? "", /oauth_state_invalid/);
    assert.match((await app.inject({ url: callback.replace("candidate", "employer"), headers: { cookie: browserCookie } })).headers.location ?? "", /oauth_state_invalid/);
    const finish = await app.inject({ url: callback, headers: { cookie: browserCookie } });
    assert.equal(finish.statusCode, 302);
    assert.equal(finish.headers.location, "https://comit.example.test/candidate/");
    assert.ok(finish.cookies.some(value => value.name === "__Host-comit_candidate_session" && value.httpOnly && value.secure));
    assert.deepEqual(store.logins, ["candidate:123456:synthetic@example.test"]);
    assert.match((await app.inject({ url: callback, headers: { cookie: browserCookie } })).headers.location ?? "", /oauth_state_invalid/);
    assert.equal(store.logins.length, 1);
  } finally { await app.close(); await pool.end(); }
});

test("provider identity cannot come from another OAuth client or an unavailable provider", async () => {
  assert.deepEqual(await yandexIdentity(config.yandex!, "candidate", "code", provider), { subject: "123456", email: "synthetic@example.test" });
  await assert.rejects(yandexIdentity(config.yandex!, "candidate", "code", async () => Response.json({ access_token: "t", id: "123", default_email: "synthetic@example.test", client_id: "wrong-client" })), /oauth_provider_unavailable/);
  await assert.rejects(yandexIdentity(config.yandex!, "candidate", "code", async () => { throw new Error("private provider error"); }), /oauth_provider_unavailable/);
});

test("mail code uses bundled Moon Walk PNG glyphs including leading zeroes and whole-code fallback", () => {
  const digits = mailDigits("001122");
  assert.equal(digits.attachments.length, 3);
  assert.equal((digits.html.match(/cid:comit-digit-0/g) ?? []).length, 2);
  assert.throws(() => mailDigits("<script>"));
  const rendered = verificationHtml({ code: "001122", email: "synthetic@example.test", role: "candidate", challenge_id: "x", expires_at: "2030-01-01" }, "12:45 UTC");
  assert.match(rendered.html, /aria-label="001122"/);
  assert.match(rendered.html, /alt="Comit"/);
  assert.equal(rendered.attachments[0]?.filename, "comit-wordmark.png");
  for (const asset of rendered.attachments) assert.ok(rendered.html.includes(`cid:${asset.cid}`));
});

const databaseUrl = process.env.AUTH_ROUTE_TEST_DATABASE_URL;
test("durable OAuth state redeems once and identities never auto-link an existing email", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const store = new AuthStore(pool, config.secret);
  const suffix = Date.now().toString();
  const email = `oauth-${suffix}@example.test`;
  const subject = suffix;
  const state = "o".repeat(43);
  try {
    await migrate(pool);
    await store.issueOAuthState("candidate", state, "account-v1");
    assert.equal(await store.consumeOAuthState("employer", state), false);
    assert.equal(await store.consumeOAuthState("candidate", state), true);
    assert.equal(await store.consumeOAuthState("candidate", state), false);
    const first = await store.loginYandex("candidate", subject, email);
    const second = await store.loginYandex("candidate", subject, `changed-${suffix}@example.test`);
    assert.equal(second.account.id, first.account.id);
    await assert.rejects(store.loginYandex("employer", subject, email), /role_mismatch/);
    await assert.rejects(store.loginYandex("candidate", subject + "1", email), /oauth_email_conflict/);
    assert.ok(await store.session("candidate", first.account.id, first.session_id));
  } finally {
    await pool.query("DELETE FROM accounts WHERE email=$1", [email]);
    await pool.end();
  }
});
