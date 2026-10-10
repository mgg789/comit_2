/** Employer-wide public discovery; every response is watermarked against API source consent. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AgentsDraftView, PublicCandidateCard, PublicCandidateDetail, SearchPage, SearchRequest, VacancyReady } from "@comit/shared";
import type { AccountGatewayOptions } from "./accounts.js";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { correlatedFetch } from "../operations/index.js";

const ref = (name: string) => ({ $ref: `urn:comit:public:matching:${name}` });
const uuid = { type: "string", format: "uuid" };

/** Register under accountRoutes so the generated matching schemas are inherited. */
export async function showcaseRoutes(app: FastifyInstance, options: AccountGatewayOptions): Promise<void> {
  const transport = correlatedFetch(options.transport ?? fetch);
  const url = options.matchingUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const token = options.matchingToken ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const security: Array<Record<string, string[]>> = [{ bearerAuth: [] }, { employerSession: [] }];
  const params = { type: "object", additionalProperties: false, required: ["candidate_id"], properties: { candidate_id: uuid } };
  const guard = async (request: FastifyRequest, reply: FastifyReply) => {
    reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff");
    try {
      const identity = await resolveSession(request, "employer", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const headers = (ownerId: string) => ({ "x-internal-token": token!, "x-owner-id": ownerId, "content-type": "application/json" });
  const current = async (cards: Pick<PublicCandidateCard, "candidate_id" | "profile_revision">[]) => {
    if (!options.profiles) return false;
    for (const card of cards) {
      if (!await options.profiles.isSelectionCurrent(card.candidate_id, card.profile_revision)) return false;
    }
    return true;
  };
  const json = async (request: FastifyRequest, ownerId: string, path: string, schema: object, body?: unknown): Promise<unknown> => {
    const response = await transport(new URL(path, url), {
      method: body === undefined ? "GET" : "POST", headers: headers(ownerId),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(15000), redirect: "error",
    });
    if (!response.ok) throw { status: [400, 404, 409, 422, 429].includes(response.status) ? response.status : 503 };
    const reader = response.body?.getReader();
    if (!reader) throw { status: 503 };
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > 8 * 1024 * 1024) { await reader.cancel(); throw { status: 503 }; }
      chunks.push(part.value);
    }
    const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!request.compileValidationSchema(schema)(value)) throw { status: 503 };
    return value;
  };
  const fail = (reply: FastifyReply, error: unknown) => {
    const status = error && typeof error === "object" && "status" in error && typeof error.status === "number" ? error.status : 503;
    return reply.code(status).send({ detail: status === 404 ? "candidate_not_public" : status === 409 ? "showcase_source_obsolete" : "showcase_unavailable" });
  };
  const pdfUrl = (candidateId: string, revision: number) => `/api/employer/candidates/${candidateId}/resume.pdf?profile_revision=${revision}`;

  app.post<{ Reply: unknown }>("/employer/candidates/search", {
    schema: { tags: ["employer-showcase"], security, body: ref("SearchRequest"), response: { 200: ref("SearchPage") } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !token || !options.profiles) return reply.code(503).send({ detail: "showcase_unavailable" });
    try {
      const vacancyId = (request.body as SearchRequest).filters?.vacancy_id;
      if (vacancyId) {
        const agentsToken = options.agentsToken ?? process.env.AGENTS_SERVICE_TOKEN;
        const agentsUrl = options.agentsUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
        if (!agentsToken) return reply.code(503).send({ detail: "showcase_unavailable" });
        // Managed vacancy and draft IDs are identical; require the authoritative publication first.
        const source = await transport(new URL(`/internal/vacancy-drafts/${vacancyId}`, agentsUrl), {
          headers: { "x-internal-token": agentsToken, "x-owner-id": identity.accountId }, signal: AbortSignal.timeout(10000), redirect: "error" });
        if (!source.ok) return reply.code(source.status === 404 ? 404 : 503).send({ detail: "owner_record_not_found" });
        const draft = await source.json() as AgentsDraftView;
        if (draft.owner_id !== identity.accountId || draft.vacancy_id !== vacancyId) return reply.code(404).send({ detail: "owner_record_not_found" });
        if (draft.state !== "created" || draft.publication?.status !== "accepted" || draft.business_status !== "open" || draft.archived) return reply.code(409).send({ detail: "vacancy_not_open" });
        if (options.vacancyLifecycle && await options.vacancyLifecycle.pending(vacancyId)) return reply.code(409).send({ detail: "vacancy_lifecycle_pending" });
        const vacancy = await json(request, identity.accountId, `/internal/vacancies/${vacancyId}/ready`, ref("VacancyReady")) as VacancyReady;
        if (vacancy.vacancy_id !== vacancyId || vacancy.owner_id !== identity.accountId) return reply.code(404).send({ detail: "owner_record_not_found" });
        if (vacancy.status !== "open") return reply.code(409).send({ detail: "vacancy_not_open" });
      }
      const result = await json(request, identity.accountId, "/internal/employer/candidates/search", ref("SearchPage"), request.body) as SearchPage;
      if (!await current(result.items)) return reply.code(409).send({ detail: "showcase_source_obsolete" });
      return reply.send({ ...result, items: result.items.map(card => ({ ...card, resume_url: pdfUrl(card.candidate_id, card.profile_revision) })) });
    } catch (error) { return fail(reply, error); }
  });
  app.get<{ Params: { candidate_id: string } }>("/employer/candidates/:candidate_id", {
    schema: { tags: ["employer-showcase"], security, params, response: { 200: ref("PublicCandidateDetail") } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !token || !options.profiles) return reply.code(503).send({ detail: "showcase_unavailable" });
    try {
      const card = await json(request, identity.accountId, `/internal/employer/candidates/${request.params.candidate_id}`, ref("PublicCandidateDetail")) as PublicCandidateDetail;
      if (card.candidate_id !== request.params.candidate_id || !await current([card])) return reply.code(409).send({ detail: "showcase_source_obsolete" });
      return reply.send({ ...card, resume_url: pdfUrl(card.candidate_id, card.profile_revision) });
    } catch (error) { return fail(reply, error); }
  });
  app.get<{ Params: { candidate_id: string }; Querystring: { profile_revision: string } }>("/employer/candidates/:candidate_id/resume.pdf", {
    schema: { tags: ["employer-showcase"], security, params,
      querystring: { type: "object", additionalProperties: false, required: ["profile_revision"], properties: { profile_revision: { type: "string", pattern: "^[1-9][0-9]{0,14}$" } } },
      response: { 200: { type: "string", format: "binary" } }, produces: ["application/pdf"] }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !token || !options.profiles) return reply.code(503).send({ detail: "showcase_unavailable" });
    try {
      const path = `/internal/employer/candidates/${request.params.candidate_id}`;
      const revision = Number(request.query.profile_revision);
      const first = await json(request, identity.accountId, path, ref("PublicCandidateDetail")) as PublicCandidateDetail;
      if (first.candidate_id !== request.params.candidate_id || first.profile_revision !== revision || !await current([first])) return reply.code(409).send({ detail: "showcase_source_obsolete" });
      // The fixed public-pool path cannot be redirected to the owner/private PDF by upstream metadata.
      const response = await transport(new URL(`/internal/pool/${request.params.candidate_id}/resume.pdf`, url), {
        headers: headers(identity.accountId), signal: AbortSignal.timeout(15000), redirect: "error",
      });
      if (!response.ok) return reply.code(response.status === 404 ? 404 : 503).send({ detail: "showcase_unavailable" });
      const reader = response.body?.getReader();
      if (!reader) return reply.code(503).send({ detail: "showcase_unavailable" });
      const chunks: Uint8Array[] = [];
      let size = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.byteLength;
        if (size > 16 * 1024 * 1024) { await reader.cancel(); return reply.code(503).send({ detail: "showcase_unavailable" }); }
        chunks.push(part.value);
      }
      const bytes = Buffer.concat(chunks);
      if (!bytes.subarray(0, 5).equals(Buffer.from("%PDF-"))) return reply.code(503).send({ detail: "showcase_unavailable" });
      const after = await json(request, identity.accountId, path, ref("PublicCandidateDetail")) as PublicCandidateDetail;
      if (after.candidate_id !== first.candidate_id || after.profile_revision !== revision || !await current([after])) return reply.code(409).send({ detail: "showcase_source_obsolete" });
      return reply.type("application/pdf").header("content-disposition", `attachment; filename="candidate-${request.params.candidate_id}.pdf"`).send(bytes);
    } catch (error) { return fail(reply, error); }
  });
}
