/** Candidate account deletion gateway regressions with a real Fastify boundary. */

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import type { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { cookieName, signSession } from "../src/auth/session.js";
import { accountRoutes } from "../src/routes/accounts.js";
import type { PurgeChatContext } from "../src/chat/client.js";

const origin = "http://localhost:38088";
const config = loadAuthConfig({ AUTH_SECRET: "synthetic-candidate-delete-route-secret-32", AUTH_ALLOWED_ORIGINS: origin, AUTH_COOKIE_SECURE: "false" });

type Scenario = { matchingStatus?: number; testingStatus?: number; testingToken?: string; chatStatus?: number };

async function appFor(scenario: Scenario, candidateId: string, employerId: string) {
  const sessions = new Map<string, { role: "candidate" | "employer"; accountId: string }>();
  const deleted: string[] = [];
  const sessionId = (role: "candidate" | "employer") => {
    const id = "A".repeat(42) + (role === "candidate" ? "C" : "E");
    sessions.set(id, { role, accountId: role === "candidate" ? candidateId : employerId });
    return id;
  };
  const candidateSessionId = sessionId("candidate");
  const employerSessionId = sessionId("employer");
  const events: string[] = [];
  const store = {
    session: async (role: "candidate" | "employer", accountId: string, id: string) => {
      const session = sessions.get(id);
      return session && session.role === role && session.accountId === accountId ? { id } : null;
    },
    deleteCandidateAccount: async (accountId: string, purge: (context: PurgeChatContext) => Promise<void>) => {
      await purge({ process_id: "synthetic-process", owner_id: employerId, candidate_id: accountId });
      events.push("account");
      deleted.push(accountId);
      return true;
    },
  } as unknown as AuthStore;
  const calls: Array<{ path: string; owner: string | null }> = [];
  const transport: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    if (url.host === "chat.internal") {
      assert.equal(url.pathname, "/internal/chat/purge");
      assert.deepEqual(JSON.parse(String(init?.body)), { process_id: "synthetic-process", owner_id: employerId,
        candidate_id: candidateId, actor_id: employerId });
      events.push("chat");
      return Response.json({ deleted: true }, { status: scenario.chatStatus ?? 200 });
    }
    events.push(url.host === "matching.internal" ? "matching" : "testing");
    calls.push({ path: url.pathname, owner: new Headers(init?.headers).get("x-owner-id") });
    const status = url.host === "matching.internal" ? (scenario.matchingStatus ?? 200) : (scenario.testingStatus ?? 200);
    return Response.json({ candidate_id: candidateId, deleted: true }, { status });
  };
  const app = Fastify();
  await app.register(cookie);
  await app.register(accountRoutes, {
    config, store, mailConfigured: false, matchingUrl: "http://matching.internal", matchingToken: "matching-token",
    testingUrl: "http://testing.internal", testingToken: scenario.testingToken ?? "testing-token", transport,
    chatUrl: "http://chat.internal", chatToken: "synthetic-chat",
  });
  return { app, calls, deleted, events, candidateToken: await signSession(config, "candidate", candidateId, candidateSessionId, new Date(Date.now() + 60_000).toISOString()),
    employerToken: await signSession(config, "employer", employerId, employerSessionId, new Date(Date.now() + 60_000).toISOString()) };
}

test("candidate account deletion enforces role/origin and fails closed before source deletion", async () => {
  const candidateId = randomUUID(), employerId = randomUUID();
  const matchingDown = await appFor({ matchingStatus: 503 }, candidateId, employerId);
  try {
    const candidateCookie = `${cookieName("candidate", config)}=${matchingDown.candidateToken}`;
    assert.equal((await matchingDown.app.inject({ method: "DELETE", url: "/candidate/account", headers: { cookie: candidateCookie, origin } })).statusCode, 503);
    assert.deepEqual(matchingDown.deleted, []);
    assert.deepEqual(matchingDown.calls.map(call => call.path), [`/internal/candidates/${candidateId}`]);
    assert.equal((await matchingDown.app.inject({ method: "DELETE", url: "/candidate/account", headers: { cookie: candidateCookie } })).statusCode, 403);
    assert.equal((await matchingDown.app.inject({ method: "DELETE", url: "/candidate/account", headers: { origin } })).statusCode, 401);
    assert.equal((await matchingDown.app.inject({ method: "DELETE", url: "/candidate/account", headers: {
      authorization: `Bearer ${matchingDown.employerToken}`, origin,
    } })).statusCode, 401);
  } finally { await matchingDown.app.close(); }

  const testingTokenMissing = await appFor({ testingToken: "" }, candidateId, employerId);
  try {
    const response = await testingTokenMissing.app.inject({ method: "DELETE", url: "/candidate/account", headers: {
      cookie: `${cookieName("candidate", config)}=${testingTokenMissing.candidateToken}`, origin,
    } });
    assert.equal(response.statusCode, 503);
    assert.deepEqual(testingTokenMissing.deleted, []);
  } finally { await testingTokenMissing.app.close(); }

  const testingDown = await appFor({ testingStatus: 503 }, candidateId, employerId);
  try {
    const response = await testingDown.app.inject({ method: "DELETE", url: "/candidate/account", headers: {
      cookie: `${cookieName("candidate", config)}=${testingDown.candidateToken}`, origin,
    } });
    assert.equal(response.statusCode, 503);
    assert.deepEqual(testingDown.deleted, []);
    assert.deepEqual(testingDown.events, ["matching", "testing"]);
    assert.deepEqual(testingDown.calls.map(call => call.path), [
      `/internal/candidates/${candidateId}`, `/internal/candidates/${candidateId}`,
    ]);
  } finally { await testingDown.app.close(); }
});

test("candidate deletion preserves the API account if QGramm purge fails", async () => {
  const h = await appFor({ chatStatus: 503 }, randomUUID(), randomUUID());
  try {
    const response = await h.app.inject({ method: "DELETE", url: "/candidate/account", headers: {
      cookie: `${cookieName("candidate", config)}=${h.candidateToken}`, origin,
    } });
    assert.equal(response.statusCode, 503);
    assert.deepEqual(h.deleted, []);
    assert.deepEqual(h.events, ["matching", "testing", "chat"]);
  } finally { await h.app.close(); }
});

test("successful candidate deletion derives owner, clears cookie and disables caching", async () => {
  const candidateId = randomUUID(), employerId = randomUUID();
  const harness = await appFor({}, candidateId, employerId);
  try {
    const response = await harness.app.inject({ method: "DELETE", url: "/candidate/account", headers: {
      cookie: `${cookieName("candidate", config)}=${harness.candidateToken}`, origin,
    } });
    assert.equal(response.statusCode, 200, response.body);
    assert.deepEqual(response.json(), { deleted: true });
    assert.deepEqual(harness.deleted, [candidateId]);
    assert.deepEqual(harness.events, ["matching", "testing", "chat", "account"]);
    assert.deepEqual(harness.calls, [
      { path: `/internal/candidates/${candidateId}`, owner: null },
      { path: `/internal/candidates/${candidateId}`, owner: null },
    ]);
    assert.equal(response.headers["cache-control"], "no-store");
    assert.match(String(response.headers["set-cookie"]), /comit_candidate_session=/);
    assert.match(String(response.headers["set-cookie"]), /Max-Age=0|Expires=/);
  } finally { await harness.app.close(); }
});
