/** Trusted, owner-scoped tool bridge for the organization agent runtime. */
import { createHash, timingSafeEqual } from "node:crypto";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { Pool } from "pg";
import type { JsonValue, RecruitmentAction, SendChatMessage } from "@comit/shared";
import { correlatedFetch } from "../operations/index.js";
import { EmployerStatisticsStore, StatisticsError } from "../statistics/store.js";
import { RecruitmentError, type RecruitmentStore } from "../recruitment/store.js";
import type { ProfileStore } from "../profiles/store.js";
import type { VacancyLifecycle } from "../recruitment/vacancy-lifecycle.js";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const tools = [
  "organization_stats", "vacancy_stats", "recruitment_list", "recruitment_get",
  "candidate_showcase_search", "candidate_showcase_detail", "candidate_context_search", "candidate_context_detail",
  "candidate_feed", "candidate_feed_continue", "feed_decision", "feed_impression",
  "candidate_chat_read", "candidate_chat_send", "interview_propose", "interview_accept",
] as const;
type ToolName = typeof tools[number];
type DelegationOperation = "read" | "send" | "propose";

/** Service-side authorization proof for agent access to a participant chat. */
export interface OrganizationAgentDelegationVerifier {
  /** Check owner, process, task, operation and expiry in the authoritative delegation store. */
  verify(input: { ownerId: string; processId: string; delegationId: string; taskId: string; operation: DelegationOperation; leaseToken?: string }): Promise<boolean>;
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
  vacancyLifecycle?: VacancyLifecycle;
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
  const privateKeys = new Set(["email", "phone", "contacts", "resume_url", "data_base64", "access_token", "authorization", "token", "secret", "private_key"]);
  return Object.fromEntries(Object.entries(value).filter(([key]) => !privateKeys.has(key)).map(([key, item]) => [key, redactPrivate(item, depth + 1)]));
}

function allowedKeys(args: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(args).some(key => !keys.includes(key))) fail(422, "invalid_tool_arguments");
}

