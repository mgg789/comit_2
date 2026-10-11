/** Employer browser routes expose only reviewed operations, even as the internal contract grows. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import agentsSpec from "../src/contracts/agents.openapi.json" with { type: "json" };
import engineSpec from "../src/contracts/candidate-pipeline.openapi.json" with { type: "json" };
import { loadAuthConfig } from "../src/auth/config.js";
import { AuthStore } from "../src/auth/store.js";
import { migrate } from "../src/auth/migrate.js";
import { cookieName, signSession } from "../src/auth/session.js";
import { accountRoutes } from "../src/routes/accounts.js";

const config = loadAuthConfig({ AUTH_SECRET: "synthetic-only-gateway-boundary-32-bytes", AUTH_ALLOWED_ORIGINS: "http://localhost:38088", AUTH_COOKIE_SECURE: "false" });

test("new internal paths and new methods on approved paths stay private", async () => {
  // Simulate a generated upstream contract update without modifying the generated file.
  const paths: Record<string, object> = agentsSpec.paths;
  const original = paths["/internal/agents/taxonomy"];
  paths["/internal/future-worker/run"] = { post: { responses: {} } };
  paths["/internal/agents/taxonomy"] = { ...original, delete: { responses: {} } };
  const matchingPaths: Record<string, object> = engineSpec.paths;
  const feedPath = "/internal/vacancies/{vacancy_id}/feed";
  const futurePath = `${feedPath}/future-worker/run`;
  const originalFeed = matchingPaths[feedPath];
  matchingPaths[futurePath] = { post: { responses: {} } };
  matchingPaths[feedPath] = { ...originalFeed, get: { responses: {} } };
  const draftId = randomUUID();
  const app = Fastify();
  const pool = new Pool({ connectionString: "postgresql://synthetic-unused@127.0.0.1:1/synthetic" });
  let calls = 0;
  try {
    await app.register(accountRoutes, { config, store: new AuthStore(pool, config.secret), mailConfigured: false,
      agentsToken: "synthetic", transport: async () => { calls++; return Response.json({}); } });
    await app.ready();
    assert.ok(app.hasRoute({ method: "GET", url: "/employer/agents/taxonomy" }));
    assert.ok(app.hasRoute({ method: "POST", url: "/employer/vacancy-drafts/:draft_id/feed" }));
    for (const [method, url] of [["POST", "/employer/future-worker/run"], ["DELETE", "/employer/agents/taxonomy"],
      ["POST", `/employer/vacancy-drafts/${draftId}/feed/future-worker/run`], ["GET", `/employer/vacancy-drafts/${draftId}/feed`]] as const) {
      assert.equal((await app.inject({ method, url, ...(method !== "GET" ? { payload: {} } : {}) })).statusCode, 404);
    }
    assert.equal(calls, 0);
  } finally {
    delete paths["/internal/future-worker/run"];
    paths["/internal/agents/taxonomy"] = original;
    delete matchingPaths[futurePath];
    matchingPaths[feedPath] = originalFeed;
    await app.close(); await pool.end();
  }
});

const databaseUrl = process.env.AUTH_ROUTE_TEST_DATABASE_URL;
test("real employer sessions cannot call candidate workers while reviewed APIs retain auth and ownership", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify();
  const calls: Array<{ path: string; owner: string | null }> = [];
  try {
    await migrate(pool);
    const store = new AuthStore(pool, config.secret);
    const email = `gateway-${randomUUID()}@example.test`;
    const issued = await store.issue("employer", email, "192.0.2.80");
    const mail = await store.claimMail(); assert.ok(mail);
    await store.finishMail(mail.job_id, mail.lease_token);
    const session = await store.verify("employer", email, issued.challenge_id, mail.code, "account-v1");
    const token = await signSession(config, "employer", session.account.id, session.session_id, session.expires_at);
    const headers = { cookie: `${cookieName("employer", config)}=${token}`, origin: "http://localhost:38088", "x-owner-id": randomUUID() };
    await app.register(cookie);
    await app.register(accountRoutes, { config, store, mailConfigured: false, agentsToken: "synthetic",
      transport: async (input, init) => {
        calls.push({ path: new URL(String(input)).pathname, owner: new Headers(init?.headers).get("x-owner-id") });
        return Response.json([], { status: 200 });
      } });
    for (const url of ["/employer/candidate-resume/parse", "/employer/candidate-recommendations/generate"]) {
      for (const auth of [headers, {}]) {
        const response = await app.inject({ method: "POST", url, headers: auth, payload: {} });
        assert.equal(response.statusCode, 404, response.body);
      }
    }
    assert.equal(calls.length, 0);
    for (const url of ["/employer/vacancy-drafts", "/employer/organization-agent/tasks"]) {
      assert.equal((await app.inject({ url })).statusCode, 401);
      const response = await app.inject({ url, headers });
      assert.equal(response.statusCode, 200, response.body);
      assert.equal(calls.at(-1)?.owner, session.account.id);
      assert.equal(calls.at(-1)?.path, url.replace("/employer/", "/internal/"));
    }
    assert.equal(calls.length, 2);
  } finally { await app.close(); await pool.end(); }
});
