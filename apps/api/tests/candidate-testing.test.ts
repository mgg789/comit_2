/** Private testing facade regression: auth, consent, ownership and schema bounds. */
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { AuthStore, type AuthAccount, type AccountRole } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { candidateTestingRoutes } from "../src/routes/candidate-testing.js";

class TestStore extends AuthStore {
  override async session(role: AccountRole, accountId: string): Promise<AuthAccount | null> {
    return { id: accountId, role, email: "test@example.test", email_verified: true,
      created_at: new Date().toISOString(), consent: { processing: true, version: "v1", accepted_at: new Date().toISOString() } };
  }
}

test("proxy derives candidate owner, blocks missing consent and never forwards login secrets", async () => {
  const previous = process.env.TESTING_ENGINE_TOKEN;
  process.env.TESTING_ENGINE_TOKEN = "synthetic-engine-token";
  const config = loadAuthConfig({ AUTH_SECRET: "synthetic-testing-browser-secret-32chars", AUTH_ALLOWED_ORIGINS: "http://localhost", AUTH_COOKIE_SECURE: "false" });
  const pool = new Pool();
  mock.method(pool, "query", async () => ({ rows: [] }));
  const app = Fastify();
  const calls: Array<{ url: string; options?: RequestInit }> = [];
  const transport: typeof fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    if (String(url).endsWith("/next")) return Response.json({
      session_id: "s-20261011120000123456", answered: 1, decided_grade: null,
      pending_answer: "def solve(n): return n * 2",
      task: { item_id: "code-1", family: "python", level: 2, type: "code", prompt: "Double n", function_name: "solve" },
    });
    if (String(url).includes("/testing/preflight")) return Response.json({
      specialization: "react", supported_specializations: ["react"], eligible: false, next_attempt_at: null,
      active_session_id: "s-20261011120000123456", active_session_status: "active", next_sequence: 4,
      recording_enabled: true, active_recording_enabled: true, max_questions: 30, supported_question_types: ["mcq", "short_answer", "code", "extended"],
      judges: { code_required: false, code_ready: true, text_configured: true, types: ["mcq", "short_answer", "code", "extended"] }, unavailable_reason: null,
      active_session_consents: { version: "testing-media-v1", processing: true, screen_recording: true, video_analysis: false,
        accepted_at: new Date().toISOString(), retention_days: 90 },
      active_supervision: { total_seconds: 20, in_frame_seconds: 0, focus_lost_seconds: 2,
        multi_face_events: 0, camera: "denied", badge: "не определён", uploaded_at: new Date().toISOString() }
    });
    return new Response(JSON.stringify({ session_id: "s-20261011120000123456", status: "active" }), { status: 200 });
  };
  try {
    await app.register(cookie);
    await app.register(candidateTestingRoutes, { pool, config, store: new TestStore(pool, config.secret), mailConfigured: false, transport });
    assert.equal((await app.inject({ url: "/candidate/testing/preflight?specialization=react" })).statusCode, 401);
    const owner = "12345678-1234-4234-8234-123456789abc";
    const token = await signSession(config, "candidate", owner, "a".repeat(43), new Date(Date.now() + 60000).toISOString());
    const headers = { authorization: `Bearer ${token}`, origin: "http://localhost" };
    assert.equal((await app.inject({ method: "POST", url: "/candidate/testing/sessions", headers,
      payload: { specialization: "react", consents: { version: "testing-media-v1", processing: false, screen_recording: false, video_analysis: false } } })).statusCode, 400);
    assert.equal(calls.length, 0);
    const response = await app.inject({ method: "POST", url: "/candidate/testing/sessions", headers,
      payload: { specialization: "react", consents: { version: "testing-media-v1", processing: true, screen_recording: true, video_analysis: false } } });
    assert.equal(response.statusCode, 200);
    const forwarded = calls[0]!;
    assert.equal(JSON.parse(String(forwarded.options?.body)).candidate_key, owner);
    const serviceHeaders = forwarded.options?.headers as Record<string, string>;
    assert.equal(serviceHeaders["x-candidate-id"], owner);
    assert.equal(serviceHeaders.authorization, undefined);
    assert.equal(serviceHeaders.cookie, undefined);
    const resumed = await app.inject({ url: "/candidate/testing/preflight?specialization=react", headers });
    assert.equal(resumed.statusCode, 200);
    assert.equal(resumed.json().next_sequence, 4);
    assert.deepEqual(resumed.json().supported_question_types, ["mcq", "short_answer", "code", "extended"]);
    assert.equal(resumed.json().judges.code_ready, true);
    assert.equal(resumed.json().unavailable_reason, null);
    const pending = await app.inject({ url: "/candidate/testing/sessions/s-20261011120000123456/next", headers });
    assert.equal(pending.statusCode, 200);
    assert.equal(pending.json().pending_answer, "def solve(n): return n * 2");
    assert.equal(pending.json().task.type, "code");
    assert.deepEqual(resumed.json().active_supervision, { total_seconds: 20, in_frame_seconds: 0,
      focus_lost_seconds: 2, multi_face_events: 0, camera: "denied" });
    assert.equal((await app.inject({ method: "POST", url: "/candidate/testing/sessions", headers: { ...headers, origin: "https://evil.example" },
      payload: { specialization: "react", consents: { version: "testing-media-v1", processing: true, screen_recording: true, video_analysis: false } } })).statusCode, 403);
  } finally {
    await app.close(); await pool.end(); mock.restoreAll();
    if (previous === undefined) delete process.env.TESTING_ENGINE_TOKEN; else process.env.TESTING_ENGINE_TOKEN = previous;
  }
});
