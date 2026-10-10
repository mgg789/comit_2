/** SSE streams exercise durable cursors and repeated real session/consent authorization. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { streamCursor } from "../src/chat/sse.js";
import { AuthStore } from "../src/auth/store.js";
import { signSession } from "../src/auth/session.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore } from "../src/recruitment/store.js";
import { chatRoutes } from "../src/routes/chat.js";

test("SSE cursor rejects ambiguity and unsafe integers", () => {
  assert.equal(streamCursor(undefined, undefined), 0);
  assert.equal(streamCursor("15", "15"), 15);
  assert.equal(streamCursor("9007199254740991", undefined), Number.MAX_SAFE_INTEGER);
  for (const [header, query] of [["1", "2"], ["9007199254740992", undefined], ["-1", undefined], ["1\ndata: private", undefined], [["1"], undefined]] as const) {
    assert.throws(() => streamCursor(header, query));
  }
});

const databaseUrl = process.env.CHAT_SSE_TEST_DATABASE_URL;
test("chat SSE bounds streams, resumes durable events and stops after consent/session revocation", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const revocationApp = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts,auth_challenges,auth_issue_events,auth_verify_events RESTART IDENTITY CASCADE");
    const secret = "synthetic-only-sse-session-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39780", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret), profiles = new ProfileStore(pool), recruitment = new RecruitmentStore(pool, true);
    const login = async (role: "candidate" | "employer", email: string) => {
      const issued = await store.issue(role, email, "192.0.2.80");
      const mail = await store.claimMail(); assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const session = await store.verify(role, email, issued.challenge_id, mail.code, "account-v1");
      return { id: session.account.id, authorization: `Bearer ${await signSession(config, role, session.account.id, session.session_id, session.expires_at)}` };
    };
    const owner = await login("employer", "sse-owner@example.test"), candidate = await login("candidate", "sse-candidate@example.test"), foreign = await login("employer", "sse-foreign@example.test");
    await profiles.save(candidate.id, { expected_revision: 0, fields: { display_name: "Synthetic SSE" }, consent: { processing: true, pool: true, version: "profile-v1" } });
    const processId = randomUUID();
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at)
      VALUES($1,$2,$3,$4,$5,1,'sent',$6,now())`, [processId, owner.id, candidate.id, randomUUID(), randomUUID(), {
      company_name: "Synthetic", description: "Invitation", salary: { from_rub: 100, to_rub: 200 }, contact_method: "platform",
      vacancy_title: "Backend", city: "Москва", daily: "5/2", work_type: "remote" }]);
    const message = { id: randomUUID(), sequence: 5, sender_id: owner.id, created_at: "2026-10-07T00:00:00Z", text: "Synthetic live message", files: [] };
    const calls: number[] = [];
    let failure = 0;
    const transport: typeof fetch = async (input, init) => {
      assert.equal(new URL(String(input)).pathname, "/internal/chat/events");
      const payload = JSON.parse(String(init?.body)) as { after_sequence: number; actor_id: string };
      assert.equal(payload.actor_id, candidate.id);
      calls.push(payload.after_sequence);
      if (failure) return Response.json({ detail: "synthetic-private-error" }, { status: failure });
      return Response.json(payload.after_sequence < 5 ? { events: [{ sequence: 5, type: "message.created", message }], next_sequence: 5 } : { events: [], next_sequence: payload.after_sequence });
    };
    await app.register(cookie);
    await app.register(chatRoutes, { store, config, profiles, recruitment, mailConfigured: false, chatUrl: "http://synthetic-chat", chatToken: "synthetic-private-token", chatTransport: transport, streamLifetimeMs: 80, streamPollMs: 10 });
    const path = `/candidate/recruitment/${processId}/chat/events`;
    const headers = { authorization: candidate.authorization };
    await pool.query("INSERT INTO recruitment_events(event_id,process_id,revision,type,state) VALUES($1,$2,1,'invited','sent')", [randomUUID(), processId]);
    const notifications = await app.inject({ url: "/candidate/recruitment-events/stream", headers });
    assert.equal(notifications.statusCode, 200, notifications.body);
    assert.match(notifications.body, /event: recruitment.changed/);
    assert.ok(!notifications.body.includes(message.text));
    const unrelated = await app.inject({ url: "/employer/recruitment-events/stream", headers: { authorization: foreign.authorization } });
    assert.ok(!unrelated.body.includes(processId));
    assert.equal((await app.inject({ url: path })).statusCode, 401);
    assert.equal((await app.inject({ url: `/employer/recruitment/${processId}/chat/events`, headers: { authorization: foreign.authorization } })).statusCode, 404);
    assert.equal((await app.inject({ url: `${path}?after_sequence=2`, headers: { ...headers, "last-event-id": "3" } })).statusCode, 400);
    const stream = await app.inject({ url: path, headers });
    assert.equal(stream.statusCode, 200, stream.body); assert.match(String(stream.headers["content-type"]), /text\/event-stream/);
    assert.match(stream.body, /id: 5\nevent: message.created/); assert.match(stream.body, /: heartbeat/); assert.match(stream.body, /event: reconnect/);
    assert.equal(stream.body.split("Synthetic live message").length - 1, 1);
    const resumed = await app.inject({ url: path, headers: { ...headers, "last-event-id": "5" } });
    assert.ok(!resumed.body.includes("message.created"));
    assert.ok(calls.includes(5));
    failure = 410;
    const resync = await app.inject({ url: path, headers });
    assert.equal(resync.statusCode, 410); assert.equal(resync.json().detail, "chat_resync_required");
    failure = 0;
    const first = app.inject({ url: path, headers }), second = app.inject({ url: path, headers });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal((await app.inject({ url: path, headers })).statusCode, 429);
    await Promise.all([first, second]);
    // Real response headers confirm the stream is open before changing durable
    // authorization. The separate bounded lifetime must not race a PG commit.
    await revocationApp.register(cookie);
    await revocationApp.register(chatRoutes, { store, config, profiles, recruitment, mailConfigured: false,
      chatUrl: "http://synthetic-chat", chatToken: "synthetic-private-token", chatTransport: transport,
      streamLifetimeMs: 5000, streamPollMs: 10 });
    const address = await revocationApp.listen({ host: "127.0.0.1", port: 0 });
    const openStream = async () => {
      const response = await fetch(`${address}${path}`, { headers, signal: AbortSignal.timeout(10_000) });
      assert.equal(response.status, 200, response.status === 200 ? undefined : await response.text());
      const reader = response.body?.getReader();
      assert.ok(reader);
      const firstFrame = await reader.read();
      assert.equal(firstFrame.done, false);
      const prefix = new TextDecoder().decode(firstFrame.value);
      assert.match(prefix, /retry: 1000/);
      return { reader, prefix };
    };
    const finishStream = async (stream: Awaited<ReturnType<typeof openStream>>) => {
      const decoder = new TextDecoder();
      let body = stream.prefix;
      while (true) {
        const frame = await stream.reader.read();
        if (frame.done) return body + decoder.decode();
        body += decoder.decode(frame.value, { stream: true });
      }
    };
    const duringWithdrawal = await openStream();
    // Exercise a mutation after the old 80ms fixture lifetime, rather than
    // relying on a scheduling sleep to assume that Fastify started the request.
    await new Promise(resolve => setTimeout(resolve, 120));
    await profiles.save(candidate.id, { expected_revision: 1, fields: {}, consent: { processing: false, pool: false, version: "profile-v1" } });
    const closed = await finishStream(duringWithdrawal);
    assert.match(closed, /event: error/); assert.ok(!closed.includes("synthetic-private-error"));
    assert.equal((await app.inject({ url: path, headers })).statusCode, 409);
    await profiles.save(candidate.id, { expected_revision: 2, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } });
    const duringLogout = await openStream();
    await store.revokeAll("candidate", candidate.id);
    assert.match(await finishStream(duringLogout), /chat_session_revoked/);
    assert.equal((await app.inject({ url: path, headers })).statusCode, 401);
  } finally { await revocationApp.close(); await app.close(); await pool.end(); }
});
