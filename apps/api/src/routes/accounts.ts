/** Browser/API-client gateways derive ownership from authenticated accounts, never request headers. */
import type { FastifyInstance, FastifyReply, FastifyRequest, HTTPMethods } from "fastify";
import agentsSpec from "../contracts/agents.openapi.json" with { type: "json" };
import engineSpec from "../contracts/candidate-pipeline.openapi.json" with { type: "json" };
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";

type Schema = string | number | boolean | null | undefined | Schema[] | { [key: string]: Schema };

function refs(value: Schema, namespace: string): Schema {
  if (Array.isArray(value)) return value.map(child => refs(child, namespace));
  if (typeof value === "string" && value.startsWith("#/components/schemas/")) return `urn:comit:public:${namespace}:${value.slice(21)}`;
  if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, refs(child, namespace)]));
  return value;
}

/** Dependencies are injectable for ownership/transport tests; production uses private service configuration. */
export interface AccountGatewayOptions extends AuthOptions {
  agentsUrl?: string;
  agentsToken?: string;
  matchingUrl?: string;
  matchingToken?: string;
  transport?: typeof fetch;
}

/** Expose employer editorial endpoints and candidate private reads with server-derived UUIDs. */
export async function accountRoutes(app: FastifyInstance, options: AccountGatewayOptions): Promise<void> {
  const { config, store } = options;
  const transport = options.transport ?? fetch;
  const agentsUrl = options.agentsUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
  const agentsToken = options.agentsToken ?? process.env.AGENTS_SERVICE_TOKEN;
  const matchingUrl = options.matchingUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const matchingToken = options.matchingToken ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  for (const [namespace, spec] of [["agents", agentsSpec], ["matching", engineSpec]] as const) {
    for (const [name, value] of Object.entries(spec.components.schemas)) {
      const transformed = refs(value, namespace);
      if (transformed && typeof transformed === "object" && !Array.isArray(transformed)) {
        app.addSchema({ ...transformed, $id: `urn:comit:public:${namespace}:${name}` });
      }
    }
  }
  const guard = (role: "candidate" | "employer") => async (request: FastifyRequest, reply: FastifyReply) => {
    try {
      const identity = await resolveSession(request, role, config, store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, config, identity.transport === "bearer")) {
        return reply.code(403).send({ detail: "origin_forbidden" });
      }
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  for (const [path, methods] of Object.entries(agentsSpec.paths)) {
    if (!path.startsWith("/internal/")) continue;
    for (const [method, raw] of Object.entries(methods)) {
      if (!["get", "post", "patch"].includes(method)) continue;
      const operation = raw as { parameters?: Array<{ name: string; in: string; schema: Schema }>;
        requestBody?: { content: { "application/json": { schema: Schema } } };
        responses: { "200"?: { content?: { "application/json"?: { schema: Schema } } } } };
      const params = operation.parameters?.filter(value => value.in === "path") ?? [];
      const response = operation.responses["200"]?.content?.["application/json"]?.schema;
      app.route({ method: method.toUpperCase() as HTTPMethods,
        url: path.replace("/internal/", "/employer/").replace(/\{([^}]+)\}/g, ":$1"),
        schema: { tags: ["employer-account"], security: [{ bearerAuth: [] }, { employerSession: [] }],
          ...(params.length ? { params: { type: "object", required: params.map(value => value.name),
            properties: Object.fromEntries(params.map(value => [value.name, refs(value.schema, "agents")])) } } : {}),
          ...(operation.requestBody ? { body: refs(operation.requestBody.content["application/json"].schema, "agents") } : {}),
          ...(response ? { response: { 200: refs(response, "agents") } } : {}) },
        onRequest: guard("employer"),
        handler: async (request, reply) => {
          const identity = identities.get(request);
          if (!identity || !agentsToken) return reply.code(503).send({ detail: "agents_unavailable" });
          try {
            const incoming = new URL(request.url, "http://gateway.invalid");
            const url = new URL(agentsUrl);
            url.pathname = incoming.pathname.replace("/employer/", "/internal/"); url.search = incoming.search;
            const result = await transport(url, { method: request.method, headers: {
              "x-internal-token": agentsToken, "x-owner-id": identity.accountId, "content-type": "application/json" },
              ...(method !== "get" ? { body: JSON.stringify(request.body) } : {}), signal: AbortSignal.timeout(10000), redirect: "error" });
            return reply.code(result.status).header("cache-control", "no-store").send(await result.json());
          } catch { return reply.code(503).send({ detail: "agents_unavailable" }); }
        } });
    }
  }
  for (const pdf of [false, true]) {
    app.get<{ Reply: unknown }>(pdf ? "/candidate/resume.pdf" : "/candidate/pipeline", {
      schema: { tags: ["candidate-account"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
        ...(pdf ? { response: { 200: { type: "string", format: "binary" } }, produces: ["application/pdf"] }
          : { response: { 200: { $ref: "urn:comit:public:matching:CandidateStatus" } } }) },
      onRequest: guard("candidate"),
    }, async (request, reply) => {
      const identity = identities.get(request);
      if (!identity || !matchingToken) return reply.code(503).send({ detail: "pipeline_unavailable" });
      try {
        const url = new URL(`/internal/candidates/${identity.accountId}${pdf ? "/resume.pdf" : ""}`, matchingUrl);
        const result = await transport(url, { headers: { "x-internal-token": matchingToken },
          signal: AbortSignal.timeout(10000), redirect: "error" });
        reply.code(result.status).header("cache-control", "no-store");
        if (pdf && result.ok) return reply.type("application/pdf").header("x-content-type-options", "nosniff")
          .send(Buffer.from(await result.arrayBuffer()));
        return reply.send(await result.json());
      } catch { return reply.code(503).send({ detail: "pipeline_unavailable" }); }
    });
  }
}
