import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";
import { organizationAgentToolsRoutes, type OrganizationAgentToolsOptions } from "../src/routes/organization-agent-tools.js";

const owner = "00000000-0000-4000-8000-000000000001";
const vacancy = "00000000-0000-4000-8000-000000000002";
const token = "organization-agent-internal-token";

function receiptPool(baseQuery: (sql: string, values?: unknown[]) => Promise<{ rowCount: number; rows: unknown[] }> = async () => ({ rowCount: 1, rows: [{ id: owner }] })): OrganizationAgentToolsOptions["pool"] {
  const receipts = new Map<string, { tool: string; request_hash: string; status: "started" | "completed"; result: unknown; updated_at: Date }>();
  const client = {
    query: async (sql: string, values?: unknown[]) => {
      if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK" || sql.includes("pg_advisory_xact_lock")) return { rowCount: 0, rows: [] };
      if (sql.includes("FOR UPDATE")) {
        const receipt = receipts.get(String(values?.[1]));
        return receipt ? { rowCount: 1, rows: [receipt] } : { rowCount: 0, rows: [] };
      }
      if (sql.startsWith("INSERT INTO organization_agent_tool_receipts")) {
        receipts.set(String(values?.[1]), { tool: String(values?.[2]), request_hash: String(values?.[3]), status: "started", result: null, updated_at: new Date() });
        return { rowCount: 1, rows: [] };
      }
      if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='started'")) {
        const receipt = receipts.get(String(values?.[1]));
        if (receipt) { receipt.status = "started"; receipt.result = null; receipt.updated_at = new Date(); }
        return { rowCount: 1, rows: [] };
      }
      throw new Error(`unexpected receipt client query: ${sql}`);
    },
    release: () => undefined,
  };
  return {
    connect: async () => client,
    query: async (sql: string, values?: unknown[]) => {
      if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='completed'")) {
        const receipt = receipts.get(String(values?.[1]));
        if (receipt) { receipt.status = "completed"; receipt.result = values?.[3] ?? null; receipt.updated_at = new Date(); }
        return { rowCount: receipt ? 1 : 0, rows: [] };
      }
      return baseQuery(sql, values);
    },
  } as unknown as OrganizationAgentToolsOptions["pool"];
}

function options(overrides: Partial<OrganizationAgentToolsOptions> = {}): OrganizationAgentToolsOptions {
  const pool = receiptPool();
  const recruitment = {
    list: async () => ({ items: [], limit: 50, offset: 0, has_more: false, next_cursor: null }),
    get: async () => ({ id: "00000000-0000-4000-8000-000000000003" }),
    withChatGrant: async () => { throw new Error("chat should not be called"); },
  } as unknown as OrganizationAgentToolsOptions["recruitment"];
  const statistics = { get: async () => ({ version: "employer-statistics-v1", current: {}, previous: {} }) } as unknown as OrganizationAgentToolsOptions["statistics"];
  return { token, pool, recruitment, statistics, transport: async () => Response.json({}), ...overrides };
}

test("organization agent bridge authenticates with a constant-time internal token", async () => {
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  let queries = 0;
  await app.register(organizationAgentToolsRoutes, options({ pool: { query: async () => { queries++; return { rowCount: 1, rows: [{ id: owner }] }; } } as unknown as OrganizationAgentToolsOptions["pool"] }));
  try {
    const response = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/organization_stats",
      headers: { "x-internal-token": "wrong", "x-owner-id": owner }, payload: { arguments: {}, idempotency_key: vacancy } });
    assert.equal(response.statusCode, 401);
    assert.equal(queries, 0);
  } finally { await app.close(); }
});

test("organization stats and recruitment reads are owner scoped and idempotency envelope is accepted", async () => {
  const calls: string[] = [];
  const configured = options({
    statistics: { get: async (id) => { calls.push(`stats:${id}`); return { value: 1 }; } } as unknown as OrganizationAgentToolsOptions["statistics"],
    recruitment: { list: async (_role, id, limit, offset) => { calls.push(`list:${id}:${limit}:${offset}`); return { items: [], limit, offset, has_more: false, next_cursor: null }; } } as unknown as OrganizationAgentToolsOptions["recruitment"],
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const stats = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/organization_stats", headers,
      payload: { arguments: {}, idempotency_key: vacancy, task_id: vacancy } });
    assert.equal(stats.statusCode, 200, stats.body);
    assert.deepEqual(stats.json(), { result: { value: 1 } });
    const list = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/recruitment_list", headers,
      payload: { arguments: { limit: 2, offset: 1 }, idempotency_key: "00000000-0000-4000-8000-000000000004" } });
    assert.equal(list.statusCode, 200, list.body);
    assert.deepEqual(calls, [`stats:${owner}`, `list:${owner}:2:1`]);
  } finally { await app.close(); }
});

