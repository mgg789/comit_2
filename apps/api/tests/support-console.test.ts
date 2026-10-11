/** Operator login gate, conversation directory and Comit replies over the durable inbox. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import type { SupportConversationPage, SupportMessagePage, SupportSessionView, SystemNotification } from "@comit/shared";
import { loadSupportConfig } from "../src/support/config.js";
import { SupportConsoleStore } from "../src/support/store.js";
import { supportRoutes } from "../src/routes/support.js";
import { SystemNotificationsStore } from "../src/notifications/system-store.js";
import { loadAuthConfig } from "../src/auth/config.js";

const supportConfig = loadSupportConfig({ SUPPORT_EMAIL: " Operator@Example.test ", SUPPORT_ACCESS_KEY: "synthetic-access-key-123456" });
if (!supportConfig) throw new Error("synthetic support configuration must load");
const origin = { origin: "http://localhost:39581" };

const databaseUrl = process.env.SUPPORT_TEST_DATABASE_URL;
test("support console: constant-time login, per-conversation unread and replies as Comit", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    // This env must identify a disposable database: never reuse a demo or production URL.
    assert.match(new URL(databaseUrl!).pathname, /^\/(?:comit_support_test|hiring_test_[0-9]+_[a-f0-9]+)$/);
    await pool.query("DROP SCHEMA public CASCADE; CREATE SCHEMA public");
    for (const name of ["001_accounts.sql", "002_candidate_profiles.sql", "003_recruitment.sql",
      "014_employer_system_inbox.sql", "021_support_console.sql"])
      await pool.query(await readFile(new URL(`../migrations/${name}`, import.meta.url), "utf8"));
    const config = loadAuthConfig({ AUTH_SECRET: "synthetic-support-auth-secret-32-bytes", AUTH_COOKIE_SECURE: "false", AUTH_ALLOWED_ORIGINS: origin.origin });
    const notifications = new SystemNotificationsStore(pool);
    const employer = randomUUID(), candidate = randomUUID();
    for (const [id, role] of [[employer, "employer"], [candidate, "candidate"]] as const) {
      await pool.query("INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at) VALUES($1,$2,$3,true,'synthetic',now())", [id, `${id}@example.test`, role]);
      await notifications.publish(id, `seed:${id}`, "system.welcome", "Добро пожаловать в Comit!");
    }
    await app.register(cookie);
    await app.register(supportRoutes, { config, support: supportConfig,
      sessions: new SupportConsoleStore(pool), notifications });

    // Login is Origin-bound, rejects wrong credentials and sets the session cookie.
    assert.equal((await app.inject({ method: "POST", url: "/support/login", payload: { email: "operator@example.test", access_key: "synthetic-access-key-123456" } })).statusCode, 403);
    assert.equal((await app.inject({ method: "POST", url: "/support/login", headers: { origin: "https://foreign.example.test" }, payload: { email: "operator@example.test", access_key: "synthetic-access-key-123456" } })).statusCode, 403);
    for (const body of [
      { email: "operator@example.test", access_key: "wrong-key-wrong-key" },
      { email: "someone@example.test", access_key: "synthetic-access-key-123456" },
    ]) assert.equal((await app.inject({ method: "POST", url: "/support/login", headers: origin, payload: body })).statusCode, 401);
    const login = await app.inject({ method: "POST", url: "/support/login", headers: origin,
      payload: { email: "operator@example.test", access_key: "synthetic-access-key-123456" } });
    assert.equal(login.statusCode, 200);
    assert.equal(login.json<SupportSessionView>().operator.email, "operator@example.test");
    const cookieHeader = `comit_support_session=${login.cookies.find(item => item.name === "comit_support_session")!.value}`;
    assert.equal((await app.inject({ url: "/support/me" })).statusCode, 401);
    assert.equal((await app.inject({ url: "/support/me", headers: { cookie: "comit_support_session=forged-token-value" } })).statusCode, 401);
    assert.equal((await app.inject({ url: "/support/me", headers: { cookie: cookieHeader, ...origin } })).statusCode, 200);

    // The directory lists both seeded conversations; nothing is unread before a user writes.
    const list = await app.inject({ url: "/support/conversations", headers: { cookie: cookieHeader, ...origin } });
    assert.equal(list.statusCode, 200);
    const page = list.json<SupportConversationPage>();
    assert.equal(page.items.length, 2); assert.equal(page.has_more, false);
    assert.equal(page.items.every(item => item.unread_count === 0), true);

    // A user message appears and raises the operator unread counter.
    await notifications.sendMessage("employer", employer, `user:${randomUUID()}`, "Нужна помощь с вакансией");
    const afterUser = (await app.inject({ url: "/support/conversations", headers: { cookie: cookieHeader, ...origin } })).json<SupportConversationPage>();
    const owner = afterUser.items.find(item => item.account_id === employer)!;
    assert.equal(owner.unread_count, 1); assert.equal(owner.last_text, "Нужна помощь с вакансией");

    const history = await app.inject({ url: `/support/conversations/${employer}/messages`, headers: { cookie: cookieHeader, ...origin } });
    assert.equal(history.statusCode, 200);
    assert.equal(history.json<SupportMessagePage>().items.at(-1)?.type, "support.user_message");

    // Reply as Comit lands in the user conversation with a Comit reply type.
    const replyKey = randomUUID();
    const reply = await app.inject({ method: "POST", url: `/support/conversations/${employer}/messages`, headers: { cookie: cookieHeader, ...origin },
      payload: { idempotency_key: replyKey, text: "Здравствуйте! Разбираемся." } });
    assert.equal(reply.statusCode, 200);
    assert.equal(reply.json<SystemNotification>().type, "support.reply");
    const replay = await app.inject({ method: "POST", url: `/support/conversations/${employer}/messages`, headers: { cookie: cookieHeader, ...origin },
      payload: { idempotency_key: replyKey, text: "Здравствуйте! Разбираемся." } });
    assert.equal(replay.json<SystemNotification>().id, reply.json<SystemNotification>().id);
    const userUnread = await pool.query("SELECT count(*)::int AS unread FROM system_notifications WHERE account_id=$1 AND sequence>(SELECT read_sequence FROM system_inboxes WHERE account_id=$1)", [employer]);
    assert.equal(userUnread.rows[0]!.unread, 1);
    assert.equal((await app.inject({ method: "POST", url: `/support/conversations/${employer}/messages`, headers: { cookie: cookieHeader, ...origin },
      payload: { idempotency_key: randomUUID(), text: "x".repeat(2001) } })).statusCode, 400);
    assert.equal((await app.inject({ method: "POST", url: `/support/conversations/${randomUUID()}/messages`, headers: { cookie: cookieHeader, ...origin },
      payload: { idempotency_key: randomUUID(), text: "Кому?" } })).statusCode, 404);

    // Read receipt is monotonic and counts only user messages.
    const read = await app.inject({ method: "POST", url: `/support/conversations/${employer}/read`, headers: { cookie: cookieHeader, ...origin },
      payload: { read_sequence: reply.json<SystemNotification>().sequence } });
    assert.equal(read.statusCode, 200); assert.equal(read.json<{ unread_count: number }>().unread_count, 0);
    assert.equal((await app.inject({ method: "POST", url: `/support/conversations/${employer}/read`, headers: { cookie: cookieHeader, ...origin },
      payload: { read_sequence: 1 } })).json<{ unread_count: number }>().unread_count, 0);

    // Logout revokes the session server-side; replaying the cookie fails closed.
    assert.equal((await app.inject({ method: "POST", url: "/support/logout", headers: { cookie: cookieHeader, ...origin } })).statusCode, 200);
    assert.equal((await app.inject({ url: "/support/me", headers: { cookie: cookieHeader, ...origin } })).statusCode, 401);
  } finally { await app.close(); await pool.end(); }
});
