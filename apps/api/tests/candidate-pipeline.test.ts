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
  assert.deepEqual(result.json(), { items: [], limit: 100, offset: 0, has_more: false });
  assert.equal(calls, 1);
  await app.close();
});

test("combine gateway validates controls and both exclusion IDs before forwarding", async () => {
  const app = gateway();
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (_url, options) => {
    calls += 1;
    assert.equal(new Headers(options?.headers).get("x-internal-token"), "synthetic-test-token");
    return Response.json({ detail: "synthetic conflict" }, { status: 409 });
  } });
  const headers = { "x-internal-token": "synthetic-test-token", "x-owner-id": "00000000-0000-4000-8000-000000000003" };
  const url = "/internal/vacancies/00000000-0000-4000-8000-000000000001/combine";
  assert.equal((await app.inject({ method: "POST", url, payload: {} })).statusCode, 401);
  for (const payload of [{ top_k: 0 }, { top_k: 501 }, { top_k: true }, { top_k: "50" }, { unknown: 1 }]) {
    assert.equal((await app.inject({ method: "POST", url, headers, payload })).statusCode, 400);
  }
  assert.equal(calls, 0);
  assert.equal((await app.inject({ method: "POST", url, headers, payload: { top_k: 50,
    idempotency_key: "00000000-0000-4000-8000-000000000002" } })).statusCode, 409);
  const stateUrl = "/internal/vacancies/00000000-0000-4000-8000-000000000001/selection-state/invalid-uuid";
  assert.equal((await app.inject({ method: "PUT", url: stateUrl, headers, payload: { state: "visited" } })).statusCode, 400);
  assert.equal(calls, 1);
  await app.close();
});

test("event delivery gateway exposes acknowledgements and validates retry stream identity", async () => {
  const app = gateway();
  let calls = 0;
  const status = { candidate_pending: 0, vacancy_pending: 1, acknowledged_count: 4,
    queued_count: 1, quarantined_count: 0, retrying_count: 0 };
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (url, options) => {
    calls += 1;
    if (options?.method === "POST") {
      assert.equal(new URL(String(url)).pathname, "/internal/matching/events/retry");
      assert.deepEqual(JSON.parse(String(options.body)), { source: "vacancy", event_id: 1 });
    }
    return Response.json(status);
  } });
  const headers = { "x-internal-token": "synthetic-test-token" };
  const result = await app.inject({ method: "GET", url: "/internal/matching/events", headers });
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.json(), status);
  assert.equal((await app.inject({ method: "POST", url: "/internal/matching/events/retry", headers,
    payload: { source: "arbitrary-table", event_id: 1 } })).statusCode, 400);
  assert.equal((await app.inject({ method: "POST", url: "/internal/matching/events/retry", headers,
    payload: { source: "vacancy", event_id: 1 } })).statusCode, 200);
  assert.equal(calls, 2);
  await app.close();
});

test("rerank gateway validates caller identity and preserves reserve pagination", async () => {
  const app = gateway();
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (url, options) => {
    calls += 1;
    const upstream = new URL(String(url));
    assert.equal(new Headers(options?.headers).get("x-internal-token"), "synthetic-test-token");
    if (options?.method === "GET") {
      assert.equal(upstream.search, "?kind=reserve&limit=2&offset=1");
    }
    return Response.json({ detail: "current rerank selection is not ready" }, { status: 409 });
  } });
  const url = "/internal/vacancies/00000000-0000-4000-8000-000000000001/rerank";
  const headers = { "x-internal-token": "synthetic-test-token" };
  assert.equal((await app.inject({ method: "POST", url, payload: {} })).statusCode, 401);
  assert.equal((await app.inject({ method: "POST", url, headers, payload: { idempotency_key: "bad" } })).statusCode, 400);
  assert.equal((await app.inject({ method: "POST", url, headers, payload: { unknown: true } })).statusCode, 400);
  assert.equal((await app.inject({ method: "POST", url, headers, payload: {} })).statusCode, 409);
  assert.equal((await app.inject({ method: "GET", url: `${url}/selections?kind=reserve&limit=2&offset=1`, headers })).statusCode, 409);
  assert.equal(calls, 2);
  await app.close();
});


test("owner-scoped matching gateway requires a UUID after token authentication and preserves it", async () => {
  const app = gateway();
  const owner = "00000000-0000-4000-8000-000000000003";
  let calls = 0;
  await app.register(candidatePipelineRoutes, { token: "synthetic-test-token", transport: async (_url, options) => {
    calls++;
    assert.equal(new Headers(options?.headers).get("x-owner-id"), owner);
    return Response.json({ detail: "owner_record_not_found" }, { status: 404 });
  } });
  const root = "/internal/vacancies/00000000-0000-4000-8000-000000000001";
  for (const [method, suffix, payload] of [
    ["POST", "/combine", {}], ["GET", "/combine", undefined], ["POST", "/combine/retry", undefined],
    ["GET", "/selections", undefined],
    ["PUT", "/selection-state/00000000-0000-4000-8000-000000000002", { state: "visited" }],
  ] as const) {
    for (const invalid of [undefined, "", "invalid", `${owner},${owner}`]) {
      const headers = { "x-internal-token": "synthetic-test-token", ...(invalid === undefined ? {} : { "x-owner-id": invalid }) };
      assert.equal((await app.inject({ method, url: root + suffix, headers, payload })).statusCode, 400);
    }
    assert.equal((await app.inject({ method, url: root + suffix, headers: { "x-owner-id": owner }, payload })).statusCode, 401);
    assert.equal((await app.inject({ method, url: root + suffix,
      headers: { "x-internal-token": "synthetic-test-token", "x-owner-id": owner }, payload })).statusCode, 404);
  }
  assert.equal(calls, 5);
  await app.close();
});
