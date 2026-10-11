/** Authenticated browser facade for the private testing engine. */
import type { FastifyInstance } from "fastify";
import type { Pool } from "pg";
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession } from "../auth/session.js";

const families = ["algorithms", "sql", "rest_api", "python", "react", "docker", "teamwork"];
const detail = { type: "object", required: ["detail"], properties: { detail: { type: "string" } } };
const subject = { type: "string", enum: families };
const nullableDate = { anyOf: [{ type: "string", format: "date-time" }, { type: "null" }] };
const grade = { anyOf: [{ enum: ["intern", "junior", "middle", "senior"] }, { type: "null" }] };
const sessionProperties = { session_id: { type: "string" }, status: { enum: ["active", "finished", "abandoned"] }, tasks_given: { type: "integer", minimum: 0 } };
const consent = { type: "object", additionalProperties: false, required: ["version", "screen_recording", "video_analysis"], properties: {
  processing: { type: "boolean" }, version: { type: "string" }, screen_recording: { type: "boolean" }, video_analysis: { type: "boolean" },
  accepted_at: nullableDate, retention_days: { const: 90 } } };
const sessionResponse = { type: "object", additionalProperties: false, required: ["session_id", "status"], properties: sessionProperties };
const resultResponse = { type: "object", additionalProperties: false,
  required: ["session_id", "status", "decided_grade", "answered", "correct", "next_attempt_at", "grade_publication", "supervision", "capture_finalized"],
  properties: { ...sessionProperties, decided_grade: grade, answered: { type: "integer", minimum: 0 }, correct: { type: "integer", minimum: 0 },
    capture_finalized: { type: "boolean" }, next_attempt_at: nullableDate, failure_reason: { anyOf: [{ const: "judge_unavailable" }, { type: "null" }] }, supervision: { anyOf: [{ type: "string" }, { type: "null" }] },
    grade_publication: { anyOf: [{ type: "object", additionalProperties: false, required: ["published", "status"],
      properties: { published: { type: "boolean" }, status: { type: "string" }, retryable: { type: "boolean" }, attempts: { type: "integer", minimum: 0 }, detail: { anyOf: [{ type: "string" }, { type: "null" }] }, retry_at: nullableDate, next_retry_at: nullableDate } }, { type: "null" }] } } };
const successSchemas: Record<string, object> = {
  sessions: sessionResponse, abandon: sessionResponse, result: resultResponse, finish: resultResponse,
  preflight: { type: "object", additionalProperties: false,
    required: ["specialization", "supported_specializations", "eligible", "next_attempt_at", "upgrade_at", "active_session_id", "active_session_consents", "active_supervision", "next_sequence", "recording_enabled", "active_recording_enabled", "max_questions", "supported_question_types", "active_session_status"],
    properties: { supported_question_types: { type: "array", items: { enum: ["mcq", "short_answer", "code", "extended"] } },
      unavailable_reason: { anyOf: [{ enum: ["code_judge_unavailable", "testing_disabled"] }, { type: "null" }] },
      judges: { type: "object", additionalProperties: false, required: ["code_required", "code_ready", "text_configured", "types"], properties: {
        code_required: { type: "boolean" }, code_ready: { type: "boolean" }, text_configured: { type: "boolean" },
        types: { type: "array", items: { enum: ["mcq", "short_answer", "code", "extended"] } } } },
      active_session_status: { anyOf: [{ enum: ["active", "finished"] }, { type: "null" }] }, specialization: subject, supported_specializations: { type: "array", items: subject }, eligible: { type: "boolean" },
      next_attempt_at: nullableDate, upgrade_at: nullableDate, active_session_id: { anyOf: [{ type: "string" }, { type: "null" }] },
      active_supervision: { anyOf: [{ type: "object", additionalProperties: false,
        required: ["total_seconds", "in_frame_seconds", "focus_lost_seconds", "multi_face_events", "camera"], properties: {
          total_seconds: { type: "number" }, in_frame_seconds: { type: "number" }, focus_lost_seconds: { type: "number" },
          multi_face_events: { type: "integer" }, camera: { enum: ["granted", "denied", "unavailable"] } } }, { type: "null" }] },
      active_session_consents: { anyOf: [consent, { type: "null" }] }, next_sequence: { type: "integer", minimum: 0 }, recording_enabled: { type: "boolean" }, active_recording_enabled: { type: "boolean" }, max_questions: { const: 30 } } },
  next: { type: "object", additionalProperties: false, required: ["session_id", "answered", "decided_grade", "task"], properties: {
    pending_answer: { anyOf: [{ type: "string", maxLength: 20000 }, { type: "null" }] },
    session_id: { type: "string" }, answered: { type: "integer", minimum: 0 }, decided_grade: grade,
    task: { type: "object", additionalProperties: false, required: ["item_id", "family", "level", "type", "prompt"], properties: {
      item_id: { type: "string" }, family: subject, level: { type: "integer", minimum: 1, maximum: 4 },
      type: { enum: ["mcq", "short_answer", "code", "extended"] }, prompt: { type: "string" },
      options: { type: "array", items: { type: "string" } }, function_name: { type: "string" } } } } },
  answer: { type: "object", additionalProperties: false, required: ["session_id", "status", "answered", "correct", "judge_status", "decided_grade"],
    properties: { ...sessionProperties, answered: { type: "integer", minimum: 0 }, correct: { anyOf: [{ type: "boolean" }, { type: "null" }] },
      judge_status: { type: "string" }, decided_grade: grade } },
  supervision: { type: "object", additionalProperties: false, required: ["session_id", "badge"], properties: { session_id: { type: "string" }, badge: { type: "string" } } },
  media: { type: "object", additionalProperties: false, required: ["session_id", "stored", "retention_days"], properties: {
    session_id: { type: "string" }, stored: { type: "string" }, retention_days: { const: 90 } } }
};

