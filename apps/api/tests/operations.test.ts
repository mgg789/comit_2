/** Root operations use real Fastify requests; distributed quotas use an isolated PostgreSQL database. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { correlatedFetch, installOperations, validatedRequestId } from "../src/operations/index.js";
import { migrate } from "../src/auth/migrate.js";

test("global quotas bound fallback keys, reset windows, expose private-free metrics and correlate concurrent calls", async () => {
  let now = 60000;
  const app = Fastify({ genReqId: request => validatedRequestId(request.headers["x-request-id"]), requestIdHeader: false });
  installOperations(app, { limit: 2, windowSeconds: 60, maxFallbackKeys: 2, now: () => now });
  const downstream: string[] = [];
  const fetcher = correlatedFetch(async (_input, init) => {
    await Promise.resolve();
    downstream.push(new Headers(init?.headers).get("x-request-id")!);
    return Response.json({ ok: true });
  });
  app.get("/anything/:id", async () => { await fetcher("http://internal.test"); return { ok: true }; });
  app.get("/healthz", async () => ({ ok: true }));
  try {
    const requests = await Promise.all(["safe-a", "safe-b"].map(id => app.inject({ url: "/anything/private-candidate-id", headers: { "x-request-id": id } })));
    assert.deepEqual(requests.map(result => result.statusCode), [200, 200]);
    assert.deepEqual(downstream.sort(), ["safe-a", "safe-b"]);
    const exhausted = await app.inject({ url: "/anything/another" });
    assert.equal(exhausted.statusCode, 429); assert.equal(exhausted.headers["retry-after"], "60");
    assert.equal((await app.inject({ url: "/healthz" })).statusCode, 200);
    assert.equal((await app.inject({ url: "/anything/second-address", remoteAddress: "192.0.2.2" })).statusCode, 200);
    assert.equal((await app.inject({ url: "/anything/third-address", remoteAddress: "192.0.2.3" })).statusCode, 429);
    now += 60000;
    const renewed = await app.inject({ url: "/anything/renewed", headers: { "x-request-id": "unsafe id" } });
    assert.equal(renewed.statusCode, 200);
    assert.match(String(renewed.headers["x-request-id"]), /^[a-f0-9-]{36}$/);
    const metrics = await app.inject({ url: "/metrics" });
    assert.equal(metrics.statusCode, 200); assert.match(metrics.body, /comit_api_requests_total/);
    assert.match(metrics.body, /comit_api_rate_limited_total 2/);
    for (const privateValue of ["private-candidate-id", "safe-a", "192.0.2.2", "authorization", "owner_id"]) assert.ok(!metrics.body.includes(privateValue));
  } finally { await app.close(); }
});

const databaseUrl = process.env.OPERATIONS_TEST_DATABASE_URL;
test("two API instances share an atomic PostgreSQL quota and database failure fails closed", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const first = Fastify(), second = Fastify();
  try {
    await migrate(pool); await pool.query("TRUNCATE api_rate_windows");
    for (const app of [first, second]) {
      installOperations(app, { pool, secret: "synthetic-shared-operational-secret", limit: 3, now: () => 60000 });
      app.get("/ordinary", async () => ({ ok: true }));
    }
    const requests = await Promise.all(Array.from({ length: 8 }, (_, index) => (index % 2 ? first : second).inject({ url: "/ordinary" })));
    assert.equal(requests.filter(result => result.statusCode === 200).length, 3);
    assert.equal(requests.filter(result => result.statusCode === 429).length, 5);
    assert.equal((await pool.query("SELECT request_count FROM api_rate_windows")).rows[0].request_count, 4);
    await pool.end();
    assert.equal((await first.inject({ url: "/ordinary" })).statusCode, 503);
  } finally { await first.close(); await second.close(); if (!pool.ended) await pool.end(); }
});