test("feed bridge forwards only after an owned published vacancy check", async () => {
  const paths: string[] = [];
  const configured = options({
    agentsToken: "agents-token", matchingToken: "matching-token",
    profiles: { isSelectionCurrent: async () => true } as unknown as OrganizationAgentToolsOptions["profiles"],
    transport: async (input) => {
      const url = new URL(String(input)); paths.push(url.pathname);
      if (url.pathname === `/internal/vacancy-drafts/${vacancy}`) return Response.json({ owner_id: owner, vacancy_id: vacancy, state: "created", publication: { status: "accepted" }, business_status: "open" });
      return Response.json({ items: [] });
    },
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const response = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_feed", headers: { "x-internal-token": token, "x-owner-id": owner },
      payload: { arguments: { vacancy_id: vacancy, limit: 1 }, idempotency_key: vacancy } });
    assert.equal(response.statusCode, 200, response.body);
    assert.deepEqual(paths, [`/internal/vacancy-drafts/${vacancy}`, `/internal/vacancies/${vacancy}/feed`]);
  } finally { await app.close(); }
});

test("read receipts freeze a feed snapshot while rechecking vacancy ownership", async () => {
  const paths: string[] = [];
  let current = true;
  const configured = options({
    agentsToken: "agents-token", matchingToken: "matching-token",
    profiles: { isSelectionCurrent: async () => current } as unknown as OrganizationAgentToolsOptions["profiles"],
    transport: async (input) => {
      const url = new URL(String(input)); paths.push(url.pathname);
      if (url.pathname === `/internal/vacancy-drafts/${vacancy}`) return Response.json({ owner_id: owner, vacancy_id: vacancy, state: "created", publication: { status: "accepted" }, business_status: "open" });
      return Response.json({ cards: [{ candidate_id: "00000000-0000-4000-8000-000000000009", profile_revision: 1, exposure_id: vacancy }] });
    },
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const payload = { arguments: { vacancy_id: vacancy, limit: 1 }, idempotency_key: "00000000-0000-4000-8000-000000000010" };
    const first = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_feed", headers, payload });
    const replay = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_feed", headers, payload });
    assert.equal(first.statusCode, 200, first.body);
    assert.equal(replay.statusCode, 200, replay.body);
    assert.deepEqual(replay.json(), first.json());
    assert.deepEqual(paths, [
      `/internal/vacancy-drafts/${vacancy}`, `/internal/vacancies/${vacancy}/feed`,
      `/internal/vacancy-drafts/${vacancy}`,
    ]);
    current = false;
    const stale = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_feed", headers, payload });
    assert.equal(stale.statusCode, 409, stale.body);
    assert.deepEqual(paths, [
      `/internal/vacancy-drafts/${vacancy}`, `/internal/vacancies/${vacancy}/feed`,
      `/internal/vacancy-drafts/${vacancy}`, `/internal/vacancy-drafts/${vacancy}`,
    ]);
  } finally { await app.close(); }
});

test("bridge cannot impersonate candidate interview acceptance and chat requires delegation", async () => {
  const configured = options({ delegation: { verify: async () => false }, recruitment: {
    withChatGrant: async (_role, _ownerId, processId, _write, operation) => operation({ process_id: processId, owner_id: owner, candidate_id: vacancy, actor_id: owner }),
  } as unknown as OrganizationAgentToolsOptions["recruitment"] });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const accept = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/interview_accept", headers,
      payload: { arguments: {}, idempotency_key: vacancy } });
    assert.equal(accept.statusCode, 403);
    const chat = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_read", headers,
      payload: { arguments: { process_id: vacancy, delegation_id: vacancy, task_id: vacancy }, idempotency_key: vacancy } });
    assert.equal(chat.statusCode, 403);
  } finally { await app.close(); }
});

