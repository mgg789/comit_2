/** Participant-authenticated post-selection endpoints; the chat implementation is intentionally absent. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AccountRole, AgentsDraftView, FeedCard, InviteRequest, RecruitmentAction, RejectSavedRequest, SavedCard } from "@comit/shared";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import type { AccountGatewayOptions } from "./accounts.js";
import { RecruitmentError, recruitmentFingerprint, type RecruitmentStore } from "../recruitment/store.js";
import {
  contactsSchema, invitationBodySchema, recruitmentActionSchema, recruitmentEventPageSchema,
  recruitmentPageSchema, recruitmentViewSchema, rejectSavedBodySchema,
} from "../recruitment/schemas.js";

/** Reuse auth/profile dependencies and injectable private transports without exposing service credentials. */
export interface RecruitmentOptions extends AccountGatewayOptions { recruitment: RecruitmentStore }

const uuid = { type: "string", format: "uuid" };
const processParams = { type: "object", additionalProperties: false, required: ["id"], properties: { id: uuid } };
const draftParams = { type: "object", additionalProperties: false, required: ["draft_id"], properties: { draft_id: uuid } };
const querySchema = { type: "object", additionalProperties: false, properties: {
  limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" },
  offset: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,14})$" },
} };
const security = (role: AccountRole) => [{ bearerAuth: [] }, { [role === "employer" ? "employerSession" : "candidateSession"]: [] }];
const invalid = (status: number, code: string): never => { throw new RecruitmentError(status, code); };