/** Register owner-scoped methods; candidate IDs from browser bodies are never trusted. */
export async function candidateTestingRoutes(app: FastifyInstance,
  options: AuthOptions & { pool: Pool; transport?: typeof fetch }): Promise<void> {
  app.setErrorHandler((error, _request, reply) => {
    if (error && typeof error === "object" && "validation" in error) return reply.code(400).send({ detail: "invalid_testing_request" });
    return reply.code(503).send({ detail: "testing_unavailable" });
  });
  const base = (process.env.TESTING_ENGINE_URL ?? "http://testing-engine:8102").replace(/\/$/, "");
  const token = process.env.TESTING_ENGINE_TOKEN ?? "";
  const transport = options.transport ?? fetch;
  const sessionPath = "/candidate/testing/sessions/:sessionId";
  const params = { type: "object", required: ["sessionId"], additionalProperties: false,
    properties: { sessionId: { type: "string", pattern: "^s-[0-9]{20}$" } } };
  const specs: Array<{ method: "GET" | "POST"; path: string; suffix: string; body?: object }> = [
    { method: "GET", path: "/candidate/testing/preflight", suffix: "preflight" },
    { method: "POST", path: "/candidate/testing/sessions", suffix: "sessions", body: {
      type: "object", additionalProperties: false, required: ["specialization", "consents"], properties: {
        specialization: subject, declared_grade: { enum: ["intern", "junior", "middle", "senior"] },
        mode: { enum: ["retake", "upgrade"] }, consents: { type: "object", additionalProperties: false,
          required: ["version", "processing", "screen_recording", "video_analysis"], properties: {
            processing: { const: true }, version: { const: "testing-media-v1" }, screen_recording: { type: "boolean" }, video_analysis: { type: "boolean" } } } } } },
    { method: "GET", path: `${sessionPath}/next`, suffix: "next" },
    { method: "GET", path: `${sessionPath}/result`, suffix: "result" },
    { method: "POST", path: `${sessionPath}/answer`, suffix: "answer", body: {
      type: "object", additionalProperties: false, required: ["item_id", "answer"], properties: {
        item_id: { type: "string", minLength: 1, maxLength: 200 }, answer: { anyOf: [
          { type: "integer", minimum: 0, maximum: 100 }, { type: "string", maxLength: 20000 } ] } } } },
    { method: "POST", path: `${sessionPath}/supervision`, suffix: "supervision", body: {
      type: "object", additionalProperties: false, required: ["total_seconds", "in_frame_seconds", "focus_lost_seconds", "multi_face_events", "camera"],
      properties: { total_seconds: { type: "number", minimum: 0, maximum: 86400 }, in_frame_seconds: { type: "number", minimum: 0 },
        focus_lost_seconds: { type: "number", minimum: 0 }, multi_face_events: { type: "integer", minimum: 0, maximum: 10000 },
        camera: { enum: ["granted", "denied", "unavailable"] } } } },
    { method: "POST", path: `${sessionPath}/media`, suffix: "media", body: {
      type: "object", additionalProperties: false, required: ["kind", "sequence", "data_base64"], properties: {
        kind: { enum: ["screen", "webcam"] }, sequence: { type: "integer", minimum: 0, maximum: 10000 },
        data_base64: { type: "string", minLength: 1, maxLength: 3000000 }, mime_type: { type: "string", maxLength: 100 } } } },
    { method: "POST", path: `${sessionPath}/finish`, suffix: "finish", body: { type: "object", additionalProperties: false } },
    { method: "POST", path: `${sessionPath}/abandon`, suffix: "abandon", body: { type: "object", additionalProperties: false } }
  ];
  for (const spec of specs) app.route<{ Params: { sessionId?: string }; Querystring: { specialization?: string; mode?: string }; Body: Record<string, unknown> }>({
    method: spec.method, url: spec.path, bodyLimit: 3100000,
    schema: { tags: ["candidate-testing"], security: [{ candidateSession: [] }, { bearerAuth: [] }],
      ...(spec.path.includes(":sessionId") ? { params } : {}), ...(spec.body ? { body: spec.body } : {}),
      ...(spec.suffix === "preflight" ? { querystring: { type: "object", additionalProperties: false, required: ["specialization"],
        properties: { specialization: subject, mode: { enum: ["retake", "upgrade"] } } } } : {}),
      response: { 200: successSchemas[spec.suffix], 400: detail, 401: detail, 403: detail, 404: detail, 409: detail, 413: detail, 422: detail, 503: detail } },
    handler: async (request, reply) => {
      reply.header("cache-control", "no-store");
      if (spec.method === "POST" && !originAllowed(request, options.config)) return reply.code(403).send({ detail: "origin_forbidden" });
      const identity = await resolveSession(request, "candidate", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (!token) return reply.code(503).send({ detail: "testing_not_configured" });
      try {
        const isStart = spec.suffix === "sessions", isPreflight = spec.suffix === "preflight";
        const specialization = isStart ? request.body.specialization : request.query.specialization;
        let next: string | null = null, upgrade: string | null = null;
        if (isStart || isPreflight) {
          const last = (await options.pool.query<{ grade: string; decided_at: Date }>(
            "SELECT grade,decided_at FROM candidate_grade_results WHERE candidate_id=$1 AND specialization=$2 ORDER BY decided_at DESC LIMIT 1",
            [identity.accountId, specialization])).rows[0];
          if (last) {
            next = new Date(last.decided_at.getTime() + 7 * 86400000).toISOString();
            upgrade = new Date(last.decided_at.getTime() + 30 * 86400000).toISOString();
          }
          const mode = isStart ? request.body.mode : request.query.mode;
          const allowedAt = mode === "upgrade" ? upgrade : next;
          if (isStart && allowedAt && Date.now() < Date.parse(allowedAt)) return reply.code(409).send({ detail: mode === "upgrade" ? "grade_upgrade_cooldown" : "retake_cooldown" });
        }
        const path = isStart ? "/testing/sessions" : isPreflight ? `/testing/preflight?specialization=${encodeURIComponent(String(specialization))}`
          : `/testing/sessions/${encodeURIComponent(request.params.sessionId!)}/${spec.suffix}`;
        const response = await transport(base + path, { method: spec.method, signal: AbortSignal.timeout(spec.suffix === "answer" ? 65000 : 25000),
          headers: { "content-type": "application/json", "x-internal-token": token, "x-candidate-id": identity.accountId },
          ...(spec.method === "POST" ? { body: JSON.stringify({ ...request.body, ...(isStart ? { candidate_key: identity.accountId } : {}) }) } : {}) });
        const result = await response.json() as Record<string, unknown>;
        if ((spec.suffix === "result" || spec.suffix === "finish") && response.ok && result.grade_publication && typeof result.grade_publication === "object") {
          const accepted = (await options.pool.query<{ decided_at: Date }>(
            "SELECT decided_at FROM candidate_grade_results WHERE candidate_id=$1 AND session_id=$2", [identity.accountId, request.params.sessionId])).rows[0];
          if (accepted) result.next_attempt_at = new Date(accepted.decided_at.getTime() + 7 * 86400000).toISOString();
        }
        if (isPreflight && response.ok) {
          // anyOf serializers validate each variant before stripping additional properties.
          // Stored supervision includes operator metadata; expose only resume counters.
          const report = result.active_supervision;
          if (report && typeof report === "object" && !Array.isArray(report)) {
            const allowed = new Set(["total_seconds", "in_frame_seconds", "focus_lost_seconds", "multi_face_events", "camera"]);
            result.active_supervision = Object.fromEntries(Object.entries(report).filter(([key]) => allowed.has(key)));
          }
          result.upgrade_at = upgrade;
          const allowedAt = request.query.mode === "upgrade" ? upgrade : next;
          if (allowedAt && Date.now() < Date.parse(allowedAt)) { result.eligible = false; result.next_attempt_at = allowedAt; }
        }
        return reply.code(response.status).send(result);
      } catch { return reply.code(503).send({ detail: "testing_unavailable" }); }
    }
  });
}