test("interview proposal requires an active delegated task proof in the envelope", async () => {
  const verified: { ownerId?: string; processId?: string; delegationId?: string; taskId?: string; operation?: string; leaseToken?: string } = {};
  const receiptClient = {
    query: async (sql: string) => {
      if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK" || sql.includes("pg_advisory_xact_lock")) return { rowCount: 0, rows: [] };
      if (sql.includes("FOR UPDATE")) return { rowCount: 0, rows: [] };
      if (sql.startsWith("INSERT INTO organization_agent_tool_receipts")) return { rowCount: 1, rows: [] };
      throw new Error(`unexpected receipt client query: ${sql}`);
    },
    release: () => undefined,
  };
  const configured = options({
    pool: {
      connect: async () => receiptClient,
      query: async (sql: string) => {
        if (sql.includes("FROM accounts")) return { rowCount: 1, rows: [{ id: owner }] };
        if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='completed'")) return { rowCount: 1, rows: [] };
        return { rowCount: 0, rows: [] };
      },
    } as unknown as OrganizationAgentToolsOptions["pool"],
    delegation: { verify: async (proof) => { Object.assign(verified, proof); return true; } },
    recruitment: { act: async () => ({ status: "proposed" }) } as unknown as OrganizationAgentToolsOptions["recruitment"],
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const missingProof = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/interview_propose",
      headers: { "x-internal-token": token, "x-owner-id": owner }, payload: {
        arguments: { process_id: vacancy, expected_revision: 1, kind: "general", starts_at: "2030-01-01T10:00:00Z", time_zone: "UTC", duration_minutes: 30, format: "remote", location: "online", idempotency_key: "00000000-0000-4000-8000-000000000003" },
        idempotency_key: "00000000-0000-4000-8000-000000000003",
      } });
    assert.equal(missingProof.statusCode, 403);
    const response = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/interview_propose",
      headers: { "x-internal-token": token, "x-owner-id": owner }, payload: {
        arguments: { process_id: vacancy, expected_revision: 1, kind: "general", starts_at: "2030-01-01T10:00:00Z", time_zone: "UTC", duration_minutes: 30, format: "remote", location: "online", idempotency_key: vacancy },
        idempotency_key: vacancy, task_id: vacancy, delegation_id: vacancy, lease_token: vacancy,
      } });
    assert.equal(response.statusCode, 200, response.body);
    assert.deepEqual(verified, { ownerId: owner, processId: vacancy, delegationId: vacancy, taskId: vacancy, operation: "propose", leaseToken: vacancy });
  } finally { await app.close(); }
});