/** Register OpenAPI-described employer/candidate lifecycle endpoints with server-derived participant IDs. */
export async function recruitmentRoutes(app: FastifyInstance, options: RecruitmentOptions): Promise<void> {
  const transport = options.transport ?? fetch;
  const agentsUrl = options.agentsUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
  const matchingUrl = options.matchingUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const agentsToken = options.agentsToken ?? process.env.AGENTS_SERVICE_TOKEN;
  const matchingToken = options.matchingToken ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const guard = (role: AccountRole) => async (request: FastifyRequest, reply: FastifyReply) => {
    reply.header("cache-control", "no-store");
    try {
      const identity = await resolveSession(request, role, options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const actor = (request: FastifyRequest): string => identities.get(request)?.accountId ?? invalid(401, "unauthenticated");
  const safely = async (reply: FastifyReply, operation: () => Promise<unknown>) => {
    try { return reply.send(await operation()); }
    catch (error) {
      if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
      return reply.code(503).send({ detail: "recruitment_unavailable" });
    }
  };
  const draftFor = async (ownerId: string, draftId: string): Promise<AgentsDraftView> => {
    if (!agentsToken) return invalid(503, "agents_unavailable");
    const response = await transport(new URL(`/internal/vacancy-drafts/${draftId}`, agentsUrl), {
      headers: { "x-internal-token": agentsToken, "x-owner-id": ownerId }, signal: AbortSignal.timeout(10000), redirect: "error" });
    if (!response.ok) return invalid(response.status === 404 ? 404 : 503, "owned_vacancy_unavailable");
    const draft = await response.json() as AgentsDraftView;
    if (draft.owner_id !== ownerId || draft.draft_id !== draftId) return invalid(404, "owned_vacancy_unavailable");
    if (draft.state !== "created" || draft.publication?.status !== "accepted") return invalid(409, "vacancy_not_published");
    return draft;
  };
  const savedFor = async (ownerId: string, vacancyId: string, candidateId: string): Promise<SavedCard> => {
    if (!matchingToken) return invalid(503, "matching_unavailable");
    const response = await transport(new URL(`/internal/vacancies/${vacancyId}/saved/${candidateId}`, matchingUrl), {
      headers: { "x-internal-token": matchingToken, "x-owner-id": ownerId }, signal: AbortSignal.timeout(10000), redirect: "error" });
    if (!response.ok) return invalid([404, 409].includes(response.status) ? response.status : 503, "saved_candidate_unavailable");
    const saved = await response.json() as SavedCard;
    if (saved.candidate_id !== candidateId || saved.vacancy_id !== vacancyId) return invalid(409, "saved_candidate_mismatch");
    return saved;
  };

  app.post<{ Params: { draft_id: string }; Body: InviteRequest }>("/employer/vacancy-drafts/:draft_id/invitations", {
    schema: { tags: ["recruitment"], security: security("employer"), params: draftParams, body: invitationBodySchema, response: { 200: recruitmentViewSchema } },
    onRequest: guard("employer"),
  }, (request, reply) => safely(reply, async () => {
    const ownerId = actor(request), payload = request.body;
    const receipt = await options.recruitment.receipt(ownerId, payload.idempotency_key, recruitmentFingerprint(`invite:${request.params.draft_id}`, payload));
    if (receipt) return receipt;
    const draft = await draftFor(ownerId, request.params.draft_id);
    const saved = await savedFor(ownerId, draft.vacancy_id, payload.candidate_id);
    if (!saved.card || !options.profiles || !matchingToken) return invalid(409, "saved_candidate_source_obsolete");
    const check = await transport(new URL(`/internal/vacancies/${draft.vacancy_id}/feed/exposures/${saved.exposure_id}`, matchingUrl), {
      headers: { "x-internal-token": matchingToken, "x-owner-id": ownerId }, signal: AbortSignal.timeout(10000), redirect: "error" });
    if (!check.ok) return invalid([404, 409].includes(check.status) ? check.status : 503, "saved_candidate_source_obsolete");
    const exposure = await check.json() as FeedCard;
    if (exposure.candidate_id !== payload.candidate_id || exposure.exposure_id !== saved.exposure_id
      || exposure.profile_revision !== saved.profile_revision || exposure.decision !== "like"
      || !await options.profiles.isSelectionCurrent(payload.candidate_id, exposure.profile_revision)) return invalid(409, "saved_candidate_source_obsolete");
    const fields = draft.fields;
    const salary = payload.salary ?? { from_rub: fields.zp_min!, to_rub: fields.zp_max! };
    if (!Number.isSafeInteger(salary.from_rub) || salary.from_rub < 0 || !Number.isSafeInteger(salary.to_rub)
      || salary.to_rub < salary.from_rub) return invalid(422, "invalid_invitation_salary");
    if (!fields.city || !fields.daily || !fields.type) return invalid(409, "vacancy_terms_incomplete");
    return options.recruitment.invite(ownerId, payload, {
      draft_id: draft.draft_id, vacancy_id: draft.vacancy_id, candidate_id: payload.candidate_id, profile_revision: exposure.profile_revision,
      terms: { company_name: payload.company_name, description: payload.description, salary, contact_method: "platform",
        vacancy_title: fields.title ?? null, city: fields.city, daily: fields.daily, work_type: fields.type },
    });
  }));

  app.post<{ Params: { draft_id: string }; Body: RejectSavedRequest }>("/employer/vacancy-drafts/:draft_id/recruitment/reject", {
    schema: { tags: ["recruitment"], security: security("employer"), params: draftParams, body: rejectSavedBodySchema, response: { 200: recruitmentViewSchema } }, onRequest: guard("employer"),
  }, (request, reply) => safely(reply, async () => {
    const ownerId = actor(request);
    const receipt = await options.recruitment.receipt(ownerId, request.body.idempotency_key, recruitmentFingerprint(`reject-saved:${request.params.draft_id}`, request.body));
    if (receipt) return receipt;
    const draft = await draftFor(ownerId, request.params.draft_id);
    const saved = await savedFor(ownerId, draft.vacancy_id, request.body.candidate_id);
    return options.recruitment.rejectSaved(ownerId, draft.draft_id, draft.vacancy_id, saved.profile_revision, request.body);
  }));

  app.get<{ Params: { draft_id: string }; Querystring: { limit?: string; offset?: string } }>("/employer/vacancy-drafts/:draft_id/recruitment", {
    schema: { tags: ["recruitment"], security: security("employer"), params: draftParams, querystring: querySchema, response: { 200: recruitmentPageSchema } }, onRequest: guard("employer"),
  }, (request, reply) => safely(reply, () => options.recruitment.list("employer", actor(request), Number(request.query.limit ?? 50), Number(request.query.offset ?? 0), request.params.draft_id)));

  app.get<{ Querystring: { limit?: string; offset?: string } }>("/candidate/recruitment", {
    schema: { tags: ["recruitment"], security: security("candidate"), querystring: querySchema, response: { 200: recruitmentPageSchema } }, onRequest: guard("candidate"),
  }, (request, reply) => safely(reply, () => options.recruitment.list("candidate", actor(request), Number(request.query.limit ?? 50), Number(request.query.offset ?? 0))));

  for (const role of ["candidate", "employer"] as const) {
    app.get<{ Params: { id: string } }>(`/${role}/recruitment/:id`, {
      schema: { tags: ["recruitment"], security: security(role), params: processParams, response: { 200: recruitmentViewSchema } }, onRequest: guard(role),
    }, (request, reply) => safely(reply, () => options.recruitment.get(role, actor(request), request.params.id)));
    app.post<{ Params: { id: string }; Body: RecruitmentAction }>(`/${role}/recruitment/:id/actions`, {
      schema: { tags: ["recruitment"], security: security(role), params: processParams, body: recruitmentActionSchema, response: { 200: recruitmentViewSchema } }, onRequest: guard(role),
    }, (request, reply) => safely(reply, () => options.recruitment.act(role, actor(request), request.params.id, request.body)));
    app.get<{ Querystring: { after_sequence?: string; limit?: string } }>(`/${role}/recruitment-events`, {
      schema: { tags: ["recruitment"], security: security(role), querystring: { type: "object", additionalProperties: false,
        properties: { limit: querySchema.properties.limit, after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,14})$" } } }, response: { 200: recruitmentEventPageSchema } }, onRequest: guard(role),
    }, (request, reply) => safely(reply, () => options.recruitment.events(role, actor(request), Number(request.query.after_sequence ?? 0), Number(request.query.limit ?? 50))));
  }
  app.get<{ Params: { id: string } }>("/employer/recruitment/:id/contacts", {
    schema: { tags: ["recruitment"], security: security("employer"), params: processParams, response: { 200: contactsSchema } }, onRequest: guard("employer"),
  }, (request, reply) => safely(reply, () => options.recruitment.contacts(actor(request), request.params.id)));
}
