/** Trusted, owner-scoped tool bridge for the organization agent runtime. */
import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { Pool } from "pg";
import type { JsonValue, RecruitmentAction, SendChatMessage } from "@comit/shared";
import { correlatedFetch } from "../operations/index.js";
import { EmployerStatisticsStore, StatisticsError } from "../statistics/store.js";
import { RecruitmentError, type RecruitmentStore } from "../recruitment/store.js";
import type { ProfileStore } from "../profiles/store.js";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const tools = [
  "organization_stats", "vacancy_stats", "recruitment_list", "recruitment_get",
  "candidate_showcase_search", "candidate_showcase_detail", "candidate_context_search", "candidate_context_detail",
  "candidate_feed", "candidate_feed_continue", "feed_decision", "feed_impression",
  "candidate_chat_read", "candidate_chat_send", "interview_propose", "interview_accept",
] as const;
type ToolName = typeof tools[number];

/** Service-side authorization proof for agent access to a participant chat. */
export interface OrganizationAgentDelegationVerifier {
  /** Check owner, process, task, operation and expiry in the authoritative delegation store. */
  verify(input: { ownerId: string; processId: string; delegationId: string; taskId: string; operation: "read" | "send" }): Promise<boolean>;
}

/** Injectable dependencies keep the bridge testable without issuing real agent actions. */
export interface OrganizationAgentToolsOptions {
  token?: string;
  agentsToken?: string;
  matchingToken?: string;
  chatToken?: string;
  agentsUrl?: string;
  matchingUrl?: string;
  chatUrl?: string;
  transport?: typeof fetch;
  pool: Pool;
  recruitment: RecruitmentStore;
  statistics: EmployerStatisticsStore;
  profiles?: ProfileStore;
  delegation?: OrganizationAgentDelegationVerifier;
}

/** Public bridge errors deliberately contain only stable machine-readable details. */
class ToolBridgeError extends Error {
  public constructor(public readonly status: number, public readonly code: string) { super(code); }
}

function fail(status: number, code: string): never { throw new ToolBridgeError(status, code); }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function argsRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value) || Object.keys(value).length > 40) fail(422, "invalid_tool_arguments");
  return value;
}

function stringArg(args: Record<string, unknown>, key: string, max = 4096): string {
  const value = args[key];
  if (typeof value !== "string" || value.length < 1 || value.length > max) fail(422, "invalid_tool_arguments");
  return value;
}

function optionalString(args: Record<string, unknown>, key: string, max = 4096): string | undefined {
  if (args[key] === undefined || args[key] === null) return undefined;
  return stringArg(args, key, max);
}

function uuidArg(args: Record<string, unknown>, key: string): string {
  const value = stringArg(args, key, 36);
  if (!uuidPattern.test(value)) fail(422, "invalid_tool_arguments");
  return value;
}

function intArg(args: Record<string, unknown>, key: string, minimum: number, maximum: number): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) fail(422, "invalid_tool_arguments");
  return value;
}

function jsonValue(value: unknown, depth = 0): JsonValue {
  if (depth > 12) fail(503, "tool_response_invalid");
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    if (value.length > 1000) fail(503, "tool_response_invalid");
    return value.map(item => jsonValue(item, depth + 1));
  }
  if (isRecord(value)) {
    if (Object.keys(value).length > 100) fail(503, "tool_response_invalid");
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, jsonValue(item, depth + 1)]));
  }
  fail(503, "tool_response_invalid");
}

/** Remove accidental private transport fields before a tool result reaches the agent context. */
function redactPrivate(value: JsonValue, depth = 0): JsonValue {
  if (depth > 12) fail(503, "tool_response_invalid");
  if (Array.isArray(value)) return value.map(item => redactPrivate(item, depth + 1));
  if (typeof value !== "object" || value === null) return value;
  const privateKeys = new Set(["email", "phone", "contacts", "data_base64", "access_token", "authorization", "token", "secret", "private_key"]);
  return Object.fromEntries(Object.entries(value).filter(([key]) => !privateKeys.has(key)).map(([key, item]) => [key, redactPrivate(item, depth + 1)]));
}

