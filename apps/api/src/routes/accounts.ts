/** Browser/API-client gateways derive ownership from authenticated accounts, never request headers. */
import type { FastifyInstance, FastifyReply, FastifyRequest, HTTPMethods } from "fastify";
import type { AgentsDraftView, CandidateCard, FeedCard, SelectionPage } from "@comit/shared";
import type { ProfileStore } from "../profiles/store.js";
import agentsSpec from "../contracts/agents.openapi.json" with { type: "json" };
import engineSpec from "../contracts/candidate-pipeline.openapi.json" with { type: "json" };
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { showcaseRoutes } from "./showcase.js";

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
  profiles?: ProfileStore;
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
        if (options.profiles) await options.profiles.assertProjectionCurrent(identity.accountId);
        const url = new URL(`/internal/candidates/${identity.accountId}${pdf ? "/resume.pdf" : ""}`, matchingUrl);
        const result = await transport(url, { headers: { "x-internal-token": matchingToken },
          signal: AbortSignal.timeout(10000), redirect: "error" });
        reply.code(result.status).header("cache-control", "no-store");
        if (pdf && result.ok) return reply.type("application/pdf").header("x-content-type-options", "nosniff")
          .send(Buffer.from(await result.arrayBuffer()));
        return reply.send(await result.json());
      } catch (error) {
        if (typeof error === "object" && error !== null && "status" in error && error.status === 404) {
          return reply.code(404).send({ detail: "profile_not_found" });
        }
        if (typeof error === "object" && error !== null && "status" in error && error.status === 409) {
          return reply.code(409).send({ detail: "profile_projection_pending" });
        }
        return reply.code(503).send({ detail: "pipeline_unavailable" });
      }
    });
  }
  app.get<{ Reply: unknown }>("/candidate/taxonomy", {
    schema: { tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
      response: { 200: { $ref: "urn:comit:public:matching:StrictSchema" } } }, onRequest: guard("candidate"),
  }, async (_request, reply) => {
    if (!matchingToken) return reply.code(503).send({ detail: "pipeline_unavailable" });
    try {
      const result = await transport(new URL("/internal/strict-schema", matchingUrl), {
        headers: { "x-internal-token": matchingToken }, signal: AbortSignal.timeout(10000), redirect: "error" });
      return reply.code(result.status).header("cache-control", "private, max-age=30").send(await result.json());
    } catch { return reply.code(503).send({ detail: "pipeline_unavailable" }); }
  });

  const selectionCurrent = async (items: Pick<CandidateCard, "candidate_id" | "profile_revision">[]) => {
    if (!options.profiles) return false;
    const profiles = options.profiles;
    const results = await Promise.all(items.map(item => profiles.isSelectionCurrent(item.candidate_id, item.profile_revision)));
    return results.every(Boolean);
  };
  const querySchema = (name: string, schema: Schema): Schema => {
    // URL parameters are strings; global intake validation intentionally disables coercion.
    if (name === "limit") return { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" };
    if (name === "offset") return { type: "string", pattern: "^(0|[1-9][0-9]*)$", maxLength: 15 };
    if (name === "cursor") return { type: "string", pattern: "^[A-Za-z0-9_-]{1,30}$" };
    return refs(schema, "matching");
  };
  // Employer matching contracts come from the same generated OpenAPI as the private engine.
  for (const [path, methods] of Object.entries(engineSpec.paths)) {
    const match = /^\/internal\/vacancies\/\{vacancy_id\}(\/(?:feed|saved|visited|preferences|showcase-likes)(?:\/.*)?)$/.exec(path);
    if (!match) continue;
    const suffix = match[1]!;
    for (const [method, raw] of Object.entries(methods)) {
      if (!["get", "post"].includes(method)) continue;
      const operation = raw as { parameters?: Array<{ name: string; in: string; schema: Schema }>;
        requestBody?: { content: { "application/json": { schema: Schema } } };
        responses: { "200"?: { content?: { "application/json"?: { schema: Schema } } } } };
      const params = operation.parameters?.filter(value => value.in === "path" && value.name !== "vacancy_id") ?? [];
      const queries = operation.parameters?.filter(value => value.in === "query") ?? [];
      const response = operation.responses["200"]?.content?.["application/json"]?.schema;
      app.route<{ Params: { draft_id: string; [key: string]: string } }>({
        method: method.toUpperCase() as HTTPMethods,
        url: `/employer/vacancy-drafts/:draft_id${suffix.replace(/\{([^}]+)\}/g, ":$1")}`,
        schema: { tags: ["employer-feed"], security: [{ bearerAuth: [] }, { employerSession: [] }],
          params: { type: "object", additionalProperties: false, required: ["draft_id", ...params.map(value => value.name)],
            properties: { draft_id: { type: "string", format: "uuid" }, ...Object.fromEntries(params.map(value => [value.name, refs(value.schema, "matching")])) } },
          ...(queries.length ? { querystring: { type: "object", additionalProperties: false,
            properties: Object.fromEntries(queries.map(value => [value.name, querySchema(value.name, value.schema)])) } } : {}),
          ...(operation.requestBody ? { body: refs(operation.requestBody.content["application/json"].schema, "matching") } : {}),
          ...(response ? { response: { 200: refs(response, "matching") } } : {}),
        }, onRequest: guard("employer"), handler: async (request, reply) => {
          const identity = identities.get(request);
          if (!identity || !agentsToken || !matchingToken || !options.profiles) return reply.code(503).send({ detail: "feed_unavailable" });
          const headers = { "x-internal-token": matchingToken, "x-owner-id": identity.accountId, "content-type": "application/json" };
          reply.header("cache-control", "no-store");
          try {
            const draftResult = await transport(new URL(`/internal/vacancy-drafts/${request.params.draft_id}`, agentsUrl), {
              headers: { "x-internal-token": agentsToken, "x-owner-id": identity.accountId }, signal: AbortSignal.timeout(10000), redirect: "error" });
            if (!draftResult.ok) return reply.code(draftResult.status).send({ detail: "owner_record_not_found" });
            const draft = await draftResult.json() as AgentsDraftView;
            if (draft.owner_id !== identity.accountId || draft.draft_id !== request.params.draft_id) return reply.code(404).send({ detail: "owner_record_not_found" });
            if (draft.state !== "created" || draft.publication?.status !== "accepted") return reply.code(409).send({ detail: "vacancy_not_published" });
            const resolved = suffix.replace(/\{([^}]+)\}/g, (_full, key: string) => encodeURIComponent(request.params[key]!));
            const upstream = new URL(`/internal/vacancies/${draft.vacancy_id}${resolved}`, matchingUrl);
            upstream.search = new URL(request.url, "http://gateway.invalid").search;
            if (method === "post" && (suffix === "/feed/decisions" || suffix === "/feed/impressions")) {
              const body = request.body as { exposure_id: string; idempotency_key: string };
              const receipt = await transport(new URL(`/internal/vacancies/${draft.vacancy_id}${suffix}/${encodeURIComponent(body.idempotency_key)}`, matchingUrl), {
                headers, signal: AbortSignal.timeout(10000), redirect: "error" });
              if (receipt.status === 404) {
                const exposure = await transport(new URL(`/internal/vacancies/${draft.vacancy_id}/feed/exposures/${encodeURIComponent(body.exposure_id)}`, matchingUrl), {
                  headers, signal: AbortSignal.timeout(10000), redirect: "error" });
                if (!exposure.ok) return reply.code(exposure.status).send(await exposure.json());
                const item = await exposure.json() as FeedCard;
                if (item.exposure_id !== body.exposure_id || !await selectionCurrent([item])) return reply.code(409).send({ detail: "feed_source_obsolete" });
              } else if (!receipt.ok) return reply.code(receipt.status).send(await receipt.json());
            }
            if (method === "post" && suffix === "/showcase-likes") {
              const body = request.body as { candidate_id: string; profile_revision: number; idempotency_key: string };
              const receipt = await transport(new URL(`/internal/vacancies/${draft.vacancy_id}/showcase-likes/${encodeURIComponent(body.idempotency_key)}`, matchingUrl), {
                headers, signal: AbortSignal.timeout(10000), redirect: "error" });
              if (receipt.status === 404) {
                if (!await selectionCurrent([body])) return reply.code(409).send({ detail: "showcase_source_obsolete" });
              } else if (receipt.ok) {
                const prior = await receipt.json() as Record<string, unknown>;
                if (prior.candidate_id !== body.candidate_id || prior.profile_revision !== body.profile_revision
                  || prior.vacancy_id !== draft.vacancy_id || prior.idempotency_key !== body.idempotency_key
                  || prior.action !== "like" || prior.selection_source !== "showcase") return reply.code(409).send({ detail: "idempotency_payload_conflict" });
              } else return reply.code([404, 409].includes(receipt.status) ? receipt.status : 503).send({ detail: "showcase_unavailable" });
            }
            const result = await transport(upstream, { method: method.toUpperCase(), headers,
              ...(method === "post" ? { body: JSON.stringify(request.body) } : {}), signal: AbortSignal.timeout(10000), redirect: "error" });
            const payload: unknown = await result.json();
            if (!result.ok) return reply.code(result.status).send(payload);
            // Every card-bearing response must pass the API-owned source consent watermark.
            const cards: Pick<CandidateCard, "candidate_id" | "profile_revision">[] = [];
            const scan = (value: unknown): void => {
              if (!value || typeof value !== "object") return;
              if (Array.isArray(value)) { value.forEach(scan); return; }
              const record = value as Record<string, unknown>;
              if (typeof record.candidate_id === "string" && typeof record.profile_revision === "number" && "resume_url" in record) cards.push({ candidate_id: record.candidate_id, profile_revision: record.profile_revision });
              else Object.values(record).forEach(scan);
            };
            scan(payload);
            if (!await selectionCurrent(cards)) return reply.code(409).send({ detail: "feed_source_obsolete" });
            const rewrite = (value: unknown, exposureId?: string): unknown => {
              if (!value || typeof value !== "object") return value;
              if (Array.isArray(value)) return value.map(item => rewrite(item, exposureId));
              const record = value as Record<string, unknown>;
              const currentExposure = typeof record.exposure_id === "string" ? record.exposure_id : exposureId;
              return Object.fromEntries(Object.entries(record).map(([key, child]) => [key,
                key === "resume_url" && currentExposure ? `/api/employer/vacancy-drafts/${draft.draft_id}/feed/exposures/${currentExposure}/resume.pdf` : rewrite(child, currentExposure)]));
            };
            return reply.send(rewrite(payload));
          } catch { return reply.code(503).send({ detail: "feed_unavailable" }); }
        },
      });
    }
  }
  app.get<{ Params: { draft_id: string; exposure_id: string } }>("/employer/vacancy-drafts/:draft_id/feed/exposures/:exposure_id/resume.pdf", {
    schema: { tags: ["employer-feed"], security: [{ bearerAuth: [] }, { employerSession: [] }],
      params: { type: "object", additionalProperties: false, required: ["draft_id", "exposure_id"],
        properties: { draft_id: { type: "string", format: "uuid" }, exposure_id: { type: "string", format: "uuid" } } },
      response: { 200: { type: "string", format: "binary" } }, produces: ["application/pdf"] }, onRequest: guard("employer"),
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !agentsToken || !matchingToken || !options.profiles) return reply.code(503).send({ detail: "feed_unavailable" });
    const headers = { "x-internal-token": matchingToken, "x-owner-id": identity.accountId };
    reply.header("cache-control", "no-store");
    try {
      const draftResult = await transport(new URL(`/internal/vacancy-drafts/${request.params.draft_id}`, agentsUrl), {
        headers: { "x-internal-token": agentsToken, "x-owner-id": identity.accountId }, signal: AbortSignal.timeout(10000), redirect: "error" });
      if (!draftResult.ok) return reply.code(draftResult.status).send({ detail: "owner_record_not_found" });
      const draft = await draftResult.json() as AgentsDraftView;
      if (draft.owner_id !== identity.accountId || draft.draft_id !== request.params.draft_id) return reply.code(404).send({ detail: "owner_record_not_found" });
      if (draft.state !== "created" || draft.publication?.status !== "accepted") return reply.code(409).send({ detail: "vacancy_not_published" });
      const exposureUrl = new URL(`/internal/vacancies/${draft.vacancy_id}/feed/exposures/${request.params.exposure_id}`, matchingUrl);
      const exposure = await transport(exposureUrl, { headers, signal: AbortSignal.timeout(10000), redirect: "error" });
      if (!exposure.ok) return reply.code(exposure.status).send(await exposure.json());
      const item = await exposure.json() as FeedCard;
      if (!await selectionCurrent([item])) return reply.code(409).send({ detail: "feed_source_obsolete" });
      const resume = await transport(new URL(`/internal/pool/${item.candidate_id}/resume.pdf`, matchingUrl), {
        headers, signal: AbortSignal.timeout(10000), redirect: "error" });
      if (!resume.ok) return reply.code(resume.status).send(await resume.json());
      const bytes = Buffer.from(await resume.arrayBuffer());
      const checked = await transport(exposureUrl, { headers, signal: AbortSignal.timeout(10000), redirect: "error" });
      if (!checked.ok) return reply.code(409).send({ detail: "feed_source_obsolete" });
      const checkedItem = await checked.json() as FeedCard;
      if (item.exposure_id !== request.params.exposure_id || checkedItem.exposure_id !== item.exposure_id
        || checkedItem.candidate_id !== item.candidate_id || checkedItem.profile_revision !== item.profile_revision
        || !await selectionCurrent([checkedItem])) return reply.code(409).send({ detail: "feed_source_obsolete" });
      return reply.type("application/pdf").header("x-content-type-options", "nosniff").send(bytes);
    } catch { return reply.code(503).send({ detail: "feed_unavailable" }); }
  });
  const publicRoutes = [
    { method: "GET", suffix: "/ready", upstream: "/ready", response: "VacancyReady" },
    { method: "POST", suffix: "/combine", upstream: "/combine", body: "CombineRequest", response: "CombineStatus" },
    { method: "GET", suffix: "/combine", upstream: "/combine", response: "CombineStatus" },
    { method: "POST", suffix: "/combine/retry", upstream: "/combine/retry", response: "CombineStatus" },
    { method: "GET", suffix: "/selections", upstream: "/selections", response: "SelectionPage" },
    { method: "GET", suffix: "/candidates/:candidate_id/resume.pdf", upstream: "/selections" },
  ] as const;
  for (const route of publicRoutes) {
    app.route<{ Params: { draft_id: string; candidate_id?: string } }>({ method: route.method,
      url: `/employer/vacancy-drafts/:draft_id${route.suffix}`,
      schema: { tags: ["employer-selection"], security: [{ bearerAuth: [] }, { employerSession: [] }],
        params: { type: "object", additionalProperties: false, required: ["draft_id", ...(route.suffix.includes(":candidate_id") ? ["candidate_id"] : [])],
          properties: { draft_id: { type: "string", format: "uuid" }, candidate_id: { type: "string", format: "uuid" } } },
        ...("body" in route ? { body: { $ref: `urn:comit:public:matching:${route.body}` } } : {}),
        ...(route.suffix === "/selections" ? { querystring: { type: "object", additionalProperties: false,
          properties: { limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|[1-4][0-9]{2}|500)$" }, offset: { type: "string", pattern: "^(0|[1-9][0-9]*)$", maxLength: 15 } } } } : {}),
        ...("response" in route ? { response: { 200: { $ref: `urn:comit:public:matching:${route.response}` } } } :
          { response: { 200: { type: "string", format: "binary" } }, produces: ["application/pdf"] }),
      }, onRequest: guard("employer"), handler: async (request, reply) => {
        const identity = identities.get(request);
        if (!identity || !agentsToken || !matchingToken || !options.profiles) return reply.code(503).send({ detail: "selection_unavailable" });
        const headers = { "x-internal-token": matchingToken, "x-owner-id": identity.accountId, "content-type": "application/json" };
        try {
          const draftResult = await transport(new URL(`/internal/vacancy-drafts/${request.params.draft_id}`, agentsUrl), {
            headers: { "x-internal-token": agentsToken, "x-owner-id": identity.accountId }, signal: AbortSignal.timeout(10000), redirect: "error" });
          if (!draftResult.ok) return reply.code(draftResult.status).send({ detail: "owner_record_not_found" });
          const draft = await draftResult.json() as AgentsDraftView;
          if (draft.owner_id !== identity.accountId || draft.draft_id !== request.params.draft_id) return reply.code(404).send({ detail: "owner_record_not_found" });
          if (draft.state !== "created" || draft.publication?.status !== "accepted") return reply.code(409).send({ detail: "vacancy_not_published" });
          const pdf = request.params.candidate_id !== undefined;
          const url = new URL(`/internal/vacancies/${draft.vacancy_id}${route.upstream}`, matchingUrl);
          if (pdf) url.search = "limit=500&offset=0";
          else if (route.suffix === "/selections") url.search = new URL(request.url, "http://gateway.invalid").search;
          const result = await transport(url, { method: route.method, headers,
            ...(route.method === "POST" && "body" in route ? { body: JSON.stringify(request.body) } : {}),
            signal: AbortSignal.timeout(10000), redirect: "error" });
          reply.header("cache-control", "no-store");
          if (!result.ok) return reply.code(result.status).send(await result.json());
          if (route.suffix === "/selections" || pdf) {
            const page = await result.json() as SelectionPage;
            if (!await selectionCurrent(page.items)) return reply.code(409).send({ detail: "selection_source_obsolete" });
            if (pdf) {
              if (!page.items.some(item => item.candidate_id === request.params.candidate_id)) return reply.code(404).send({ detail: "candidate_not_selected" });
              const resume = await transport(new URL(`/internal/pool/${request.params.candidate_id}/resume.pdf`, matchingUrl), {
                headers, signal: AbortSignal.timeout(10000), redirect: "error" });
              if (!resume.ok) return reply.code(resume.status).send(await resume.json());
              const bytes = Buffer.from(await resume.arrayBuffer());
              if (!await selectionCurrent(page.items)) return reply.code(409).send({ detail: "selection_source_obsolete" });
              return reply.type("application/pdf").header("x-content-type-options", "nosniff").send(bytes);
            }
            return reply.send({ ...page, items: page.items.map(item => ({ ...item,
              resume_url: `/api/employer/vacancy-drafts/${draft.draft_id}/candidates/${item.candidate_id}/resume.pdf` })) });
          }
          return reply.send(await result.json());
        } catch { return reply.code(503).send({ detail: "selection_unavailable" }); }
      },
    });
  }
  await app.register(showcaseRoutes, options);
}
