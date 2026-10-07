import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";
import { organizationAgentToolsRoutes, type OrganizationAgentToolsOptions } from "../src/routes/organization-agent-tools.js";

const owner = "00000000-0000-4000-8000-000000000001";
const vacancy = "00000000-0000-4000-8000-000000000002";
const token = "organization-agent-internal-token";

function options(overrides: Partial<OrganizationAgentToolsOptions> = {}): OrganizationAgentToolsOptions {
  const pool = { query: async () => ({ rowCount: 1, rows: [{ id: owner }] }) } as unknown as OrganizationAgentToolsOptions["pool"];
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
      payload: { arguments: {}, idempotency_key: vacancy } });
    assert.equal(stats.statusCode, 200, stats.body);
    assert.deepEqual(stats.json(), { result: { value: 1 } });
    const list = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/recruitment_list", headers,
      payload: { arguments: { limit: 2, offset: 1 }, idempotency_key: vacancy } });
    assert.equal(list.statusCode, 200, list.body);
    assert.deepEqual(calls, [`stats:${owner}`, `list:${owner}:2:1`]);
  } finally { await app.close(); }
});

test("feed bridge forwards only after an owned published vacancy check", async () => {
  const paths: string[] = [];
  const configured = options({
    agentsToken: "agents-token", matchingToken: "matching-token",
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
