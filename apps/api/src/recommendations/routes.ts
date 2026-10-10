/** Candidate-session route exposes the latest own published recommendation. */
import type { FastifyInstance } from "fastify";
import type { AuthOptions } from "../routes/auth.js";
import { resolveSession } from "../auth/session.js";
import type { CandidateRecommendationsStore } from "./store.js";
const failure = { type: "object", additionalProperties: false, required: ["detail"], properties: { detail: { type: "string" } } } as const;
/** Register an owner-derived, uncached read contract; unavailable storage is explicit. */
export async function recommendationsRoutes(app: FastifyInstance, options: AuthOptions & { recommendations: CandidateRecommendationsStore }): Promise<void> {
  app.get("/candidate/recommendations/latest", {
    schema: { tags: ["candidate-recommendations"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
      response: { 200: { type: "object", additionalProperties: false, required: ["item"], properties: { item: { anyOf: [{ type: "null" }, {
        type: "object", additionalProperties: false, required: ["recommendation_id", "week_start", "state", "text", "generated_at", "evidence"], properties: {
          recommendation_id: { type: "string", format: "uuid" }, week_start: { type: "string", format: "date" },
          state: { type: "string", enum: ["ready", "insufficient_data", "provider_unavailable"] }, text: { type: "string", minLength: 1, maxLength: 3000 },
          generated_at: { type: "string", format: "date-time" }, evidence: { type: "array", maxItems: 21, items: {
            type: "object", additionalProperties: false, required: ["source", "category", "count"], properties: {
              source: { type: "string", enum: ["employer_feedback", "matching"] }, category: { type: "string", maxLength: 64 }, count: { type: "integer", minimum: 3 },
            } } },
        } }] } } }, 401: failure, 503: failure } },
  }, async (request, reply) => {
    reply.header("cache-control", "no-store");
    try {
      const identity = await resolveSession(request, "candidate", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      return await options.recommendations.latest(identity.accountId);
    } catch { return reply.code(503).send({ detail: "recommendations_unavailable" }); }
  });
}
