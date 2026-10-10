/** Readiness must reflect configured migrated storage, independently of liveness. */
import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { healthRoutes } from "../src/routes/health.js";
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
