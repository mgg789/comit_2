/** Employer deletion authorization, fail-closed ordering and session revocation boundary. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import type { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { cookieName, signSession } from "../src/auth/session.js";
import { accountRoutes } from "../src/routes/accounts.js";
const origin = "http://localhost:38088";
const config = loadAuthConfig({ AUTH_SECRET: "synthetic-employer-delete-route-secret-32", AUTH_ALLOWED_ORIGINS: origin, AUTH_COOKIE_SECURE: "false" });
async function harness(failure?: string) {
  const accountId = randomUUID(), sessionId = "E".repeat(43), events: string[] = [];
  let live = true;
  const store = {
    session: async (role: string, id: string) => live && role === "employer" && id === accountId ? { id } : null,
    deleteEmployerAccount: async (id: string) => { assert.equal(id, accountId); events.push("account"); live = false; return true; },
  } as unknown as AuthStore;
  const transport: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.pathname, `/internal/employers/${accountId}`);
    assert.equal(init?.method, "DELETE");
    assert.equal(new Headers(init?.headers).get("x-owner-id"), accountId);
    events.push(url.hostname);
    return Response.json({ deleted: true }, { status: url.hostname === failure ? 503 : 200 });
  };
  const app = Fastify(); await app.register(cookie);
  await app.register(accountRoutes, { config, store, mailConfigured: false, agentsUrl: "http://agents.internal", agentsToken: "synthetic-agents",
    matchingUrl: "http://matching.internal", matchingToken: "synthetic-matching", transport });
  const token = await signSession(config, "employer", accountId, sessionId, new Date(Date.now()+60000).toISOString());
  return { app, events, headers: { cookie: `${cookieName("employer", config)}=${token}`, origin } };
}
test("employer deletion fails closed on each downstream failure and enforces origin/session", async () => {
  for (const failure of ["agents.internal", "matching.internal"]) {
    const h = await harness(failure);
    try {
      assert.equal((await h.app.inject({ method: "DELETE", url: "/employer/account", headers: h.headers })).statusCode, 503);
      assert.deepEqual(h.events, failure === "agents.internal" ? ["agents.internal"] : ["agents.internal", "matching.internal"]);
      assert.equal((await h.app.inject({ method: "DELETE", url: "/employer/account", headers: { cookie: h.headers.cookie } })).statusCode, 403);
      assert.equal((await h.app.inject({ method: "DELETE", url: "/employer/account", headers: { origin } })).statusCode, 401);
    } finally { await h.app.close(); }
  }
});
test("employer deletion clears cookie only after services and account cleanup and revokes replay", async () => {
  const h = await harness();
  try {
    const response = await h.app.inject({ method: "DELETE", url: "/employer/account", headers: h.headers });
    assert.equal(response.statusCode, 200, response.body);
    assert.deepEqual(response.json(), { deleted: true });
    assert.deepEqual(h.events, ["agents.internal", "matching.internal", "account"]);
    assert.equal(response.headers["cache-control"], "no-store");
    assert.match(String(response.headers["set-cookie"]), /comit_employer_session=.*(?:Max-Age=0|Expires=)/);
    assert.equal((await h.app.inject({ method: "DELETE", url: "/employer/account", headers: h.headers })).statusCode, 401);
  } finally { await h.app.close(); }
});
