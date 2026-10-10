/** Protected agents transport; owner ID is accounting until the account system lands. */
import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance, HTTPMethods } from "fastify";
import spec from "../contracts/agents.openapi.json" with { type: "json" };
import { correlatedFetch } from "../operations/index.js";

/** Injectable HTTP transport for isolated protocol tests. */
export interface AgentsGatewayOptions {
  token?: string;
  serviceUrl?: string;
  transport?: typeof fetch;
}

type JsonSchemaValue = string | number | boolean | null | undefined | JsonSchemaValue[] | { [key: string]: JsonSchemaValue };

function schema(value: JsonSchemaValue): JsonSchemaValue {
  if (Array.isArray(value)) return value.map(schema);
  if (typeof value === "string" && value.startsWith("#/components/schemas/")) {
    return `urn:comit:agents:${value.slice("#/components/schemas/".length)}`;
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, schema(child)]));
  }
  return value;
}

/** Register only internal documented agents paths, preserving partial patch values and owner headers. */
export async function agentsRoutes(app: FastifyInstance, options: AgentsGatewayOptions = {}): Promise<void> {
  const token = options.token ?? process.env.AGENTS_SERVICE_TOKEN;
  const base = options.serviceUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
  const transport = correlatedFetch(options.transport ?? fetch);
  for (const [name, value] of Object.entries(spec.components.schemas)) {
    const transformed = schema(value);
    if (transformed !== null && typeof transformed === "object" && !Array.isArray(transformed)) {
      app.addSchema({ ...transformed, $id: `urn:comit:agents:${name}` });
    }
  }
  for (const [path, operations] of Object.entries(spec.paths)) {
    if (!path.startsWith("/internal/")) continue;
    for (const [method, operation] of Object.entries(operations)) {
      if (!["get", "post", "put", "patch", "delete"].includes(method)) continue;
      const op = operation as {
        parameters?: Array<{ name: string; in: string; required?: boolean; schema: JsonSchemaValue }>;
        requestBody?: { content: { "application/json": { schema: JsonSchemaValue } } };
        responses: Record<string, { content?: { "application/json"?: { schema: JsonSchemaValue } } }>;
      };
      const responses = Object.fromEntries(Object.entries(op.responses)
        .filter(([status, definition]) => ["200", "202"].includes(status) && definition.content?.["application/json"]?.schema)
        .map(([status, definition]) => [status, schema(definition.content!["application/json"]!.schema)]));
      const params = op.parameters?.filter(parameter => parameter.in === "path") ?? [];
      app.route({
        method: method.toUpperCase() as HTTPMethods,
        ...(path === "/internal/organization-agent/documents" && method === "post" ? { bodyLimit: 7 * 1024 * 1024 } : {}),
        // Matching owns /internal/vacancies; editorial CRUD uses a distinct API namespace.
        url: path.replace(/^\/internal\/vacancies(?=\/|$)/, "/internal/agents/vacancies").replace(/\{([^}]+)\}/g, ":$1"),
        schema: {
          tags: ["agents"], security: [{ internalToken: [] }],
          ...(params.length ? { params: { type: "object", required: params.map(parameter => parameter.name),
            properties: Object.fromEntries(params.map(parameter => [parameter.name, schema(parameter.schema)])) } } : {}),
          headers: { type: "object", required: ["x-owner-id", "x-internal-token"], properties: {
            "x-owner-id": { type: "string", format: "uuid" },
            "x-internal-token": { type: "string", minLength: 1 },
          } },
          ...(op.requestBody ? { body: schema(op.requestBody.content["application/json"].schema) } : {}),
          ...(Object.keys(responses).length ? { response: responses } : {}),
        },
        onRequest: async (request, reply) => {
          if (!token) return reply.code(503).send({ detail: "agents authentication is not configured" });
          const supplied = request.headers["x-internal-token"];
          if (typeof supplied !== "string" || Buffer.byteLength(supplied) !== Buffer.byteLength(token)
            || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) {
            return reply.code(401).send({ detail: "invalid internal token" });
          }
        },
        handler: async (request, reply) => {
          try {
            const incoming = new URL(request.url, "http://gateway.invalid");
            const upstream = new URL(base);
            upstream.pathname = incoming.pathname.replace(/^\/internal\/agents\/vacancies(?=\/|$)/, "/internal/vacancies");
            upstream.search = incoming.search;
            const owner = request.headers["x-owner-id"];
            const result = await transport(upstream, {
              method: method.toUpperCase(),
              headers: { "x-internal-token": token ?? "", "x-owner-id": typeof owner === "string" ? owner : "",
                "content-type": "application/json" },
              ...(method !== "get" ? { body: JSON.stringify(request.body) } : {}),
              signal: AbortSignal.timeout(10000), redirect: "error",
            });
            reply.code(result.status).header("cache-control", "no-store");
            const payload: unknown = await result.json();
            return reply.send(payload);
          } catch {
            return reply.code(503).send({ detail: "agents service unavailable" });
          }
        },
      });
    }
  }
}
