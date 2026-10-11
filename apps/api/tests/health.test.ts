/** Readiness must reflect configured migrated storage, independently of liveness. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { healthRoutes } from "../src/routes/health.js";
import { requiredApiMigrationsApplied } from "../src/server.js";
test("readiness fails closed while liveness stays up", async () => {
  for (const readiness of [undefined, async () => false, async () => { throw new Error("private connection string"); }]) {
    const app = Fastify(); await app.register(healthRoutes, readiness ? { readiness } : {});
    assert.equal((await app.inject("/healthz")).statusCode, 200);
    const ready = await app.inject("/ready"); assert.equal(ready.statusCode, 503);
    assert.equal(ready.body.includes("private"), false); await app.close();
  }
});
test("only verified readiness admits traffic", async () => {
  const app=Fastify(); await app.register(healthRoutes,{readiness:async()=>true});
  assert.equal((await app.inject("/ready")).statusCode,200); await app.close();
});

test("readiness requires both independent migration branches", async () => {
  const support = "021_support_console.sql", weekly = "021_weekly_challenges.sql", oauth = "022_yandex_oauth.sql";
  for (const [names, expected] of [
    [[], 503], [[support], 503], [[weekly], 503],
    [[support, support], 503], [[support, weekly], 503], [[support, weekly, oauth], 200],
  ] as const) {
    const app = Fastify();
    await app.register(healthRoutes, { readiness: async () => requiredApiMigrationsApplied(names) });
    try {
      assert.equal((await app.inject("/ready")).statusCode, expected, names.join(","));
      assert.equal((await app.inject("/healthz")).statusCode, 200);
    } finally { await app.close(); }
  }
});
