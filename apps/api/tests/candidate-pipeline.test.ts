/** Gateway authorization, real schema registration and sanitized dependency failures. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { candidatePipelineRoutes, candidateValidationOptions } from "../src/routes/candidate-pipeline.js";

function gateway() {
  return Fastify({ ajv: { customOptions: candidateValidationOptions } });
}

test("gateway rejects anonymous requests before forwarding or exposing validation errors", async () => {
  const app = gateway();
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async () => {
    calls += 1;
    return new Response("{}");
  } });
  const result = await app.inject({ method: "PUT", url: "/internal/candidates/00000000-0000-4000-8000-000000000001/profile", payload: { source_revision: 1 } });
  assert.equal(result.statusCode, 401);
  assert.equal(calls, 0);
  await app.close();
});

test("gateway fails closed without a token", async () => {
  const app = gateway();
  await app.register(candidatePipelineRoutes, { token: "" });
  assert.equal((await app.inject({ method: "GET", url: "/internal/pool" })).statusCode, 503);
  await app.close();
});

test("gateway never exposes the private owner resume, including to the pool service token", async () => {
  const app = gateway();
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token" });
  const response = await app.inject({ method: "GET", url: "/internal/candidates/00000000-0000-4000-8000-000000000001/resume.pdf",
    headers: { "x-internal-token": "synthetic-test-token" } });
  assert.equal(response.statusCode, 404);
  await app.close();
});

test("gateway validates generated intake schema and preserves upstream conflict status", async () => {
  const app = gateway();
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (_url, options) => {
    calls += 1;
    assert.equal(new Headers(options?.headers).get("x-internal-token"), "synthetic-test-token");
    return Response.json({ detail: "stale source_revision" }, { status: 409 });
  } });
  const headers = { "x-internal-token": "synthetic-test-token" };
  const url = "/internal/candidates/00000000-0000-4000-8000-000000000001/profile";
  const invalid = await app.inject({ method: "PUT", url, headers, payload: { source_revision: 1, domains: [{ key: "sql", level: 8, status: "confirmed" }] } });
  assert.equal(invalid.statusCode, 400);
  assert.equal(calls, 0);
  const unknown = await app.inject({ method: "PUT", url, headers, payload: { source_revision: 1, unknown_data: "must not be discarded" } });
  assert.equal(unknown.statusCode, 400);
  assert.equal(calls, 0);
  const conflict = await app.inject({ method: "PUT", url, headers, payload: { source_revision: 1 } });
  assert.equal(conflict.statusCode, 409);
  assert.equal(calls, 1);
  await app.close();
});

test("gateway does not expose upstream exceptions or DSNs", async () => {
  const app = gateway();
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async () => {
    throw new Error("private-dsn-secret");
  } });
  const response = await app.inject({ method: "GET", url: "/internal/pool", headers: { "x-internal-token": "synthetic-test-token" } });
  assert.equal(response.statusCode, 503);
  assert.equal(response.body.includes("private-dsn-secret"), false);
  await app.close();
});

test("vacancy gateway accepts sparse requirements and upstream aliases without altering input", async () => {
  const app = gateway();
  const received: unknown[] = [];
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (_url, options) => {
    received.push(JSON.parse(String(options?.body)));
    return Response.json({ detail: "synthetic conflict" }, { status: 409 });
  } });
  const headers = { "x-internal-token": "synthetic-test-token" };
  const url = "/internal/vacancies/00000000-0000-4000-8000-000000000001";
  for (const payload of [{ source_revision: 1, bio: null }, { source_revision: 2, class: "ML", type: "дистант" },
    { source_revision: 3, stacks: { python: 0 }, domens: {}, soft_skills: {} },
    { source_revision: Number.MAX_SAFE_INTEGER, zp_min: Number.MAX_SAFE_INTEGER, zp_max: Number.MAX_SAFE_INTEGER }]) {
    assert.equal((await app.inject({ method: "PUT", url, headers, payload })).statusCode, 409);
    assert.deepEqual(received.at(-1), payload);
  }
  for (const payload of [{ source_revision: 4, stacks: { python: true } },
    { source_revision: 4, stacks: { python: 8 } }, { source_revision: 4, unknown: 1 },
    { source_revision: Number.MAX_SAFE_INTEGER + 1 }, { source_revision: 4, zp_max: Number.MAX_SAFE_INTEGER + 1 }]) {
    assert.equal((await app.inject({ method: "PUT", url, headers, payload })).statusCode, 400);
  }
  assert.equal(received.length, 4);
  await app.close();
});

test("vacancy ready page uses the static route and anonymous intake never reaches upstream", async () => {
  const app = gateway();
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (url) => {
    calls += 1;
    assert.equal(new URL(String(url)).pathname, "/internal/vacancies/ready");
    return Response.json({ items: [], limit: 100, offset: 0 });
  } });
  assert.equal((await app.inject({ method: "PUT", url: "/internal/vacancies/00000000-0000-4000-8000-000000000001", payload: {} })).statusCode, 401);
  const result = await app.inject({ method: "GET", url: "/internal/vacancies/ready", headers: { "x-internal-token": "synthetic-test-token" } });
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.json(), { items: [], limit: 100, offset: 0 });
  assert.equal(calls, 1);
  await app.close();
});
