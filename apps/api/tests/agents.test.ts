/** Agents gateway contract checks; transport doubles do not constitute provider acceptance. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { agentsRoutes } from "../src/routes/agents.js";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";

const owner = "00000000-0000-4000-8000-000000000001";
const draft = "00000000-0000-4000-8000-000000000002";
const headers = { "x-internal-token": "synthetic-agents-token", "x-owner-id": owner };

test("agents routes fail closed before exposing payload validation", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  let calls = 0;
  await app.register(agentsRoutes, { token: headers["x-internal-token"], transport: async () => {
    calls += 1; return Response.json({});
  } });
  assert.equal((await app.inject({ method: "POST", url: "/internal/vacancy-drafts", payload: { fields: { unexpected: 1 } } })).statusCode, 401);
  assert.equal(calls, 0);
  await app.close();
});

test("agents gateway requires owner and preserves partial patches including explicit zero", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(agentsRoutes, { token: headers["x-internal-token"], transport: async (_url, options) => {
    assert.equal(new Headers(options?.headers).get("x-owner-id"), owner);
    assert.deepEqual(JSON.parse(String(options?.body)), { expected_revision: 1, fields: { stacks: { python: 0 } } });
    return Response.json({ detail: "synthetic_conflict" }, { status: 409 });
  } });
  const url = `/internal/vacancy-drafts/${draft}`;
  assert.equal((await app.inject({ method: "PATCH", url, headers: { "x-internal-token": headers["x-internal-token"] },
    payload: { expected_revision: 1, fields: {} } })).statusCode, 400);
  assert.equal((await app.inject({ method: "PATCH", url, headers, payload: { expected_revision: 1, fields: { stacks: { python: 0 } } } })).statusCode, 409);
  assert.equal((await app.inject({ method: "PATCH", url, headers, payload: { expected_revision: 1, fields: { stacks: { python: 8 } } } })).statusCode, 400);
  assert.equal((await app.inject({ method: "PATCH", url, headers, payload: { expected_revision: 1, fields: { invented: true } } })).statusCode, 400);
  await app.close();
});

test("agents gateway sanitizes upstream failures and exposes no tool publication endpoint", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(agentsRoutes, { token: headers["x-internal-token"], transport: async () => { throw new Error("private-secret"); } });
  const result = await app.inject({ method: "GET", url: "/internal/vacancy-drafts", headers });
  assert.equal(result.statusCode, 503);
  assert.equal(result.body.includes("private-secret"), false);
  assert.equal((await app.inject({ method: "POST", url: "/internal/agents/tools/publish", headers, payload: {} })).statusCode, 404);
  await app.close();
});

test("agents gateway is unavailable with no service token", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(agentsRoutes, { token: "" });
  assert.equal((await app.inject({ method: "GET", url: "/internal/vacancy-drafts", headers })).statusCode, 503);
  await app.close();
});
