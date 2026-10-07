/** Employer statistics expose aggregates only; owner identity always comes from the revocable session. */
import type { FastifyInstance } from "fastify";
import type { EmployerStatisticsQuery } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { resolveSession } from "../auth/session.js";
import { EmployerStatisticsStore, StatisticsError, statisticStages } from "../statistics/store.js";

const count = { type: "integer", minimum: 0 };
const counts = { type: "object", additionalProperties: false, required: statisticStages,
  properties: Object.fromEntries(statisticStages.map(stage => [stage, count])) };
const date = { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" };
const uuid = { type: "string", format: "uuid" };
const failure = { type: "object", additionalProperties: false, required: ["detail"], properties: { detail: { type: "string" } } } as const;
const response = {
  type: "object", additionalProperties: false,
  required: ["version", "generated_at", "timezone", "period", "inventory", "current", "previous", "unique_candidates", "daily", "top_vacancies"],
  properties: {
    version: { const: "employer-statistics-v1", type: "string" }, generated_at: { type: "string", format: "date-time" }, timezone: { type: "string", const: "UTC" },
    period: { type: "object", additionalProperties: false, required: ["from", "to", "previous_from", "previous_to", "vacancy_id"],
      properties: { from: date, to: date, previous_from: date, previous_to: date, vacancy_id: { anyOf: [uuid, { type: "null" }] } } },
    inventory: { type: "object", additionalProperties: false, required: ["draft", "open", "closed", "archived", "saved_pairs", "active_processes"],
      properties: { draft: count, open: count, closed: count, archived: count, saved_pairs: count, active_processes: count } },
    current: counts, previous: counts, unique_candidates: count,
    daily: { type: "array", maxItems: 90, items: { type: "object", additionalProperties: false, required: ["date", "counts"], properties: { date, counts } } },
    top_vacancies: { type: "array", maxItems: 10, items: { type: "object", additionalProperties: false,
      required: ["vacancy_id", "draft_id", "title", "status", "events", "candidates"], properties: {
        vacancy_id: uuid, draft_id: uuid, title: { type: "string", maxLength: 200 }, status: { type: "string", enum: ["draft", "open", "closed", "archived"] }, events: count, candidates: count,
      } } },
  },
} as const;

/** Register the chart-ready GET contract; missing source schemas or database failure produce an explicit 503. */
export async function statisticsRoutes(app: FastifyInstance, options: AuthOptions & { statistics: EmployerStatisticsStore }): Promise<void> {
  app.get<{ Querystring: EmployerStatisticsQuery }>("/employer/statistics", {
    schema: { tags: ["employer-statistics"], security: [{ bearerAuth: [] }, { employerSession: [] }],
      querystring: { type: "object", additionalProperties: false, properties: { from: date, to: date, vacancy_id: uuid } },
      response: { 200: response, 401: failure, 404: failure, 503: failure } },
    onRequest: async (request, reply) => {
      reply.header("cache-control", "no-store");
      try {
        if (!await resolveSession(request, "employer", options.config, options.store)) return reply.code(401).send({ detail: "unauthenticated" });
      } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
    },
  }, async (request, reply) => {
    try {
      // Recheck revocation immediately before reading the aggregate snapshot.
      const identity = await resolveSession(request, "employer", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      return await options.statistics.get(identity.accountId, request.query);
    } catch (error) {
      if (error instanceof StatisticsError) return reply.code(error.status).send({ detail: error.code });
      return reply.code(503).send({ detail: "statistics_unavailable" });
    }
  });
}
