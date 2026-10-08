/** Protected transport to matching-owned candidate projections; never accepts public JWTs as service credentials. */
import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import engineSpec from "../contracts/candidate-pipeline.openapi.json" with { type: "json" };
import { correlatedFetch } from "../operations/index.js";

/** Injected transport is only for isolated gateway tests; production uses real HTTP. */
export interface CandidateGatewayOptions {
  token?: string;
  engineUrl?: string;
  transport?: typeof fetch;
}

/** Never remove unknown profile fields or coerce their values before upstream validation. */
export const candidateValidationOptions = { removeAdditional: false, coerceTypes: false, useDefaults: false } as const;

type SchemaValue = string | number | boolean | null | undefined | SchemaValue[] | { [key: string]: SchemaValue };

function engineSchema(value: SchemaValue): SchemaValue {
  if (Array.isArray(value)) return value.map(engineSchema);
  if (typeof value === "string" && value.startsWith("#/components/schemas/")) {
    return `urn:comit:candidate:${value.slice("#/components/schemas/".length)}`;
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, engineSchema(child)]));
  }
  return value;
}

/** Register allowlisted internal endpoints using generated engine schemas, with bounded upstream calls. */
export async function candidatePipelineRoutes(app: FastifyInstance, options: CandidateGatewayOptions = {}): Promise<void> {
  const token = options.token ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const baseUrl = options.engineUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const transport = correlatedFetch(options.transport ?? fetch);
  for (const [name, schema] of Object.entries(engineSpec.components.schemas)) {
    const transformed = engineSchema(schema);
    if (transformed !== null && typeof transformed === "object" && !Array.isArray(transformed)) {
      app.addSchema({ ...transformed, $id: `urn:comit:candidate:${name}` });
    }
  }
  const routes = [
    { method: "PUT", path: "/internal/candidates/:candidate_id/profile", body: "CandidateProfile", response: "CandidateStatus" },
    { method: "GET", path: "/internal/candidates/:candidate_id", response: "CandidateStatus" },
    { method: "POST", path: "/internal/candidates/:candidate_id/retry", response: "CandidateStatus" },
    { method: "GET", path: "/internal/strict-schema", response: "StrictSchema" },
    { method: "POST", path: "/internal/strict-schema/coordinates", body: "CoordinateAddition", response: "StrictSchema" },
    { method: "GET", path: "/internal/pool", response: "PoolPage" },
    { method: "GET", path: "/internal/pool/:candidate_id/resume.pdf" },
    { method: "PUT", path: "/internal/vacancies/:vacancy_id", body: "VacancyInput", response: "VacancyPipelineStatus" },
    { method: "GET", path: "/internal/vacancies/ready", response: "VacancyReadyPage" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id", response: "VacancyPipelineStatus" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id/ready", response: "VacancyReady" },
    { method: "POST", path: "/internal/vacancies/:vacancy_id/retry", response: "VacancyPipelineStatus" },
    { method: "POST", path: "/internal/vacancies/:vacancy_id/combine", body: "CombineRequest", response: "CombineStatus" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id/combine", response: "CombineStatus" },
    { method: "POST", path: "/internal/vacancies/:vacancy_id/combine/retry", response: "CombineStatus" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id/selections", response: "SelectionPage" },
    { method: "POST", path: "/internal/vacancies/:vacancy_id/rerank", body: "RerankRequest", response: "RerankStatus" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id/rerank", response: "RerankStatus" },
    { method: "POST", path: "/internal/vacancies/:vacancy_id/rerank/retry", response: "RerankStatus" },
    { method: "GET", path: "/internal/vacancies/:vacancy_id/rerank/selections", response: "RerankPage" },
    { method: "PUT", path: "/internal/vacancies/:vacancy_id/selection-state/:candidate_id", body: "SelectionStateInput", response: "SelectionState" },
    { method: "GET", path: "/internal/matching/events", response: "DeliveryStatus" },
    { method: "POST", path: "/internal/matching/events/retry", body: "DeliveryRetryRequest", response: "DeliveryStatus" },
  ] as const;
  for (const route of routes) {
    const paramNames = ["vacancy_id", "candidate_id"].filter(name => route.path.includes(`:${name}`));
    app.route({
      method: route.method,
      url: route.path,
      schema: {
        tags: [route.path.startsWith("/internal/vacancies") ? "vacancy-pipeline" : "candidate-pipeline"],
        security: [{ internalToken: [] }],
        ...(paramNames.length ? { params: {
          type: "object", required: paramNames,
          properties: Object.fromEntries(paramNames.map(name => [name, { type: "string", format: "uuid" }])),
        } } : {}),
        ...("body" in route ? { body: { $ref: `urn:comit:candidate:${route.body}` } } : {}),
        ...("response" in route ? { response: { 200: { $ref: `urn:comit:candidate:${route.response}` } } } : {}),
      },
      onRequest: async (request, reply) => {
        if (!token) return reply.code(503).send({ detail: "pipeline authentication is not configured" });
        const supplied = request.headers["x-internal-token"];
        if (typeof supplied !== "string" || Buffer.byteLength(supplied) !== Buffer.byteLength(token)
          || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) {
          return reply.code(401).send({ detail: "invalid internal token" });
        }
      },
      handler: async (request, reply) => {
        try {
          const incoming = new URL(request.url, "http://gateway.invalid");
          const upstream = new URL(baseUrl);
          upstream.pathname = incoming.pathname;
          upstream.search = incoming.search;
          const response = await transport(upstream, {
            method: route.method,
            headers: { "x-internal-token": token ?? "", "content-type": "application/json" },
            body: route.method === "PUT" || (route.method === "POST" && "body" in route)
              ? JSON.stringify(request.body) : undefined,
            signal: AbortSignal.timeout(10000),
            redirect: "error",
          });
          reply.code(response.status).header("cache-control", "no-store");
          if (route.path.endsWith("resume.pdf") && response.ok) {
            return reply.type("application/pdf").header("x-content-type-options", "nosniff")
              .send(Buffer.from(await response.arrayBuffer()));
          }
          const payload: unknown = await response.json();
          return reply.send(payload);
        } catch {
          return reply.code(503).send({ detail: "candidate pipeline unavailable" });
        }
      },
    });
  }
}
