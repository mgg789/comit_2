/** Real PostgreSQL/session + QGramm façade acceptance; no upstream chat test double. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import swagger from "@fastify/swagger";
import { Pool } from "pg";
import type { AccountRole, ChatFile, ChatMessage, ChatPage, ChatReadStatus, DownloadChatFile, RecruitmentView, RecruitmentPage } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore } from "../src/recruitment/store.js";
import { recruitmentRoutes } from "../src/routes/recruitment.js";
import { chatConfiguration, chatRoutes } from "../src/routes/chat.js";
import { authRoutes } from "../src/routes/auth.js";
import { createApiClient } from "../../../packages/api-client/src/index.js";

test("chat configuration fails closed without valid private credentials", () => {
  assert.equal(chatConfiguration({}), null);
  assert.equal(chatConfiguration({ CHAT_SERVICE_URL: "ftp://chat", CHAT_SERVICE_TOKEN: "x".repeat(32) }), null);
  assert.equal(chatConfiguration({ CHAT_SERVICE_URL: "http://secret@chat", CHAT_SERVICE_TOKEN: "x".repeat(32) }), null);
  assert.equal(chatConfiguration({ CHAT_SERVICE_URL: "http://chat/ignored-prefix", CHAT_SERVICE_TOKEN: "x".repeat(32) }), null);
  assert.equal(chatConfiguration({ CHAT_SERVICE_URL: "http://chat", CHAT_SERVICE_TOKEN: "short" }), null);
  assert.deepEqual(chatConfiguration({ CHAT_SERVICE_URL: "http://chat:8080", CHAT_SERVICE_TOKEN: "x".repeat(32) }), { url: "http://chat:8080", token: "x".repeat(32) });
});

const databaseUrl = process.env.CHAT_TEST_DATABASE_URL;
const chatUrl = process.env.CHAT_TEST_SERVICE_URL;
const chatToken = process.env.CHAT_TEST_SERVICE_TOKEN;
test("invitation chat: two authenticated participants, files, polling, retries, consent and closure", {
  skip: !(databaseUrl && chatUrl && chatToken),
}, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts,auth_challenges,auth_issue_events,auth_verify_events RESTART IDENTITY CASCADE");
    const secret = "synthetic-only-chat-session-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39780", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret), profiles = new ProfileStore(pool), recruitment = new RecruitmentStore(pool, true);
    const login = async (role: AccountRole, index: number) => {
      const email = `chat-${index}@example.test`;
      const issued = await store.issue(role, email, `192.0.2.${index}`);
      const mail = await store.claimMail(); assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const session = await store.verify(role, email, issued.challenge_id, mail.code, "account-v1");
      const token = await signSession(config, role, session.account.id, session.session_id, session.expires_at);
      return { id: session.account.id, headers: { authorization: `Bearer ${token}` }, token };
    };
    const owner = await login("employer", 1), other = await login("employer", 2), candidate = await login("candidate", 3);
    await profiles.save(candidate.id, { expected_revision: 0, fields: { display_name: "Synthetic chat profile" },
      consent: { processing: true, pool: true, version: "profile-v1" } });
    const id = randomUUID(), hidden = randomUUID(), anotherProcess = randomUUID();
    const terms = { company_name: "Synthetic", description: "Invitation", salary: { from_rub: 100000, to_rub: 200000 },
      contact_method: "platform", vacancy_title: "Backend", city: "Москва", daily: "5/2", work_type: "remote" };
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at)
      VALUES($1,$2,$3,$4,$5,1,'sent',$6,now())`, [id, owner.id, candidate.id, randomUUID(), randomUUID(), terms]);
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state)
      VALUES($1,$2,$3,$4,$5,1,'rejected')`, [hidden, owner.id, candidate.id, randomUUID(), randomUUID()]);
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at)
      VALUES($1,$2,$3,$4,$5,1,'sent',$6,now())`, [anotherProcess, owner.id, candidate.id, randomUUID(), randomUUID(), terms]);
    await app.register(cookie);
    await app.register(swagger, { openapi: { info: { title: "Chat acceptance", version: "1" }, components: { securitySchemes: {
      bearerAuth: { type: "http", scheme: "bearer" }, candidateSession: { type: "apiKey", in: "cookie", name: "comit_candidate_session" },
      employerSession: { type: "apiKey", in: "cookie", name: "comit_employer_session" },
    } } } });
    await app.register(authRoutes, { store, config, mailConfigured: false });
    let upstreamMode: "real" | "offline" | "malformed" = "real";
    let pauseHistory: { entered: () => void; release: Promise<void> } | null = null;
    const chatTransport: typeof fetch = async (input, init) => {
      if (upstreamMode === "offline") throw new Error("synthetic-private-upstream-error");
      if (upstreamMode === "malformed") return Response.json({ secret: "synthetic-private-upstream-error" });
      if (pauseHistory && new URL(String(input)).pathname.endsWith("/history")) {
        pauseHistory.entered();
        await pauseHistory.release;
      }
      return fetch(input, init);
    };
    await app.register(recruitmentRoutes, { store, config, profiles, recruitment, mailConfigured: false, chatUrl, chatToken, chatTransport });
    await app.register(chatRoutes, { store, config, profiles, recruitment, mailConfigured: false, chatUrl, chatToken, chatTransport });
    const baseUrl = await app.listen({ host: "127.0.0.1", port: 0 });
    const http = async (input: { url: string; method?: string; headers?: Record<string, string>; payload?: unknown }) => {
      const response = await fetch(`${baseUrl}${input.url}`, { method: input.method ?? "GET",
        headers: { ...input.headers, ...(input.payload === undefined ? {} : { "content-type": "application/json" }) },
        body: input.payload === undefined ? undefined : JSON.stringify(input.payload) });
      const body = await response.text();
      return { statusCode: response.status, headers: Object.fromEntries(response.headers), body,
        json: <T>() => JSON.parse(body) as T };
    };
    const employerPath = `/employer/recruitment/${id}/chat`, candidatePath = `/candidate/recruitment/${id}/chat`;
    assert.equal((await http({ url: `${employerPath}/messages` })).statusCode, 401);
    assert.equal((await http({ url: `${employerPath}/messages`, headers: other.headers })).statusCode, 404);
    assert.equal((await http({ url: `${employerPath}/messages`, headers: candidate.headers })).statusCode, 401);
    for (const [role, person] of [["candidate", candidate], ["employer", owner]] as const)
      assert.equal((await http({ url: `/${role}/recruitment/${hidden}/chat/messages`, headers: person.headers })).statusCode, 404);
    assert.equal((await recruitment.get("candidate", candidate.id, id)).chat_available, true);
    await assert.rejects(recruitment.contacts(owner.id, id), { code: "invitation_acceptance_required" });
    const initial = await http({ url: `${candidatePath}/messages`, headers: candidate.headers });
    assert.equal(initial.statusCode, 200, initial.body);
    assert.deepEqual(initial.json<ChatPage>().messages, []);
    assert.equal(initial.headers["cache-control"], "no-store");
    for (const mode of ["offline", "malformed"] as const) {
      upstreamMode = mode;
      const failure = await http({ url: `${candidatePath}/messages`, headers: candidate.headers });
      assert.equal(failure.statusCode, 503);
      assert.equal(failure.body.includes("synthetic-private-upstream-error"), false);
      assert.equal(failure.body.includes(chatToken!), false);
    }
    upstreamMode = "real";
    let saturatedEntered!: () => void, saturatedRelease!: () => void;
    const saturatedStarted = new Promise<void>(resolve => { saturatedEntered = resolve; });
    pauseHistory = { entered: saturatedEntered, release: new Promise<void>(resolve => { saturatedRelease = resolve; }) };
    const blocked = [http({ url: `${candidatePath}/messages`, headers: candidate.headers })];
    await saturatedStarted;
    blocked.push(http({ url: `${employerPath}/messages`, headers: owner.headers }));
    blocked.push(http({ url: `/candidate/recruitment/${anotherProcess}/chat/messages`, headers: candidate.headers }));
    try {
      await new Promise(resolve => setTimeout(resolve, 100));
      const busy = await http({ url: `${candidatePath}/messages`, headers: candidate.headers });
      assert.equal(busy.statusCode, 429, busy.body);
      assert.equal(busy.headers["retry-after"], "1");
      const account = await http({ url: "/auth/employer/me", headers: owner.headers });
      assert.equal(account.statusCode, 200, "non-chat account reads must retain database capacity");
    } finally { saturatedRelease(); pauseHistory = null; }
    for (const operation of blocked) assert.equal((await operation).statusCode, 200);
    const payload = { idempotency_key: randomUUID(), text: "Обсудим вакансию до принятия приглашения", files: [] };
    for (const invalid of [{ ...payload, actor_id: other.id }, { ...payload, text: " " }, { ...payload, idempotency_key: "bad" }])
      assert.equal((await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers, payload: invalid })).statusCode, 400);
    const sent = await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers, payload });
    assert.equal(sent.statusCode, 200, sent.body);
    const message = sent.json<ChatMessage>();
    assert.equal(message.sender_id, owner.id);
    assert.equal(message.text, payload.text);
    const retry = await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers, payload });
    assert.equal(retry.statusCode, 200, retry.body);
    assert.equal(retry.json<ChatMessage>().id, message.id);
    assert.equal((await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers, payload: { ...payload, text: "changed" } })).statusCode, 409);
    const candidateView = await http({ url: `/candidate/recruitment/${id}`, headers: candidate.headers });
    assert.equal(candidateView.statusCode, 200);
    assert.equal(candidateView.json<RecruitmentView>().chat_unread_count, 1);
    const ownerView = await http({ url: `/employer/recruitment/${id}`, headers: owner.headers });
    assert.equal(ownerView.json<RecruitmentView>().chat_unread_count, 0);
    const candidateList = await http({ url: `/candidate/recruitment`, headers: candidate.headers });
    assert.equal(candidateList.statusCode, 200);
    assert.equal(candidateList.json<RecruitmentPage>().items.find(item => item.id === id)?.chat_unread_count, 1);
    upstreamMode = "offline";
    const degraded = await http({ url: `/candidate/recruitment/${id}`, headers: candidate.headers });
    assert.equal(degraded.statusCode, 200);
    assert.equal(degraded.json<RecruitmentView>().chat_unread_count, null);
    upstreamMode = "real";
    // Cross the QGramm chunk boundary and the usual nginx 1 MiB default with synthetic bytes.
    const bytes = Buffer.concat([Buffer.from("Синтетический файл: encrypted attachment"), Buffer.alloc(1048576 + 17, 0x78)]);
    const upload = { idempotency_key: randomUUID(), name: "cv.txt", media_type: "text/plain", data_base64: bytes.toString("base64") };
    const uploaded = await http({ method: "POST", url: `${employerPath}/files`, headers: owner.headers, payload: upload });
    assert.equal(uploaded.statusCode, 200, uploaded.body);
    const file = uploaded.json<ChatFile>();
    assert.equal(file.size, bytes.length);
    const uploadAgain = await http({ method: "POST", url: `${employerPath}/files`, headers: owner.headers, payload: upload });
    assert.equal(uploadAgain.statusCode, 200, uploadAgain.body);
    assert.equal(uploadAgain.json<ChatFile>().id, file.id);
    assert.equal((await http({ url: `${candidatePath}/files/${file.id}`, headers: candidate.headers })).statusCode, 404, "unpublished uploads are private");
    const attached = await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers,
      payload: { idempotency_key: randomUUID(), text: "", files: [file] } });
    assert.equal(attached.statusCode, 200, attached.body);
    const download = await http({ url: `${candidatePath}/files/${file.id}`, headers: candidate.headers });
    assert.equal(download.statusCode, 200, download.body);
    assert.deepEqual(Buffer.from(download.json<DownloadChatFile>().data_base64, "base64"), bytes);
    assert.equal(download.json<DownloadChatFile>().name, "cv.txt");
    // Exercise the actual QGramm event journal through public authenticated SSE.
    const streaming = createApiClient({ baseUrl, getAuthToken: () => candidate.token });
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 5000);
    let fileEvent = false, lastSequence = 0;
    try {
      await streaming.streamChatEvents("candidate", id, event => {
        assert.ok(event.sequence > lastSequence);
        lastSequence = event.sequence;
        if (event.type === "message.created" && event.message.files.some(item => item.id === file.id)) {
          fileEvent = true;
          controller.abort();
        }
      }, { signal: controller.signal });
    } catch (error) {
      if (!controller.signal.aborted) throw error;
    } finally { clearTimeout(deadline); controller.abort(); }
    assert.ok(fileEvent, "encrypted attachment message must arrive through the durable SSE journal");
    assert.equal((await http({ url: `/candidate/recruitment/${anotherProcess}/chat/files/${file.id}`, headers: candidate.headers })).statusCode, 404, "same participants cannot read a different invitation's file");
    assert.equal((await http({ method: "POST", url: `/employer/recruitment/${anotherProcess}/chat/messages`, headers: owner.headers,
      payload: { idempotency_key: randomUUID(), text: "wrong process", files: [file] } })).statusCode, 404);
    const page = await http({ url: `${candidatePath}/messages?after_sequence=${message.sequence}&limit=1`, headers: candidate.headers });
    assert.equal(page.statusCode, 200, page.body);
    assert.equal(page.json<ChatPage>().messages.length, 1);
    assert.deepEqual(page.json<ChatPage>().messages[0]?.files, [file]);
    const reply = await http({ method: "POST", url: `${candidatePath}/messages`, headers: candidate.headers,
      payload: { idempotency_key: randomUUID(), text: "Уточню график", files: [] } });
    assert.equal(reply.statusCode, 200, reply.body);
    assert.equal(reply.json<ChatMessage>().sender_id, candidate.id);
    assert.equal((await http({ url: `${employerPath}/files/${file.id}`, headers: other.headers })).statusCode, 404);
    assert.equal((await http({ method: "POST", url: `${employerPath}/read`, headers: other.headers, payload: { through_sequence: message.sequence } })).statusCode, 404);
    assert.equal((await http({ method: "POST", url: `${candidatePath}/read`, headers: candidate.headers, payload: { through_sequence: -1 } })).statusCode, 400);
    assert.equal((await http({ method: "POST", url: `${candidatePath}/read`, headers: candidate.headers, payload: { through_sequence: message.sequence, actor_id: owner.id } })).statusCode, 400);
    assert.equal((await http({ method: "POST", url: `${candidatePath}/read`, headers: candidate.headers, payload: { through_sequence: Number.MAX_SAFE_INTEGER } })).statusCode, 400);
    const marked = await streaming.acknowledgeChatRead("candidate", id, { through_sequence: attached.json<ChatMessage>().sequence });
    assert.equal(marked.unread_count, 0, "own outgoing messages must not count as unread");
    const older = await streaming.acknowledgeChatRead("candidate", id, { through_sequence: message.sequence });
    assert.equal(older.read_sequence, marked.read_sequence, "older retries cannot undo a read receipt");
    assert.equal(older.unread_count, 0);
    const ownerRead = await http({ method: "POST", url: `${employerPath}/read`, headers: owner.headers, payload: { through_sequence: message.sequence } });
    assert.equal(ownerRead.statusCode, 200);
    assert.equal(ownerRead.json<ChatReadStatus>().unread_count, 1, "candidate reply remains unread for employer");
    const cookieHeaders = { cookie: `comit_candidate_session=${candidate.token}`, origin: "http://evil.example" };
    assert.equal((await http({ method: "POST", url: `${candidatePath}/messages`, headers: cookieHeaders, payload: { ...payload, idempotency_key: randomUUID() } })).statusCode, 403);
    // Pool withdrawal does not break an existing invitation; processing withdrawal blocks both reads and writes.
    await profiles.save(candidate.id, { expected_revision: 1, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } });
    assert.equal((await http({ url: `${candidatePath}/messages`, headers: candidate.headers })).statusCode, 200);
    let entered!: () => void, release!: () => void;
    const enteredPromise = new Promise<void>(resolve => { entered = resolve; });
    pauseHistory = { entered, release: new Promise<void>(resolve => { release = resolve; }) };
    const inFlight = http({ url: `${candidatePath}/messages`, headers: candidate.headers });
    await enteredPromise;
    let revoked = false;
    const revoke = profiles.save(candidate.id, { expected_revision: 2, fields: {},
      consent: { processing: false, pool: false, version: "profile-v1" } }).then(() => { revoked = true; });
    try {
      await new Promise(resolve => setTimeout(resolve, 50));
      assert.equal(revoked, false, "processing withdrawal must serialize behind the in-flight grant");
    } finally { release(); pauseHistory = null; }
    assert.equal((await inFlight).statusCode, 200);
    await revoke;
    assert.equal((await http({ method: "POST", url: `${candidatePath}/read`, headers: candidate.headers, payload: { through_sequence: message.sequence } })).statusCode, 409);
    for (const path of [`${candidatePath}/messages`, `${employerPath}/files/${file.id}`])
      assert.equal((await http({ url: path, headers: path.startsWith("/candidate") ? candidate.headers : owner.headers })).statusCode, 409);
    assert.equal((await recruitment.get("candidate", candidate.id, id)).chat_available, false);
    await profiles.save(candidate.id, { expected_revision: 3, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } });
    await recruitment.act("candidate", candidate.id, id, { action: "decline", expected_revision: 1, idempotency_key: randomUUID() });
    assert.equal((await http({ url: `${candidatePath}/messages`, headers: candidate.headers })).statusCode, 200);
    assert.equal((await http({ method: "POST", url: `${candidatePath}/read`, headers: candidate.headers, payload: { through_sequence: message.sequence } })).statusCode, 200, "readonly history permits read acknowledgement");
    assert.equal((await http({ method: "POST", url: `${employerPath}/messages`, headers: owner.headers, payload: { ...payload, idempotency_key: randomUUID() } })).statusCode, 409);
    assert.equal((await http({ method: "POST", url: `${employerPath}/files`, headers: owner.headers, payload: { ...upload, idempotency_key: randomUUID() } })).statusCode, 409);
    const paths = app.swagger().paths ?? {};
    assert.ok(paths["/candidate/recruitment/{id}/chat/messages"]?.get);
    assert.ok(paths["/employer/recruitment/{id}/chat/files/{file_id}"]?.get);
    await store.revokeAll("candidate", candidate.id);
    assert.equal((await http({ url: `${candidatePath}/messages`, headers: candidate.headers })).statusCode, 401);
    // No service credentials, raw QGramm JWTs or platform-managed contacts in the public payload.
    for (const output of [initial.body, sent.body, uploaded.body, download.body, page.body]) {
      assert.equal(output.includes(chatToken!), false);
      assert.equal(output.includes("@example.test"), false);
      assert.equal(output.includes("envelope"), false);
    }
  } finally { await app.close(); await pool.end(); }
});