function fingerprint(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value, (_key, item: unknown) => {
    if (!isRecord(item)) return item;
    return Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)));
  }), "utf8").digest("hex");
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

  const databaseDelegation: OrganizationAgentDelegationVerifier = {
    verify: async ({ ownerId, processId, delegationId, taskId, operation, leaseToken }) => {
      try {
        const result = await options.pool.query<{
          kind: string; status: string; lease_until: Date | string | null; lease_token: string | null; presence: boolean; last_seen_at: Date | string | null; chat_status: string;
          conversation_refs: Record<string, unknown>;
        }>(`SELECT t.kind,t.status,t.lease_until,t.conversation_refs,c.presence,c.status AS chat_status
          ,t.lease_token,c.last_seen_at
          FROM agents_service.organization_tasks t
          JOIN agents_service.organization_chats c ON c.chat_id=t.chat_id
          WHERE t.task_id=$1 AND t.owner_id=$2`, [taskId, ownerId]);
        const row = result.rows[0];
        if (!row || !["queued", "processing", "retrying"].includes(row.status) || row.chat_status !== "active") return false;
        const leaseUntil = row.lease_until ? new Date(row.lease_until).getTime() : null;
        if (leaseUntil !== null && (!Number.isFinite(leaseUntil) || leaseUntil <= Date.now())) return false;
        const refs = row.conversation_refs;
        const processIds = refs.process_ids;
        if (Array.isArray(processIds) ? !processIds.includes(processId) : refs.process_id !== processId) return false;
        const delegationExpiresAt = typeof refs.expires_at === "string" ? Date.parse(refs.expires_at) : Number.NaN;
        if (refs.delegation_id !== delegationId || !Number.isFinite(delegationExpiresAt) || delegationExpiresAt <= Date.now()) return false;
        const allowed = refs.allowed_operations;
        const requiredOperation = operation === "send" ? "candidate_chat_send" : operation === "propose" ? "interview_propose" : "candidate_chat_read";
        if (!Array.isArray(allowed) || !allowed.includes(requiredOperation)) return false;
        if (operation === "send" || operation === "propose") {
          if (row.status !== "processing" || leaseUntil === null || leaseUntil <= Date.now() || !row.lease_token || row.lease_token !== leaseToken) return false;
        }
        if (row.kind === "stand_in" && operation === "send") {
          const seenAt = row.last_seen_at ? new Date(row.last_seen_at).getTime() : null;
          // A missing heartbeat is unknown; a stale heartbeat makes a prior
          // online presence offline after the bounded freshness window.
          if (seenAt === null || !Number.isFinite(seenAt)) return false;
          const effectiveOnline = row.presence && seenAt >= Date.now() - 120_000;
          if (effectiveOnline) return false;
        }
        return true;
      } catch {
        fail(503, "agent_delegation_unavailable");
      }
    },
  };

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
    if (open && options.vacancyLifecycle && await options.vacancyLifecycle.pending(vacancyId)) fail(409, "vacancy_lifecycle_pending");
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

  const verifyDelegation = async (ownerId: string, processId: string,
    proof: { delegationId: string; taskId: string; leaseToken?: string }, operation: DelegationOperation): Promise<void> => {
    const delegationId = proof.delegationId;
    const taskId = proof.taskId;
    const leaseToken = operation === "read" ? undefined : proof.leaseToken;
    const verifier = options.delegation ?? databaseDelegation;
    try {
      if (!await verifier.verify({ ownerId, processId, delegationId, taskId, operation, ...(leaseToken ? { leaseToken } : {}) })) fail(403, "agent_delegation_denied");
    } catch (error) {
      if (error instanceof ToolBridgeError) throw error;
      fail(503, "agent_delegation_unavailable");
    }
  };

  // Every bridge operation that reads an external or mutable store gets a
  // durable owner/key snapshot.  Replays still perform the owner and
  // delegation/current-profile checks below before returning that snapshot.
  const receiptTools = new Set<ToolName>(tools.filter(name => name !== "interview_accept"));
  const readReceiptTools = new Set<ToolName>([
    "organization_stats", "vacancy_stats", "recruitment_list", "recruitment_get",
    "candidate_showcase_search", "candidate_showcase_detail", "candidate_context_search", "candidate_context_detail",
    "candidate_feed", "candidate_feed_continue", "candidate_chat_read",
  ]);
  type ReceiptReservation = { claimed: boolean; result?: JsonValue };
  const reserveReceipt = async (ownerId: string, key: string, tool: ToolName, hash: string): Promise<ReceiptReservation> => {
    const client = await options.pool.connect();
    try {
      await client.query("BEGIN");
      // Lock the owner/key pair even when the receipt row does not exist yet.
      // The transaction stays open only for this reservation, never across the
      // downstream side effect.
      await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1::text || ':' || $2::text, 0))", [ownerId, key]);
      const existing = (await client.query<{ tool: string; request_hash: string; status: "started" | "completed"; result: JsonValue | null; updated_at: Date | string }>(
        "SELECT tool,request_hash,status,result,updated_at FROM organization_agent_tool_receipts WHERE owner_id=$1 AND idempotency_key=$2 FOR UPDATE", [ownerId, key])).rows[0];
      if (!existing) {
        await client.query("INSERT INTO organization_agent_tool_receipts(owner_id,idempotency_key,tool,request_hash,status) VALUES($1,$2,$3,$4,'started')", [ownerId, key, tool, hash]);
        await client.query("COMMIT");
        return { claimed: true };
      }
      if (existing.tool !== tool || existing.request_hash !== hash) fail(409, "idempotency_payload_conflict");
      if (existing.status === "completed" && existing.result !== null) {
        await client.query("COMMIT");
        return { claimed: false, result: jsonValue(existing.result) };
      }
      if (new Date(existing.updated_at).getTime() > Date.now() - 30_000) fail(409, "idempotency_in_flight");
      await client.query("UPDATE organization_agent_tool_receipts SET status='started',result=NULL,updated_at=now() WHERE owner_id=$1 AND idempotency_key=$2", [ownerId, key]);
      await client.query("COMMIT");
      return { claimed: true };
    } catch (error) {
      try { await client.query("ROLLBACK"); } catch { /* Preserve the original error. */ }
      if (error instanceof ToolBridgeError) throw error;
      fail(503, "organization_agent_receipts_unavailable");
    } finally { client.release(); }
  };
  const completeReceipt = async (ownerId: string, key: string, hash: string, result: JsonValue): Promise<void> => {
    try {
      const updated = await options.pool.query("UPDATE organization_agent_tool_receipts SET status='completed',result=$4,updated_at=now() WHERE owner_id=$1 AND idempotency_key=$2 AND request_hash=$3 AND status='started'", [ownerId, key, hash, result]);
      if (updated.rowCount !== 1) fail(503, "organization_agent_receipts_unavailable");
    } catch (error) {
      if (error instanceof ToolBridgeError) throw error;
      fail(503, "organization_agent_receipts_unavailable");
    }
  };

  const prepareArguments = (name: ToolName, input: unknown, envelopeTaskId?: string,
    envelopeDelegationId?: string, envelopeLeaseToken?: string): Record<string, unknown> => {
    const args = { ...argsRecord(input) };
    if (envelopeTaskId !== undefined) {
      if (!uuidPattern.test(envelopeTaskId)) fail(422, "invalid_task_id");
      if (name === "candidate_chat_read" || name === "candidate_chat_send") {
        if (args.task_id !== undefined && args.task_id !== envelopeTaskId) fail(409, "task_id_conflict");
        args.task_id = envelopeTaskId;
      }
    }
    if (envelopeDelegationId !== undefined) {
      if (!uuidPattern.test(envelopeDelegationId)) fail(422, "invalid_delegation_id");
      if (name === "candidate_chat_read" || name === "candidate_chat_send") {
        if (args.delegation_id !== undefined && args.delegation_id !== envelopeDelegationId) fail(409, "delegation_id_conflict");
        args.delegation_id = envelopeDelegationId;
      } else if (name !== "interview_propose") {
        fail(422, "invalid_tool_arguments");
      }
    }
    if (envelopeLeaseToken !== undefined) {
      if (!uuidPattern.test(envelopeLeaseToken)) fail(422, "invalid_lease_token");
      if (name === "candidate_chat_send") {
        if (args.lease_token !== undefined && args.lease_token !== envelopeLeaseToken) fail(409, "lease_token_conflict");
        args.lease_token = envelopeLeaseToken;
      } else if (name !== "interview_propose") {
        fail(422, "invalid_tool_arguments");
      }
    }
    return args;
  };

  const reauthorizeCachedRead = async (ownerId: string, name: ToolName, input: unknown,
    envelopeTaskId: string | undefined, envelopeDelegationId: string | undefined,
    envelopeLeaseToken: string | undefined, cached: JsonValue): Promise<void> => {
    const args = prepareArguments(name, input, envelopeTaskId, envelopeDelegationId, envelopeLeaseToken);
    switch (name) {
      case "candidate_showcase_search": case "candidate_context_search": {
        allowedKeys(args, ["q", "filters", "limit", "cursor"]);
        const filters = args.filters;
        if (filters !== undefined && !isRecord(filters)) fail(422, "invalid_tool_arguments");
        if (isRecord(filters) && filters.vacancy_id !== undefined) await ownedVacancy(ownerId, uuidArg(filters, "vacancy_id"), true);
        await requireCurrentCandidates(cached);
        return;
      }
      case "candidate_showcase_detail": case "candidate_context_detail":
        allowedKeys(args, ["candidate_id"]);
        await requireCurrentCandidates(cached);
        return;
      case "candidate_feed": {
        allowedKeys(args, ["vacancy_id", "include_reserve", "limit"]);
        await ownedVacancy(ownerId, uuidArg(args, "vacancy_id"), true);
        await requireCurrentCandidates(cached);
        return;
      }
      case "candidate_feed_continue":
        allowedKeys(args, ["vacancy_id", "session_id"]);
        await ownedVacancy(ownerId, uuidArg(args, "vacancy_id"), true);
        await requireCurrentCandidates(cached);
        return;
      case "candidate_chat_read": {
        allowedKeys(args, ["process_id", "delegation_id", "task_id", "after_sequence", "limit"]);
        const processId = uuidArg(args, "process_id");
        await options.recruitment.withChatGrant("employer", ownerId, processId, false, async grant => {
          await verifyDelegation(ownerId, processId, { delegationId: uuidArg(args, "delegation_id"), taskId: uuidArg(args, "task_id") }, "read");
          return grant;
        });
        return;
      }
      default:
        return;
    }
  };

  const dispatch = async (ownerId: string, name: ToolName, input: unknown, envelopeTaskId?: string, envelopeKey?: string,
    envelopeDelegationId?: string, envelopeLeaseToken?: string): Promise<JsonValue> => {
    const args = prepareArguments(name, input, envelopeTaskId, envelopeDelegationId, envelopeLeaseToken);
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
          await verifyDelegation(ownerId, processId, { delegationId: uuidArg(args, "delegation_id"), taskId: uuidArg(args, "task_id") }, "read");
          return chatRequest(ownerId, processId, "history", { ...grant, after_sequence: after, limit });
        });
        return redactPrivate(result);
      }
      case "candidate_chat_send": {
        allowedKeys(args, ["process_id", "delegation_id", "task_id", "lease_token", "idempotency_key", "text", "files"]);
        const processId = uuidArg(args, "process_id");
        const message: SendChatMessage = { idempotency_key: requireEnvelopeKey("idempotency_key"), text: stringArg(args, "text", 16000), files: [] };
        if (args.files !== undefined && (!Array.isArray(args.files) || args.files.length > 0)) fail(422, "agent_chat_attachments_forbidden");
        const result = await options.recruitment.withChatGrant("employer", ownerId, processId, true, async grant => {
          await verifyDelegation(ownerId, processId, { delegationId: uuidArg(args, "delegation_id"), taskId: uuidArg(args, "task_id"), leaseToken: uuidArg(args, "lease_token") }, "send");
          return chatRequest(ownerId, processId, "send", { ...grant, ...message });
        });
        return redactPrivate(result);
      }
      case "interview_propose": {
        allowedKeys(args, ["process_id", "expected_revision", "kind", "starts_at", "time_zone", "duration_minutes", "format", "location", "idempotency_key"]);
        const processId = uuidArg(args, "process_id");
        if (!envelopeTaskId || !envelopeDelegationId || !envelopeLeaseToken) fail(403, "agent_delegation_required");
        await verifyDelegation(ownerId, processId, { delegationId: envelopeDelegationId, taskId: envelopeTaskId, leaseToken: envelopeLeaseToken }, "propose");
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

  app.post<{ Params: { tool: string }; Body: { arguments: unknown; idempotency_key: string; task_id?: string; delegation_id?: string; lease_token?: string } }>("/internal/organization-agent/tools/:tool", {
    schema: {
      tags: ["organization-agent"], security: [{ internalToken: [] }],
      params: { type: "object", additionalProperties: false, required: ["tool"], properties: { tool: { type: "string", enum: [...tools] } } },
      // Fastify exposes standard Host/User-Agent headers alongside these two required credentials.
      headers: { type: "object", required: ["x-internal-token", "x-owner-id"], properties: {
        "x-internal-token": { type: "string", minLength: 1, maxLength: 4096 }, "x-owner-id": { type: "string", format: "uuid" },
      } },
      body: { type: "object", additionalProperties: false, required: ["arguments", "idempotency_key"], properties: {
        arguments: { type: "object", maxProperties: 40 }, idempotency_key: { type: "string", format: "uuid" },
        task_id: { type: "string", format: "uuid" }, delegation_id: { type: "string", format: "uuid" }, lease_token: { type: "string", format: "uuid" },
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
        const hash = fingerprint({ tool: name, arguments: request.body.arguments, task_id: request.body.task_id ?? null, delegation_id: request.body.delegation_id ?? null, lease_token: request.body.lease_token ?? null });
        const reservation = receiptTools.has(name) ? await reserveReceipt(ownerId, request.body.idempotency_key, name, hash) : { claimed: true };
        if (!reservation.claimed && readReceiptTools.has(name)) {
          // A completed read receipt is a frozen upstream snapshot.  Recheck
          // current owner/delegation/profile authority before returning it;
          // this intentionally does not repeat the external read itself.
          await reauthorizeCachedRead(ownerId, name, request.body.arguments,
            request.body.task_id, request.body.delegation_id, request.body.lease_token, reservation.result ?? null);
        }
        const result = reservation.claimed
          ? await dispatch(ownerId, name, request.body.arguments, request.body.task_id, request.body.idempotency_key, request.body.delegation_id, request.body.lease_token)
          : (reservation.result ?? null);
        if (receiptTools.has(name) && reservation.claimed) await completeReceipt(ownerId, request.body.idempotency_key, hash, result);
        const durable = result;
        return reply.send({ result: durable });
      } catch (error) {
        if (error instanceof ToolBridgeError) return reply.code(error.status).send({ detail: error.code });
        if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
        if (error instanceof StatisticsError) return reply.code(error.status).send({ detail: error.code });
        return reply.code(503).send({ detail: "organization_agent_tool_unavailable" });
      }
    },
  });
}
