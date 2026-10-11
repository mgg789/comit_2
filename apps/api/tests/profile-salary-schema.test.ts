import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { candidateProfileFieldsSchema } from "../src/profiles/schemas.js";

test("candidate expectations accept no ceiling and preserve it in response serialization", async () => {
  const app = Fastify({ ajv: { customOptions: { coerceTypes: false } } });
  app.post("/profile", { schema: { body: candidateProfileFieldsSchema, response: { 200: candidateProfileFieldsSchema } } }, async request => request.body);
  try {
    for (const salary of [{ from_rub: 120000, to_rub: null }, { from_rub: 0, to_rub: null }, { from_rub: 120000, to_rub: 150000 }]) {
      const response = await app.inject({ method: "POST", url: "/profile", payload: { salary } });
      assert.equal(response.statusCode, 200); assert.deepEqual(response.json().salary, salary);
    }
    for (const salary of [{ from_rub: -1, to_rub: null }, { from_rub: 120000 }, { from_rub: 120000, to_rub: "unlimited" }]) {
      assert.equal((await app.inject({ method: "POST", url: "/profile", payload: { salary } })).statusCode, 400);
    }
  } finally { await app.close(); }
});
