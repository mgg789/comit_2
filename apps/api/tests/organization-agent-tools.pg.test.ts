import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { candidateValidationOptions } from "../src/routes/candidate-pipeline.js";
import { organizationAgentToolsRoutes, type OrganizationAgentToolsOptions } from "../src/routes/organization-agent-tools.js";

const databaseUrl = process.env.ORGANIZATION_AGENT_TEST_DATABASE_URL;
const internalToken = "organization-agent-pg-test-token";

test("organization agent bridge uses real PostgreSQL receipts and delegation lease state", { skip: !databaseUrl }, async () => {
  assert.ok(databaseUrl);
  const pool = new Pool({ connectionString: databaseUrl });
  await migrate(pool);
  const ownerId = randomUUID();
  const processId = randomUUID();
  const taskId = randomUUID();
  const leaseToken = randomUUID();
  const delegationId = randomUUID();
  const idempotencyKey = randomUUID();
  const email = `${ownerId}@organization-agent-pg.test`;
  let transportCalls = 0;
  let statisticsCalls = 0;
  const configured: OrganizationAgentToolsOptions = {
    token: internalToken,
    chatToken: "chat-test-token",
    chatUrl: "http://chat.test",
    pool,
    statistics: { get: async () => { statisticsCalls += 1; return { snapshot: statisticsCalls }; } } as unknown as OrganizationAgentToolsOptions["statistics"],
    recruitment: {
      withChatGrant: async (_role, _owner, process, _write, operation) => operation({ process_id: process, owner_id: ownerId, candidate_id: randomUUID(), actor_id: ownerId }),
    } as unknown as OrganizationAgentToolsOptions["recruitment"],
    transport: async () => {
      transportCalls += 1;
      return Response.json({ id: randomUUID(), sequence: transportCalls, sender_id: ownerId, created_at: new Date().toISOString(), text: "delivered", files: [] });
    },
  };
  const app = Fastify({ ajv: { customOptions: candidateValidationOptions } });
  try {
    await pool.query("INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at) VALUES($1,$2,'employer',true,'v1',now())", [ownerId, email]);
    await pool.query("INSERT INTO agents_service.organization_chats(owner_id,chat_id,status,presence,last_seen_at) VALUES($1,$2,'active',false,now())", [ownerId, randomUUID()]);
    const chat = await pool.query<{ chat_id: string }>("SELECT chat_id FROM agents_service.organization_chats WHERE owner_id=$1", [ownerId]);
    await pool.query("INSERT INTO agents_service.organization_tasks(task_id,chat_id,owner_id,kind,instruction,conversation_refs,status,lease_token,lease_until,idempotency_key) VALUES($1,$2,$3,'stand_in','send a delegated message',$4::jsonb,'processing',$5,now()+interval '5 minutes',$6)", [
      taskId, chat.rows[0].chat_id, ownerId,
      JSON.stringify({ process_ids: [processId], delegation_id: delegationId, expires_at: new Date(Date.now() + 300_000).toISOString(), allowed_operations: ["candidate_chat_send"] }),
      leaseToken, randomUUID(),
    ]);
    await app.register(organizationAgentToolsRoutes, configured);
    const headers = { "x-internal-token": internalToken, "x-owner-id": ownerId };
    const payload = {
      arguments: { process_id: processId, delegation_id: delegationId, task_id: taskId, lease_token: leaseToken, idempotency_key: idempotencyKey, text: "hello" },
      idempotency_key: idempotencyKey,
    };
    const first = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload });
    assert.equal(first.statusCode, 200, first.body);
    const replay = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload });
    assert.equal(replay.statusCode, 200, replay.body);
    assert.equal(transportCalls, 1);
    const receipt = await pool.query<{ status: string; request_hash: string }>("SELECT status,request_hash FROM organization_agent_tool_receipts WHERE owner_id=$1 AND idempotency_key=$2", [ownerId, idempotencyKey]);
    assert.equal(receipt.rows[0].status, "completed");
    assert.match(receipt.rows[0].request_hash, /^[a-f0-9]{64}$/);

    const statsKey = randomUUID();
    const statsPayload = { arguments: {}, idempotency_key: statsKey };
    const statsFirst = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/organization_stats", headers, payload: statsPayload });
    const statsReplay = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/organization_stats", headers, payload: statsPayload });
    assert.equal(statsFirst.statusCode, 200, statsFirst.body);
    assert.equal(statsReplay.statusCode, 200, statsReplay.body);
    assert.deepEqual(statsReplay.json(), statsFirst.json());
    assert.equal(statisticsCalls, 1);

    await pool.query("UPDATE agents_service.organization_chats SET presence=true,last_seen_at=now() WHERE owner_id=$1", [ownerId]);
    const onlineKey = randomUUID();
    const online = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload: { ...payload, idempotency_key: onlineKey, arguments: { ...payload.arguments, idempotency_key: onlineKey } } });
    assert.equal(online.statusCode, 403);
    await pool.query("UPDATE agents_service.organization_chats SET presence=false WHERE owner_id=$1", [ownerId]);
    await pool.query("UPDATE agents_service.organization_tasks SET lease_until=now()-interval '1 second' WHERE task_id=$1", [taskId]);
    const expiredKey = randomUUID();
    const expired = await app.inject({ method: "POST", url: "/internal/organization-agent/tools/candidate_chat_send", headers, payload: { ...payload, idempotency_key: expiredKey, arguments: { ...payload.arguments, idempotency_key: expiredKey } } });
    assert.equal(expired.statusCode, 403);
  } finally {
    await app.close();
    await pool.query("DELETE FROM organization_agent_tool_receipts WHERE owner_id=$1", [ownerId]);
    await pool.query("DELETE FROM agents_service.organization_tasks WHERE task_id=$1", [taskId]);
    await pool.query("DELETE FROM agents_service.organization_chats WHERE owner_id=$1", [ownerId]);
    await pool.query("DELETE FROM accounts WHERE id=$1", [ownerId]);
    await pool.end();
  }
});
