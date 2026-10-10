/** Trusted-producer contour: the testing engine publishes approved grade results.
 *
 * POST /internal/testing/session-result (X-Internal-Token = TESTING_ENGINE_TOKEN):
 *   {candidate_id, session_id, specialization, decided_grade, supervision_badge, tasks_answered}
 * Writes candidate_grade_results, updates the source profile's declared grade
 * (CAS revision bump), stamps `grade:trusted` evidence as confirmed and
 * enqueues the exact snapshot into the outbox. Cooldowns: retake >= 7 days,
 * grade upgrade >= 30 days after the previous confirmation.
 */

import { randomUUID } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { timingSafeEqual } from "node:crypto";
import { CandidateProfileStore, ProfileError } from "../profiles/store.js";

const RETAKE_COOLDOWN_DAYS = 7;
const UPGRADE_COOLDOWN_DAYS = 30;
const GRADE_RANK: Record<string, number> = { intern: 0, junior: 1, middle: 2, senior: 3 };

export interface TestingIntegrationOptions {
  profiles: CandidateProfileStore;
  token?: string;
}

/** Register the internal testing result endpoint (service token, never browser JWTs). */
export function registerTestingIntegration(
  app: FastifyInstance,
  options: TestingIntegrationOptions,
): void {
  const token = options.token ?? process.env.TESTING_ENGINE_TOKEN ?? "";
  app.post("/internal/testing/session-result", async (request, reply) => {
    if (!token) return reply.code(503).send({ detail: "testing integration is not configured" });
    const supplied = request.headers["x-internal-token"];
    if (typeof supplied !== "string"
        || Buffer.byteLength(supplied) !== Buffer.byteLength(token)
        || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) {
      return reply.code(401).send({ detail: "invalid_internal_token" });
    }
    const body = request.body as Record<string, unknown>;
    const candidateId = String(body.candidate_id ?? "");
    const sessionId = String(body.session_id ?? "");
    const grade = String(body.decided_grade ?? "");
    const specialization = String(body.specialization ?? "backend");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidateId)
        || !sessionId || sessionId.length > 120
        || !(grade in GRADE_RANK)) {
      return reply.code(400).send({ detail: "invalid_session_result" });
    }
    try {
      const outcome = await options.profiles.recordTrustedGrade(candidateId, {
        sessionId, grade, specialization,
        supervisionBadge: typeof body.supervision_badge === "string" ? body.supervision_badge : null,
        tasksAnswered: Number(body.tasks_answered ?? 0),
      });
      return reply.code(outcome.recorded ? 200 : 409).send(outcome);
    } catch (error) {
      if (error instanceof ProfileError) return reply.code(error.status).send({ detail: error.code });
      throw error;
    }
  });
}
