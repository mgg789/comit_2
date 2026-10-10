/** Token-authenticated accepted-result producer, compatible with the adaptive engine publisher. */
import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { ProfileError, type ProfileStore } from "../profiles/store.js";
interface TrustedResult {
  candidate_id: string; session_id: string; specialization: string;
  decided_grade: "intern" | "junior" | "middle" | "senior";
  supervision_badge?: string | null; tasks_answered: number;
}

/** Browser sessions cannot publish grades; missing service configuration fails closed. */
export async function testingIntegrationRoutes(app: FastifyInstance,
  options: { profiles: ProfileStore; token?: string }): Promise<void> {
  const token = options.token ?? process.env.TESTING_ENGINE_TOKEN ?? "";
  app.post<{ Body: TrustedResult; Reply: unknown }>("/internal/testing/session-result", {
    schema: { tags: ["internal-testing"], security: [{ internalToken: [] }],
      body: { type: "object", additionalProperties: false,
        required: ["candidate_id", "session_id", "specialization", "decided_grade", "tasks_answered"], properties: {
          candidate_id: { type: "string", format: "uuid" }, session_id: { type: "string", minLength: 1, maxLength: 120 },
          specialization: { type: "string", minLength: 1, maxLength: 100 },
          decided_grade: { type: "string", enum: ["intern", "junior", "middle", "senior"] },
          supervision_badge: { anyOf: [{ type: "string", maxLength: 100 }, { type: "null" }] },
          tasks_answered: { type: "integer", minimum: 1, maximum: 1000 }
        } }, response: {
          200: { type: "object", required: ["recorded", "grade", "decided_at"], properties: {
            recorded: { const: true }, grade: { type: "string" }, decided_at: { type: "string", format: "date-time" } } },
          400: { type: "object", properties: { detail: { type: "string" } } },
          401: { type: "object", properties: { detail: { type: "string" } } },
          403: { type: "object", properties: { detail: { type: "string" } } },
          404: { type: "object", properties: { detail: { type: "string" } } },
          409: { type: "object", properties: { detail: { type: "string" } } },
          503: { type: "object", properties: { detail: { type: "string" } } }
        } }, onRequest: async (request, reply) => {
      if (!token) return reply.code(503).send({ detail: "testing_not_configured" });
      const supplied = request.headers["x-internal-token"];
      if (typeof supplied !== "string" || Buffer.byteLength(supplied) !== Buffer.byteLength(token)
        || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) return reply.code(401).send({ detail: "invalid_internal_token" });
    }
  }, async (request, reply) => {
    try {
      const body = request.body;
      return reply.header("cache-control", "no-store").send(await options.profiles.recordTrustedGrade(body.candidate_id, {
        sessionId: body.session_id, grade: body.decided_grade, specialization: body.specialization,
        supervisionBadge: body.supervision_badge ?? null, tasksAnswered: body.tasks_answered }));
    } catch (error) {
      if (error instanceof ProfileError) return reply.code(error.status).send({ detail: error.code });
      return reply.code(503).send({ detail: "testing_result_unavailable" });
    }
  });
}
