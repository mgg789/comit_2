/** Production account gateway/JWT checks with synthetic session and HTTP boundaries; no database calls. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore } from "../src/auth/store.js";
import { cookieName, signSession } from "../src/auth/session.js";
import { accountRoutes } from "../src/routes/accounts.js";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";

test("chat clear proxy derives owner from the session, validates CAS and enforces browser origin", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  const pool = new Pool({ connectionString: "postgresql://synthetic-unused@127.0.0.1:1/synthetic" });
  const owner = randomUUID(), foreign = randomUUID(), sid = "a".repeat(43);
  const now = new Date().toISOString();
  const config = loadAuthConfig({ AUTH_SECRET: "synthetic-clear-secret-with-32-characters",
    AUTH_ALLOWED_ORIGINS: "http://localhost:39580", AUTH_COOKIE_SECURE: "false" });
  const store = new AuthStore(pool, config.secret);
  store.session = async (role, id, sessionId) => role === "employer" && [owner, foreign].includes(id) && sessionId === sid
    ? { id, email: `${id}@example.test`, role, email_verified: true, created_at: now,
      consent: { processing: true, version: "synthetic-v1", accepted_at: now } } : null;
  const forwarded: string[] = [];
  await app.register(cookie);
  await app.register(accountRoutes, { config, store, mailConfigured: false, agentsToken: "synthetic-service-token",
    transport: async (input, init) => {
      assert.equal(new URL(String(input)).pathname, "/internal/organization-agent/messages");
      assert.equal(init?.method, "DELETE");
      assert.equal(new Headers(init?.headers).get("x-internal-token"), "synthetic-service-token");
      const currentOwner = new Headers(init?.headers).get("x-owner-id");
      assert.ok(currentOwner);
      forwarded.push(currentOwner);
      const body = JSON.parse(String(init?.body)) as { expected_context_revision: number };
      if (body.expected_context_revision === 9) return Response.json({ detail: "organization_chat_busy" }, { status: 409 });
      return Response.json({ owner_id: currentOwner, current_summary: {}, revision: body.expected_context_revision + 1 });
    } });
  try {
    const url = "/employer/organization-agent/messages";
    const token = await signSession(config, "employer", owner, sid, new Date(Date.now() + 60_000).toISOString());
    const otherToken = await signSession(config, "employer", foreign, sid, new Date(Date.now() + 60_000).toISOString());
    const headers = { authorization: `Bearer ${token}`, "x-owner-id": foreign };
    assert.equal((await app.inject({ method: "DELETE", url, payload: { expected_context_revision: 7 } })).statusCode, 401);
    assert.equal((await app.inject({ method: "DELETE", url, headers, payload: {} })).statusCode, 400);
    assert.equal((await app.inject({ method: "DELETE", url, headers, payload: { expected_context_revision: 7, owner_id: foreign } })).statusCode, 400);
    assert.equal((await app.inject({ method: "DELETE", url, headers, payload: { expected_context_revision: "7" } })).statusCode, 400);
    const cleared = await app.inject({ method: "DELETE", url, headers, payload: { expected_context_revision: 7 } });
    assert.equal(cleared.statusCode, 200, cleared.body);
    assert.equal(cleared.json().owner_id, owner);
    assert.equal(cleared.json().revision, 8);
    assert.equal(cleared.headers["cache-control"], "no-store");
    const busy = await app.inject({ method: "DELETE", url, headers, payload: { expected_context_revision: 9 } });
    assert.equal(busy.statusCode, 409);
    assert.equal(busy.json().detail, "organization_chat_busy");
    const other = await app.inject({ method: "DELETE", url, headers: { authorization: `Bearer ${otherToken}`, "x-owner-id": owner },
      payload: { expected_context_revision: 7 } });
    assert.equal(other.json().owner_id, foreign);
    const browserHeaders = { cookie: `${cookieName("employer", config)}=${token}` };
    assert.equal((await app.inject({ method: "DELETE", url, headers: browserHeaders, payload: { expected_context_revision: 7 } })).statusCode, 403);
    assert.equal((await app.inject({ method: "DELETE", url, headers: { ...browserHeaders, origin: "https://foreign.example" }, payload: { expected_context_revision: 7 } })).statusCode, 403);
    assert.equal((await app.inject({ method: "DELETE", url, headers: { ...browserHeaders, origin: "http://localhost:39580" }, payload: { expected_context_revision: 7 } })).statusCode, 200);
    assert.deepEqual(forwarded, [owner, owner, foreign, owner]);
  } finally {
    await app.close();
    await pool.end();
  }
});
