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