function allowedKeys(args: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(args).some(key => !keys.includes(key))) fail(422, "invalid_tool_arguments");
}

/** Register the internal organization tool endpoint with constant-time token and account checks. */
export async function organizationAgentToolsRoutes(app: FastifyInstance, options: OrganizationAgentToolsOptions): Promise<void> {
  const token = options.token ?? process.env.AGENTS_SERVICE_TOKEN;
  const agentsToken = options.agentsToken ?? process.env.AGENTS_SERVICE_TOKEN;
  const matchingToken = options.matchingToken ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const chatToken = options.chatToken ?? process.env.CHAT_SERVICE_TOKEN;
  const agentsUrl = options.agentsUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
  const matchingUrl = options.matchingUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const chatUrl = options.chatUrl ?? process.env.CHAT_SERVICE_URL ?? "http://chat-service:8103";
  const transport = correlatedFetch(options.transport ?? fetch);

  const ownerIsActiveEmployer = async (ownerId: string): Promise<void> => {
    try {
      const result = await options.pool.query<{ id: string }>(
        "SELECT id FROM accounts WHERE id=$1 AND role='employer' AND email_verified=true AND processing_consent_at IS NOT NULL",
        [ownerId],
      );
      if (result.rowCount !== 1) fail(401, "organization_owner_inactive");
    } catch (error) {
      if (error instanceof ToolBridgeError) throw error;
      fail(503, "organization_owner_unavailable");
    }
  };

  const readUpstream = async (url: URL, init: RequestInit, maxBytes = 8 * 1024 * 1024): Promise<JsonValue> => {
    let response: Response;
    try { response = await transport(url, { ...init, signal: AbortSignal.timeout(15000), redirect: "error" }); }
    catch { fail(503, "tool_upstream_unavailable"); }
    if (!response.ok) fail([400, 404, 409, 422, 429].includes(response.status) ? response.status : 503, response.status === 404 ? "tool_resource_not_found" : "tool_upstream_rejected");
    const reader = response.body?.getReader();
    if (!reader) fail(503, "tool_response_invalid");
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > maxBytes) { await reader.cancel(); fail(503, "tool_response_too_large"); }
      chunks.push(part.value);
    }
    try { return jsonValue(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
    catch (error) { if (error instanceof ToolBridgeError) throw error; fail(503, "tool_response_invalid"); }
  };

  const ownedVacancy = async (ownerId: string, vacancyId: string, open = false): Promise<void> => {
    if (!agentsToken) fail(503, "agents_unavailable");
    const result = await readUpstream(new URL(`/internal/vacancy-drafts/${vacancyId}`, agentsUrl), {
      headers: { "x-internal-token": agentsToken, "x-owner-id": ownerId },
    });
    if (!isRecord(result) || result.owner_id !== ownerId || result.vacancy_id !== vacancyId) fail(404, "vacancy_not_found");
    if (result.state !== "created" || !isRecord(result.publication) || result.publication.status !== "accepted") fail(409, "vacancy_not_published");
    if (open && (result.business_status !== "open" || result.archived === true)) fail(409, "vacancy_not_open");
  };

  const matchingRequest = async (ownerId: string, path: string, method: "GET" | "POST", body?: unknown): Promise<JsonValue> => {
    if (!matchingToken) fail(503, "matching_unavailable");
    return readUpstream(new URL(path, matchingUrl), {
      method, headers: { "x-internal-token": matchingToken, "x-owner-id": ownerId, "content-type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  };

  const requireCurrentCandidates = async (value: JsonValue): Promise<void> => {
    const cards: Array<{ candidate_id: string; profile_revision: number }> = [];
    const scan = (item: JsonValue): void => {
      if (Array.isArray(item)) { item.forEach(scan); return; }
      if (typeof item !== "object" || item === null) return;
      if (typeof item.candidate_id === "string" && typeof item.profile_revision === "number") {
        cards.push({ candidate_id: item.candidate_id, profile_revision: item.profile_revision });
      }
      Object.values(item).forEach(child => scan(child));
    };
    scan(value);
    if (cards.length === 0) return;
    if (!options.profiles) fail(503, "profile_source_unavailable");
    for (const card of cards) if (!await options.profiles.isSelectionCurrent(card.candidate_id, card.profile_revision)) fail(409, "candidate_source_obsolete");
  };

  const chatRequest = async (ownerId: string, processId: string, operation: "history" | "send", payload: object): Promise<JsonValue> => {
    if (!chatToken) fail(503, "chat_unavailable");
    return readUpstream(new URL(`/internal/chat/${operation}`, chatUrl), {
      method: "POST", headers: { "content-type": "application/json", "x-internal-token": chatToken },
      body: JSON.stringify({ ...payload, process_id: processId, actor_id: ownerId }),
      // Chat payloads are bounded by the upstream contract; this is an additional bridge limit.
    }, operation === "send" ? 2 * 1024 * 1024 : 8 * 1024 * 1024);
  };

  const verifyDelegation = async (ownerId: string, processId: string, args: Record<string, unknown>, operation: "read" | "send"): Promise<void> => {
    const delegationId = uuidArg(args, "delegation_id");
    const taskId = uuidArg(args, "task_id");
    if (!options.delegation) fail(503, "agent_delegation_unavailable");
    try {
      if (!await options.delegation.verify({ ownerId, processId, delegationId, taskId, operation })) fail(403, "agent_delegation_denied");
    } catch (error) {
      if (error instanceof ToolBridgeError) throw error;
      fail(503, "agent_delegation_unavailable");
    }
  };

  const dispatch = async (ownerId: string, name: ToolName, input: unknown, envelopeTaskId?: string, envelopeKey?: string): Promise<JsonValue> => {
    const args = argsRecord(input);
    if (envelopeTaskId !== undefined) {
      if (!uuidPattern.test(envelopeTaskId)) fail(422, "invalid_task_id");
      if (args.task_id !== undefined && args.task_id !== envelopeTaskId) fail(409, "task_id_conflict");
      args.task_id = envelopeTaskId;
    }
    const requireEnvelopeKey = (argumentKey: string): string => {
      const value = uuidArg(args, argumentKey);
      if (envelopeKey !== undefined && value !== envelopeKey) fail(409, "idempotency_key_conflict");
      return value;
    };
    switch (name) {
      case "organization_stats": {
        allowedKeys(args, ["from", "to"]);
        return redactPrivate(jsonValue(await options.statistics.get(ownerId, {
          ...(optionalString(args, "from", 10) ? { from: optionalString(args, "from", 10) } : {}),
          ...(optionalString(args, "to", 10) ? { to: optionalString(args, "to", 10) } : {}),
        })));
      }
      case "vacancy_stats": {
        allowedKeys(args, ["from", "to", "vacancy_id"]);
        const vacancyId = uuidArg(args, "vacancy_id");
        return redactPrivate(jsonValue(await options.statistics.get(ownerId, {
          vacancy_id: vacancyId,
          ...(optionalString(args, "from", 10) ? { from: optionalString(args, "from", 10) } : {}),
          ...(optionalString(args, "to", 10) ? { to: optionalString(args, "to", 10) } : {}),
        })));
      }
      case "recruitment_list": {
        allowedKeys(args, ["limit", "offset", "cursor", "draft_id"]);
        const limit = args.limit === undefined ? 50 : intArg(args, "limit", 1, 100);
        const offset = args.offset === undefined ? 0 : intArg(args, "offset", 0, 1000000);
        const draftId = args.draft_id === undefined ? undefined : uuidArg(args, "draft_id");
        const cursor = optionalString(args, "cursor", 1024);
        return redactPrivate(jsonValue(await options.recruitment.list("employer", ownerId, limit, offset, draftId, cursor)));
      }
      case "recruitment_get": {
        allowedKeys(args, ["process_id"]);
        return redactPrivate(jsonValue(await options.recruitment.get("employer", ownerId, uuidArg(args, "process_id"))));
      }
      case "candidate_showcase_search": case "candidate_context_search": {
        allowedKeys(args, ["q", "filters", "limit", "cursor"]);
        const filters = args.filters;
        if (filters !== undefined && !isRecord(filters)) fail(422, "invalid_tool_arguments");
        if (isRecord(filters) && filters.vacancy_id !== undefined) await ownedVacancy(ownerId, uuidArg(filters, "vacancy_id"), true);
        const body: Record<string, unknown> = { q: args.q === undefined ? "" : stringArg(args, "q", 500), limit: args.limit === undefined ? 50 : intArg(args, "limit", 1, 100), cursor: args.cursor === undefined ? null : optionalString(args, "cursor", 4096), ...(filters === undefined ? {} : { filters }) };
        const result = await matchingRequest(ownerId, "/internal/employer/candidates/search", "POST", body);
        await requireCurrentCandidates(result);
        return redactPrivate(result);
      }
      case "candidate_showcase_detail": case "candidate_context_detail": {
        allowedKeys(args, ["candidate_id"]);
        const result = await matchingRequest(ownerId, `/internal/employer/candidates/${uuidArg(args, "candidate_id")}`, "GET");
        await requireCurrentCandidates(result);
        return redactPrivate(result);
      }
      case "candidate_feed": {
        allowedKeys(args, ["vacancy_id", "include_reserve", "limit"]);
        const vacancyId = uuidArg(args, "vacancy_id");
        await ownedVacancy(ownerId, vacancyId, true);
        const body = { include_reserve: args.include_reserve === undefined ? false : args.include_reserve, limit: args.limit === undefined ? 50 : intArg(args, "limit", 1, 100) };
        if (typeof body.include_reserve !== "boolean") fail(422, "invalid_tool_arguments");
        const result = await matchingRequest(ownerId, `/internal/vacancies/${vacancyId}/feed`, "POST", body);
        await requireCurrentCandidates(result);
        return redactPrivate(result);
      }
      case "candidate_feed_continue": {
        allowedKeys(args, ["vacancy_id", "session_id"]);
        const vacancyId = uuidArg(args, "vacancy_id");
        await ownedVacancy(ownerId, vacancyId, true);
        const result = await matchingRequest(ownerId, `/internal/vacancies/${vacancyId}/feed/${uuidArg(args, "session_id")}`, "GET");
        await requireCurrentCandidates(result);
        return redactPrivate(result);
      }
      case "feed_decision": case "feed_impression": {
        const keys = name === "feed_decision" ? ["vacancy_id", "exposure_id", "action", "idempotency_key"] : ["vacancy_id", "exposure_id", "idempotency_key"];
        allowedKeys(args, keys);
        const vacancyId = uuidArg(args, "vacancy_id");
        await ownedVacancy(ownerId, vacancyId, true);
        const body: Record<string, unknown> = { exposure_id: uuidArg(args, "exposure_id"), idempotency_key: requireEnvelopeKey("idempotency_key") };
        if (name === "feed_decision") { const action = stringArg(args, "action", 8); if (action !== "like" && action !== "dislike") fail(422, "invalid_tool_arguments"); body.action = action; }
        return redactPrivate(await matchingRequest(ownerId, `/internal/vacancies/${vacancyId}/feed/${name === "feed_decision" ? "decisions" : "impressions"}`, "POST", body));
      }
      case "candidate_chat_read": {
        allowedKeys(args, ["process_id", "delegation_id", "task_id", "after_sequence", "limit"]);
        const processId = uuidArg(args, "process_id");
        const after = args.after_sequence === undefined ? 0 : intArg(args, "after_sequence", 0, Number.MAX_SAFE_INTEGER);
        const limit = args.limit === undefined ? 50 : intArg(args, "limit", 1, 100);
        const result = await options.recruitment.withChatGrant("employer", ownerId, processId, false, async grant => {
          await verifyDelegation(ownerId, processId, args, "read");
          return chatRequest(ownerId, processId, "history", { ...grant, after_sequence: after, limit });
        });
        return redactPrivate(result);
      }
      case "candidate_chat_send": {
        allowedKeys(args, ["process_id", "delegation_id", "task_id", "idempotency_key", "text", "files"]);
        const processId = uuidArg(args, "process_id");
        const message: SendChatMessage = { idempotency_key: requireEnvelopeKey("idempotency_key"), text: stringArg(args, "text", 16000), files: [] };
        if (args.files !== undefined && (!Array.isArray(args.files) || args.files.length > 0)) fail(422, "agent_chat_attachments_forbidden");
        const result = await options.recruitment.withChatGrant("employer", ownerId, processId, true, async grant => {
          await verifyDelegation(ownerId, processId, args, "send");
          return chatRequest(ownerId, processId, "send", { ...grant, ...message });
        });
        return redactPrivate(result);
      }
      case "interview_propose": {
        allowedKeys(args, ["process_id", "expected_revision", "kind", "starts_at", "time_zone", "duration_minutes", "format", "location", "idempotency_key"]);
        const processId = uuidArg(args, "process_id");
        const command: RecruitmentAction = {
          action: "interview_propose", expected_revision: intArg(args, "expected_revision", 1, Number.MAX_SAFE_INTEGER),
          kind: stringArg(args, "kind", 16) as "technical" | "general", starts_at: stringArg(args, "starts_at", 64), time_zone: stringArg(args, "time_zone", 100),
          duration_minutes: intArg(args, "duration_minutes", 1, 480), format: stringArg(args, "format", 16) as "remote" | "onsite", location: stringArg(args, "location", 2000), idempotency_key: requireEnvelopeKey("idempotency_key"),
        };
        if (!["technical", "general"].includes(command.kind) || !["remote", "onsite"].includes(command.format)) fail(422, "invalid_tool_arguments");
        return redactPrivate(jsonValue(await options.recruitment.act("employer", ownerId, processId, command)));
      }
      case "interview_accept":
        // A service call cannot honestly act as the candidate; the candidate session owns acceptance.
        fail(403, "candidate_action_requires_candidate_session");
    }
  };

  app.post<{ Params: { tool: string }; Body: { arguments: unknown; idempotency_key: string; task_id?: string } }>("/internal/organization-agent/tools/:tool", {
    schema: {
      tags: ["organization-agent"], security: [{ internalToken: [] }],
      params: { type: "object", additionalProperties: false, required: ["tool"], properties: { tool: { type: "string", enum: [...tools] } } },
      // Fastify exposes standard Host/User-Agent headers alongside these two required credentials.
      headers: { type: "object", required: ["x-internal-token", "x-owner-id"], properties: {
        "x-internal-token": { type: "string", minLength: 1, maxLength: 4096 }, "x-owner-id": { type: "string", format: "uuid" },
      } },
      body: { type: "object", additionalProperties: false, required: ["arguments", "idempotency_key"], properties: {
        arguments: { type: "object", maxProperties: 40 }, idempotency_key: { type: "string", format: "uuid" }, task_id: { type: "string", format: "uuid" },
      } },
      response: { 200: { type: "object", additionalProperties: false, required: ["result"], properties: { result: {} } },
        400: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
        401: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
        403: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
        409: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
        422: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } },
        503: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } } },
    },
    handler: async (request, reply) => {
      reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff");
      if (!token) return reply.code(503).send({ detail: "organization_agent_auth_not_configured" });
      const supplied = request.headers["x-internal-token"];
      if (typeof supplied !== "string" || Buffer.byteLength(supplied) !== Buffer.byteLength(token)
        || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) return reply.code(401).send({ detail: "invalid_internal_token" });
      const ownerId = request.headers["x-owner-id"];
      if (typeof ownerId !== "string" || !uuidPattern.test(ownerId)) return reply.code(401).send({ detail: "invalid_owner" });
      try {
        await ownerIsActiveEmployer(ownerId);
        const name = request.params.tool as ToolName;
        const result = await dispatch(ownerId, name, request.body.arguments, request.body.task_id, request.body.idempotency_key);
        return reply.send({ result });
      } catch (error) {
        if (error instanceof ToolBridgeError) return reply.code(error.status).send({ detail: error.code });
        if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
        if (error instanceof StatisticsError) return reply.code(error.status).send({ detail: error.code });
        return reply.code(503).send({ detail: "organization_agent_tool_unavailable" });
      }
    },
  });
}