test("default delegation verifier binds task, process, lease and live employer presence before chat read", async () => {
  let expired = false;
  const configured = options({
    chatToken: "chat-token", chatUrl: "http://chat.test",
    pool: receiptPool(async (sql: string) => {
      if (sql.includes("FROM accounts")) return { rowCount: 1, rows: [{ id: owner }] };
      if (sql.includes("organization_tasks")) return { rowCount: 1, rows: [{ kind: "inquiry", status: "processing", lease_until: expired ? new Date(Date.now() - 1000) : new Date(Date.now() + 60000), lease_token: vacancy, presence: !expired, last_seen_at: new Date(), chat_status: "active", conversation_refs: { process_id: vacancy, delegation_id: vacancy, expires_at: new Date(Date.now() + 60000).toISOString(), allowed_operations: ["candidate_chat_read"] } }] };
      return { rowCount: 0, rows: [] };
    }),
    recruitment: { withChatGrant: async (_role, _ownerId, processId, _write, operation) => operation({ process_id: processId, owner_id: owner, candidate_id: vacancy, actor_id: owner }) } as unknown as OrganizationAgentToolsOptions["recruitment"],
    transport: async () => Response.json({ messages: [], next_sequence: 0 }),
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const payload = { arguments: { process_id: vacancy, delegation_id: vacancy, task_id: vacancy }, idempotency_key: vacancy };
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_read", headers, payload })).statusCode, 200);
    expired = true;
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_read", headers, payload })).statusCode, 403);
  } finally { await app.close(); }
});

test("business writes reserve one owner/key before the effect and replay the completed receipt", async () => {
  const receipt = { tool: "feed_decision", request_hash: "", status: "started" as "started" | "completed", result: null as Record<string, unknown> | null, updated_at: new Date() };
  let matchingCalls = 0;
  const client = {
    query: async (sql: string, values?: unknown[]) => {
      if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK") return { rowCount: 0, rows: [] };
      if (sql.includes("pg_advisory_xact_lock")) return { rowCount: 1, rows: [] };
      if (sql.includes("FOR UPDATE")) return { rowCount: receipt.request_hash ? 1 : 0, rows: receipt.request_hash ? [receipt] : [] };
      if (sql.startsWith("INSERT INTO organization_agent_tool_receipts")) { receipt.request_hash = String(values?.[3]); receipt.tool = String(values?.[2]); return { rowCount: 1, rows: [] }; }
      if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='started'")) { receipt.status = "started"; receipt.result = null; return { rowCount: 1, rows: [] }; }
      throw new Error(`unexpected client query: ${sql}`);
    },
    release: () => undefined,
  };
  const configured = options({
    pool: {
      connect: async () => client,
      query: async (sql: string, values?: unknown[]) => {
        if (sql.includes("FROM accounts")) return { rowCount: 1, rows: [{ id: owner }] };
        if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='completed'")) { receipt.status = "completed"; receipt.result = { ok: true }; return { rowCount: 1, rows: [] }; }
        throw new Error(`unexpected pool query: ${sql}`);
      },
    } as unknown as OrganizationAgentToolsOptions["pool"],
    agentsToken: "agents-token", matchingToken: "matching-token",
    transport: async (input) => {
      const path = new URL(String(input)).pathname;
      if (path.includes("vacancy-drafts")) return Response.json({ owner_id: owner, vacancy_id: vacancy, state: "created", publication: { status: "accepted" }, business_status: "open" });
      matchingCalls++;
      return Response.json({ ok: true });
    },
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const payload = { arguments: { vacancy_id: vacancy, exposure_id: vacancy, action: "like", idempotency_key: vacancy }, idempotency_key: vacancy };
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/feed_decision", headers, payload })).statusCode, 200);
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/feed_decision", headers, payload })).statusCode, 200);
    assert.equal(matchingCalls, 1);
    receipt.status = "started";
    receipt.result = null;
    receipt.updated_at = new Date(Date.now() - 60000);
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/feed_decision", headers, payload })).statusCode, 200);
    assert.equal(matchingCalls, 2);
  } finally { await app.close(); }
});

test("stand-in chat send requires a fresh explicit offline heartbeat and task lease", async () => {
  const state: { presence: boolean; seen: Date | null; lease: Date } = { presence: false, seen: new Date(), lease: new Date(Date.now() + 60000) };
  const receiptClient = {
    query: async (sql: string) => {
      if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK") return { rowCount: 0, rows: [] };
      if (sql.includes("pg_advisory_xact_lock")) return { rowCount: 1, rows: [] };
      if (sql.includes("FOR UPDATE")) return { rowCount: 0, rows: [] };
      if (sql.startsWith("INSERT INTO organization_agent_tool_receipts") || sql.startsWith("UPDATE organization_agent_tool_receipts SET status='started'")) return { rowCount: 1, rows: [] };
      throw new Error(`unexpected receipt client query: ${sql}`);
    },
    release: () => undefined,
  };
  const configured = options({
    chatToken: "chat-token", chatUrl: "http://chat.test",
    pool: {
      connect: async () => receiptClient,
      query: async (sql: string) => {
        if (sql.includes("FROM accounts")) return { rowCount: 1, rows: [{ id: owner }] };
        if (sql.includes("organization_tasks")) return { rowCount: 1, rows: [{ kind: "stand_in", status: "processing", lease_until: state.lease, lease_token: vacancy, presence: state.presence, last_seen_at: state.seen, chat_status: "active", conversation_refs: { process_ids: [vacancy], delegation_id: vacancy, expires_at: new Date(Date.now() + 60000).toISOString(), allowed_operations: ["candidate_chat_send"] } }] };
        if (sql.startsWith("UPDATE organization_agent_tool_receipts SET status='completed'")) return { rowCount: 1, rows: [] };
        return { rowCount: 0, rows: [] };
      },
    } as unknown as OrganizationAgentToolsOptions["pool"],
    recruitment: { withChatGrant: async (_role, _ownerId, processId, _write, operation) => operation({ process_id: processId, owner_id: owner, candidate_id: vacancy, actor_id: owner }) } as unknown as OrganizationAgentToolsOptions["recruitment"],
    transport: async () => Response.json({ id: vacancy, sequence: 1, sender_id: owner, created_at: new Date().toISOString(), text: "ok", files: [] }),
  });
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  await app.register(organizationAgentToolsRoutes, configured);
  try {
    const headers = { "x-internal-token": token, "x-owner-id": owner };
    const payload = { arguments: { process_id: vacancy, delegation_id: vacancy, task_id: vacancy, lease_token: vacancy, idempotency_key: vacancy, text: "hello" }, idempotency_key: vacancy };
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload })).statusCode, 200);
    state.presence = true;
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload: { ...payload, idempotency_key: "00000000-0000-4000-8000-000000000004", arguments: { ...payload.arguments, idempotency_key: "00000000-0000-4000-8000-000000000004" } } })).statusCode, 403);
    state.seen = new Date(Date.now() - 180000);
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload: { ...payload, idempotency_key: "00000000-0000-4000-8000-000000000006", arguments: { ...payload.arguments, idempotency_key: "00000000-0000-4000-8000-000000000006" } } })).statusCode, 200);
    state.presence = false;
    state.seen = null;
    assert.equal((await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload: { ...payload, idempotency_key: "00000000-0000-4000-8000-000000000005", arguments: { ...payload.arguments, idempotency_key: "00000000-0000-4000-8000-000000000005" } } })).statusCode, 403);
  } finally { await app.close(); }
});
