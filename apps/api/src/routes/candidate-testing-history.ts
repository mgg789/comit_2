/** Private confirmed-test calendar; candidate ownership always comes from the session. */
import type { FastifyInstance } from "fastify";
import type { AuthOptions } from "./auth.js";
import { resolveSession } from "../auth/session.js";
import type { CandidateTestingHistoryStore } from "../profiles/testing-history.js";

/** Register the read-only history endpoint with its exact OpenAPI response. */
export async function candidateTestingHistoryRoutes(app: FastifyInstance,
  options: AuthOptions & { testingHistory: CandidateTestingHistoryStore }): Promise<void> {
  app.get("/candidate/testing-history", { schema: {
    tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
    querystring: { type: "object", additionalProperties: false, properties: {} },
    response: { 401: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
      503: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } }, 200: { type: "object", additionalProperties: false,
      required: ["timezone", "period", "kind", "daily", "total"], properties: {
        timezone: { const: "Europe/Moscow" }, kind: { const: "confirmed_results" },
        period: { type: "object", additionalProperties: false, required: ["from", "to"], properties: {
          from: { type: "string", format: "date" }, to: { type: "string", format: "date" } } },
        total: { type: "integer", minimum: 0 }, daily: { type: "array", minItems: 30, maxItems: 30,
          items: { type: "object", additionalProperties: false, required: ["date", "count"], properties: {
            date: { type: "string", format: "date" }, count: { type: "integer", minimum: 0 } } } }
      } } } } }, async (request, reply) => {
    try {
      const identity = await resolveSession(request, "candidate", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      return reply.header("cache-control", "no-store").send(await options.testingHistory.get(identity.accountId));
    } catch { return reply.code(503).send({ detail: "testing_history_unavailable" }); }
  });
}
