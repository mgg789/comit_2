/** Preserve authoritative cooldown dates across the service publication error contract. */
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { ProfileStore, ProfileError } from "../src/profiles/store.js";
import { testingIntegrationRoutes } from "../src/routes/testing-integration.js";

test("409 publisher rejection includes exact cooldown date without exposing internals", async () => {
  const pool = new Pool();
  const profiles = new ProfileStore(pool);
  const date = "2030-02-01T12:00:00Z";
  mock.method(profiles, "recordTrustedGrade", async () => { throw new ProfileError(409, "grade_upgrade_cooldown", date); });
  const app = Fastify();
  try {
    await app.register(testingIntegrationRoutes, { profiles, token: "synthetic" });
    const response = await app.inject({ method: "POST", url: "/internal/testing/session-result",
      headers: { "x-internal-token": "synthetic" }, payload: {
        candidate_id: "12345678-1234-4234-8234-123456789abc", session_id: "synthetic-session",
        specialization: "python", decided_grade: "senior", tasks_answered: 6 } });
    assert.equal(response.statusCode, 409);
    assert.deepEqual(response.json(), { detail: "grade_upgrade_cooldown", upgrade_at: date });
  } finally { await app.close(); await pool.end(); mock.restoreAll(); }
});
