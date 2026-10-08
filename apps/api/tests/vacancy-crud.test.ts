/** Owner-derived direct management routes reuse generated agents contracts and verified sessions. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import type { AccountRole, AgentsDraftView, VacancyReady } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { accountRoutes } from "../src/routes/accounts.js";
import { installOperations } from "../src/operations/index.js";

const databaseUrl = process.env.VACANCY_CRUD_TEST_DATABASE_URL;
const secret = "synthetic-only-vacancy-crud-secret-32-bytes";
const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:38088", AUTH_COOKIE_SECURE: "false" });

test("direct vacancy adapters enforce owner/schema, preserve cursor and require owned open source for filtered discovery", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts, auth_challenges, auth_issue_events, auth_verify_events RESTART IDENTITY CASCADE");
    const store = new AuthStore(pool, secret), profiles = new ProfileStore(pool);
    const login = async (role: AccountRole, email: string) => {
      const issued = await store.issue(role, email, "192.0.2.70");
      const mail = await store.claimMail(); assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const result = await store.verify(role, email, issued.challenge_id, mail.code, "account-v1");
      return { id: result.account.id, authorization: `Bearer ${await signSession(config, role, result.account.id, result.session_id, result.expires_at)}` };
    };
    const owner = await login("employer", "vacancy-crud-owner@example.test"), other = await login("employer", "vacancy-crud-other@example.test"), candidate = await login("candidate", "vacancy-crud-candidate@example.test");
    const id = randomUUID();
    const draft: AgentsDraftView = { draft_id: id, vacancy_id: id, owner_id: owner.id, revision: 1, state: "created",
      fields: {}, publication: { status: "accepted", source_revision: 1 }, business_status: "open", archived: false };
    const ready: VacancyReady = { vacancy_id: id, owner_id: owner.id, revision: 1, source_revision: 1, status: "open",
      zp_min: 1, zp_max: 2, daily: "5/2", city: "Москва", type: "remote", bio: "", domens: {}, stacks: {}, soft_skills: {},
      metadata: {}, class: "backend", build_mode: "class_only", strict_vector: [0], requirement_mask: [false],
      strict_schema_version: 1, strict_schema_hash: "a".repeat(64), semantic_vector: null, semantic_model_version: null,
      semantic_space_id: null, semantic_available: false, published_at: "2026-10-07T00:00:00Z" };
    const calls: Array<{ path: string; method: string; owner: string | null; requestId: string | null; body: unknown; query: string }> = [];
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input)), headers = new Headers(init?.headers);
      calls.push({ path: url.pathname, method: init?.method ?? "GET", owner: headers.get("x-owner-id"), requestId: headers.get("x-request-id"), body: init?.body ? JSON.parse(String(init.body)) : null, query: url.search });
      if (url.pathname === "/internal/vacancies" && init?.method === "GET") return Response.json({ items: [draft], limit: 2, has_more: false, next_cursor: null });
      if (url.pathname === "/internal/vacancies" || url.pathname.startsWith(`/internal/vacancies/${id}`) && !url.pathname.endsWith("/ready")) {
        if (headers.get("x-owner-id") !== owner.id) return Response.json({}, { status: 404 });
        return Response.json(draft);
      }
      if (url.pathname === `/internal/vacancy-drafts/${id}`) return headers.get("x-owner-id") === owner.id ? Response.json(draft) : Response.json({}, { status: 404 });
      if (url.pathname === `/internal/vacancies/${id}/ready`) return Response.json(ready);
      if (url.pathname === "/internal/employer/candidates/search") return Response.json({ items: [], total: 0, limit: 50, has_more: false, next_cursor: null, mode: "browse", fallback_reason: null });
      return Response.json({}, { status: 404 });
    };
    installOperations(app, { limit: 100 });
    await app.register(cookie);
    await app.register(accountRoutes, { store, config, profiles, mailConfigured: false, agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport });
    await app.ready();
    const headers = { authorization: owner.authorization, "x-owner-id": other.id, "x-request-id": "vacancy-operation-safe" };
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancies", payload: {} })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancies", headers: { authorization: candidate.authorization }, payload: {} })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancies", headers, payload: { fields: {}, owner_id: other.id } })).statusCode, 400);
    assert.equal((await app.inject({ method: "POST", url: "/employer/vacancies", headers, payload: { fields: {} } })).statusCode, 200);
    assert.equal(calls.at(-1)?.owner, owner.id); assert.equal(calls.at(-1)?.requestId, "vacancy-operation-safe");
    for (const [method, suffix, body] of [
      ["GET", "", undefined], ["PATCH", "", { expected_revision: 1, fields: { city: "Тула" } }],
      ["POST", "/approve", { expected_revision: 1 }], ["POST", "/publish", { expected_revision: 1, summary_hash: "a".repeat(64) }],
      ["POST", "/close", { expected_revision: 1 }], ["DELETE", "", { expected_revision: 1 }],
    ] as const) {
      const response = await app.inject({ method, url: `/employer/vacancies/${id}${suffix}`, headers, ...(body ? { payload: body } : {}) });
      assert.equal(response.statusCode, 200, response.body); assert.equal(calls.at(-1)?.owner, owner.id);
      assert.equal(calls.at(-1)?.method, method);
    }
    assert.equal((await app.inject({ url: `/employer/vacancies/${id}`, headers: { authorization: other.authorization } })).statusCode, 404);
    const page = await app.inject({ url: "/employer/vacancies?limit=2&cursor=opaque", headers });
    assert.equal(page.statusCode, 200, page.body); assert.equal(calls.at(-1)?.query, "?limit=2&cursor=opaque");
    assert.equal((await app.inject({ url: "/employer/vacancies?limit=200", headers })).statusCode, 400);
    const search = { method: "POST" as const, url: "/employer/candidates/search", headers, payload: { filters: { vacancy_id: id } } };
    assert.equal((await app.inject(search)).statusCode, 200);
    assert.equal(calls.at(-1)?.owner, owner.id);
    assert.equal((await app.inject({ ...search, headers: { authorization: other.authorization } })).statusCode, 404);
    draft.business_status = "closed";
    const before = calls.length;
    assert.equal((await app.inject(search)).statusCode, 409);
    assert.equal(calls.length, before + 1); // no ready/search call after source closure
  } finally { await app.close(); await pool.end(); }
});
