import type { FastifyInstance } from "fastify";
import type { Grade, SalaryMarketAggregate, Specialization } from "@comit/shared";

const specializations = ["backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml"] as const;
const grades = ["intern", "junior", "middle", "senior"] as const;
const failure = { type: "object", additionalProperties: false, required: ["detail"], properties: { detail: { type: "string" } } };
const response = { type: "object", additionalProperties: false,
  required: ["specialization", "grade", "stacks", "sample_count", "minimum_sample_count", "average_salary_rub"],
  properties: { specialization: { type: "string", enum: specializations }, grade: { type: "string", enum: grades },
    stacks: { type: "array", maxItems: 100, items: { type: "string", pattern: "^[a-z][a-z0-9_-]{0,99}$" } },
    sample_count: { type: "integer", minimum: 0 }, minimum_sample_count: { const: 3, type: "integer" },
    average_salary_rub: { anyOf: [{ type: "integer", minimum: 0 }, { type: "null" }] } } } as const;
type Query = { specialization: Specialization; grade: Grade; stacks?: string };
export interface SalaryMarketReader {
  get(query: { specialization: Specialization; grade: Grade; stacks: string[] }): Promise<SalaryMarketAggregate>;
}

/** Register a read-only public aggregate; no individual vacancy fields leave PostgreSQL. */
export async function salaryMarketRoutes(app: FastifyInstance, options: { market: SalaryMarketReader }): Promise<void> {
  app.get<{ Querystring: Query }>("/public/salary-market", {
    attachValidation: true,
    schema: { tags: ["public-showcase"], security: [], querystring: { type: "object", additionalProperties: false,
      required: ["specialization", "grade"], properties: { specialization: { type: "string", enum: specializations },
        grade: { type: "string", enum: grades }, stacks: { type: "string", maxLength: 10240,
          pattern: "^(?:[a-z][a-z0-9_-]{0,99})(?:,[a-z][a-z0-9_-]{0,99})*$" } } },
      response: { 200: response, 400: failure, 503: failure } },
  }, async (request, reply) => {
    reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff");
    if (request.validationError) return reply.code(400).send({ detail: "invalid_salary_market_query" });
    try {
      const stacks = request.query.stacks ? request.query.stacks.split(",") : [];
      if (stacks.length > 100 || new Set(stacks).size !== stacks.length) return reply.code(400).send({ detail: "invalid_salary_market_query" });
      stacks.sort();
      return await options.market.get({ specialization: request.query.specialization, grade: request.query.grade, stacks });
    } catch {
      return reply.code(503).send({ detail: "salary_market_unavailable" });
    }
  });
}
