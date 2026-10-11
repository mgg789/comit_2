/** Isolated durable inbox/company acceptance plus bounded metadata transport regression. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import type { AccountRole, SystemChatFile, SystemChatFileDownload, SystemChatPage, SystemNotification, ChatUnreadStatus } from "@comit/shared";
import { SystemNotificationsStore } from "../src/notifications/system-store.js";
import { fetchGlobalRecruitmentUnread } from "../src/notifications/unread.js";
import { EmployerProfileStore } from "../src/employers/store.js";
import { employerProfileRoutes } from "../src/routes/employer-profile.js";
import { notificationRoutes } from "../src/routes/notifications.js";
import { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { hashSessionId } from "../src/auth/crypto.js";

const configuration = { url: "http://private.test", token: "synthetic-metadata-token" };
test("global unread counts all 205 chats and fails closed on forged, duplicate or unavailable counters", async () => {
  const grants = Array.from({ length: 205 }, (_, i) => ({ process_id: String(i), owner_id: "owner", candidate_id: "candidate", actor_id: "owner" }));
  const sizes: number[] = [];
  const transport: typeof fetch = async (_input, init) => {
    const payload = JSON.parse(String(init?.body)) as { chats: typeof grants };
    sizes.push(payload.chats.length);
    return Response.json({ items: payload.chats.map(chat => ({ process_id: chat.process_id, read_sequence: 0, unread_count: 2 })) });
  };
  assert.equal(await fetchGlobalRecruitmentUnread(grants, configuration, transport), 410);
  assert.deepEqual(sizes, [100, 100, 5]);
  assert.equal(await fetchGlobalRecruitmentUnread([], null, transport), 0);
  assert.equal(await fetchGlobalRecruitmentUnread(grants, null, transport), null);
  for (const invalid of [
    { items: [{ process_id: "other", read_sequence: 0, unread_count: 3 }] },
    { items: [{ process_id: "0", read_sequence: 0, unread_count: -1 }] },
    { items: [{ process_id: "0", read_sequence: 0, unread_count: 1.1 }] },
    { items: [{ process_id: "0", read_sequence: 0, unread_count: 1 }, { process_id: "0", read_sequence: 0, unread_count: 1 }] },
  ]) assert.equal(await fetchGlobalRecruitmentUnread(grants.slice(0, 1), configuration, async () => Response.json(invalid)), null);
  assert.equal(await fetchGlobalRecruitmentUnread(grants, configuration, async () => { throw new Error("offline"); }), null);
});

const databaseUrl = process.env.SYSTEM_INBOX_TEST_DATABASE_URL;
test("company CAS, invitation backfill and durable owner-only inbox/read receipts", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    // This env must identify a disposable database: never reuse a demo or production URL.
    assert.match(new URL(databaseUrl!).pathname, /^\/(?:comit_system_inbox_test|hiring_test_[0-9]+_[a-f0-9]+)$/);
    await pool.query("DROP SCHEMA IF EXISTS feed_pipeline CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public");
    for (const name of ["001_accounts.sql", "002_candidate_profiles.sql", "003_recruitment.sql"])
      await pool.query(await readFile(new URL(`../migrations/${name}`, import.meta.url), "utf8"));
    const secret = "synthetic-system-inbox-auth-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_COOKIE_SECURE: "false", AUTH_ALLOWED_ORIGINS: "http://localhost:39580" });
    const auth = new AuthStore(pool, secret);
    const account = async (role: AccountRole) => {
      const id = randomUUID(), sid = randomBytes(32).toString("base64url"), expiry = new Date(Date.now() + 3600000).toISOString();
      await pool.query("INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at) VALUES($1,$2,$3,true,'synthetic',now())", [id, `${id}@example.test`, role]);
      await pool.query("INSERT INTO sessions(id,account_id,role,session_hash,expires_at) VALUES($1,$2,$3,$4,$5)", [randomUUID(), id, role, hashSessionId(sid), expiry]);
      return { id, headers: { authorization: `Bearer ${await signSession(config, role, id, sid, expiry)}` } };
    };
    const owner = await account("employer"), foreign = await account("employer"), candidate = await account("candidate");
    const oldProcess = randomUUID(), latestProcess = randomUUID();
    for (const [id, name, ago] of [[oldProcess, "Old company", "2 days"], [latestProcess, " Current company ", "1 day"]])
      await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,terms,invited_at)
        VALUES($1,$2,$3,$4,$5,1,'sent',$6,now()-$7::interval)`, [id, owner.id, candidate.id, randomUUID(), randomUUID(), { company_name: name }, ago]);
    await pool.query(await readFile(new URL("../migrations/014_employer_system_inbox.sql", import.meta.url), "utf8"));
    await pool.query(await readFile(new URL("../migrations/023_system_chat_attachments.sql", import.meta.url), "utf8"));
    const company = new EmployerProfileStore(pool), notifications = new SystemNotificationsStore(pool);
    assert.equal((await company.get(owner.id)).company_name, "Current company");
    assert.deepEqual(await company.get(foreign.id), { company_name: null, revision: 0, updated_at: null });
    await assert.rejects(company.update(candidate.id, { expected_revision: 0, company_name: "Wrong role" }), { code: "employer_not_found" });
    await assert.rejects(company.update(foreign.id, { expected_revision: 0, company_name: "  " }), { code: "invalid_employer_profile" });
    const results = await Promise.allSettled([company.update(foreign.id, { expected_revision: 0, company_name: " First " }), company.update(foreign.id, { expected_revision: 0, company_name: "Second" })]);
    assert.equal(results.filter(result => result.status === "fulfilled").length, 1);
    assert.equal((await company.get(foreign.id)).revision, 1);
    const event = await notifications.publish(owner.id, "synthetic-event", "recruitment.changed", "Статус изменился.");
    const duplicates = await Promise.all(Array.from({ length: 5 }, () => notifications.publish(owner.id, "synthetic-event", "recruitment.changed", "Повтор")));
    assert.equal(duplicates.every(item => item.id === event.id && item.sequence === event.sequence), true);
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await notifications.append(client, owner.id, "rolled-back", "recruitment.changed", "Must roll back");
      await client.query("ROLLBACK");
    } finally { client.release(); }
    assert.equal((await pool.query("SELECT id FROM system_notifications WHERE idempotency_key='rolled-back'")).rowCount, 0);
    await assert.rejects(notifications.read("employer", owner.id, event.sequence), { code: "system_read_not_delivered" });
    const page = await notifications.page("employer", owner.id, 0, 1);
    assert.equal(page.items.length, 1); assert.equal(page.has_more, true); assert.equal(page.unread_count, 2);
    const read = await notifications.read("employer", owner.id, page.next_sequence);
    assert.equal(read.unread_count, 1);
    assert.deepEqual(await notifications.read("employer", owner.id, 0), read);
    await assert.rejects(notifications.read("employer", owner.id, page.next_sequence + 1), { code: "system_read_not_delivered" });
    const restarted = new SystemNotificationsStore(pool);
    assert.equal((await restarted.page("employer", owner.id)).items[0]?.id, event.id);
    const ownOther = await restarted.page("employer", foreign.id);
    assert.equal(ownOther.items.length, 1); assert.equal(ownOther.items[0]?.type, "system.welcome");
    assert.equal((await restarted.page("employer", foreign.id)).items.length, 1);
    await pool.query(`INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,consent_version,consent_accepted_at)
      VALUES($1,1,true,true,'synthetic',now())`, [candidate.id]);
    await app.register(cookie);
    await app.register(employerProfileRoutes, { store: auth, config, mailConfigured: false, employerProfiles: company });
    let calls = 0;
    const transport: typeof fetch = async (_input, init) => {
      calls++;
      const payload = JSON.parse(String(init?.body)) as { chats: Array<{ process_id: string; owner_id: string; actor_id: string }> };
      assert.equal(payload.chats.every(chat => chat.owner_id === owner.id && chat.actor_id === owner.id), true);
      return Response.json({ items: payload.chats.map(chat => ({ process_id: chat.process_id, read_sequence: 0, unread_count: 3 })) });
    };
    await app.register(notificationRoutes, { store: auth, config, mailConfigured: false, notifications, chatUrl: configuration.url, chatToken: configuration.token, chatTransport: transport });
    for (const path of ["/employer/profile", "/employer/system-chat", "/employer/chat-unread"]) {
      assert.equal((await app.inject({ url: path })).statusCode, 401);
      assert.equal((await app.inject({ url: path, headers: candidate.headers })).statusCode, 401);
    }
    const ownPage = await app.inject({ url: "/employer/system-chat", headers: foreign.headers });
    assert.equal(ownPage.statusCode, 200); assert.equal(ownPage.json<SystemChatPage>().items.length, 1);
    assert.equal(ownPage.headers["cache-control"], "no-store");
    const sentMessage = randomUUID();
    const sent = await app.inject({ method: "POST", url: "/employer/system-chat/messages", headers: owner.headers,
      payload: { idempotency_key: sentMessage, text: "Опишите проблему." } });
    assert.equal(sent.statusCode, 200);
    assert.equal(sent.json<SystemNotification>().type, "support.user_message");
    const fileBytes = Buffer.from("owner system attachment");
    const fileUploadKey = randomUUID();
    const fileUpload = await app.inject({ method: "POST", url: "/employer/system-chat/files", headers: owner.headers,
      payload: { idempotency_key: fileUploadKey, name: "owner.txt", media_type: "text/plain", data_base64: fileBytes.toString("base64") } });
    assert.equal(fileUpload.statusCode, 200);
    const file = fileUpload.json<SystemChatFile>();
    const fileMessage = await app.inject({ method: "POST", url: "/employer/system-chat/messages", headers: owner.headers,
      payload: { idempotency_key: randomUUID(), text: "", files: [file] } });
    assert.equal(fileMessage.statusCode, 200);
    assert.deepEqual(fileMessage.json<SystemNotification>().files, [file]);
    const fileDownload = await app.inject({ url: `/employer/system-chat/files/${file.id}`, headers: owner.headers });
    assert.equal(fileDownload.statusCode, 200);
    assert.equal(fileDownload.headers["content-disposition"], 'attachment; filename="support-attachment.json"');
    assert.equal(fileDownload.headers["x-content-type-options"], "nosniff");
    assert.equal(fileDownload.headers["cache-control"], "private, no-store");
    assert.deepEqual(Buffer.from(fileDownload.json<SystemChatFileDownload>().data_base64, "base64"), fileBytes);
    assert.equal((await app.inject({ url: `/employer/system-chat/files/${file.id}`, headers: foreign.headers })).statusCode, 404);
    assert.equal((await app.inject({ method: "POST", url: "/employer/system-chat/files", headers: owner.headers,
      payload: { idempotency_key: fileUploadKey, name: "invalid.txt", media_type: "text/plain", data_base64: fileBytes.toString("base64") } })).statusCode, 409);
    // Own message acknowledges delivery: unread counts only what Comit adds next.
    assert.equal((await app.inject({ url: "/employer/system-chat", headers: owner.headers })).json<SystemChatPage>().unread_count, 0);
    const replay = await app.inject({ method: "POST", url: "/employer/system-chat/messages", headers: owner.headers,
      payload: { idempotency_key: sentMessage, text: "Опишите проблему." } });
    assert.equal(replay.statusCode, 200); assert.equal(replay.json<SystemNotification>().id, sent.json<SystemNotification>().id);
    assert.equal((await app.inject({ method: "POST", url: "/employer/system-chat/messages", headers: owner.headers,
      payload: { idempotency_key: randomUUID(), text: "   " } })).statusCode, 422);
    assert.equal((await app.inject({ method: "POST", url: "/employer/system-chat/read", headers: owner.headers, payload: { read_sequence: 10000 } })).statusCode, 409);

    // Quota fixtures keep content tiny while exercising the persisted byte counters at their real limits.
    // The upload path always writes matching content/size; only this isolated test setup uses synthetic metadata
    // so the concurrency and linked-byte regressions do not allocate 320 MiB per test run.
    const quotaAccount = await account("employer"), pendingAccount = await account("employer"), linkAccount = await account("candidate");
    const seedFile = async (accountId: string, size: number, notificationId?: string): Promise<SystemChatFile> => {
      const file = { id: randomUUID(), name: `seed-${randomUUID()}.txt`, media_type: "text/plain", size,
        sha256: "b".repeat(64) } satisfies SystemChatFile;
      await pool.query(`INSERT INTO system_chat_files(id,account_id,idempotency_key,request_hash,name,media_type,size_bytes,sha256,content)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`, [file.id, accountId, randomUUID(), "a".repeat(64), file.name, file.media_type, size, file.sha256, Buffer.from("seed")]);
      if (notificationId) await pool.query("INSERT INTO system_notification_files(notification_id,file_id,ordinal) VALUES($1,$2,0)", [notificationId, file.id]);
      return file;
    };
    const retryPayload = { idempotency_key: randomUUID(), name: "quota-retry.txt", media_type: "text/plain", data_base64: Buffer.from("r").toString("base64") };
    const retryFile = await notifications.uploadFile(quotaAccount.id, retryPayload);
    const retryMessage = await notifications.publish(quotaAccount.id, `quota-retry-message:${randomUUID()}`, "test.quota_seed", "seed");
    await pool.query("INSERT INTO system_notification_files(notification_id,file_id,ordinal) VALUES($1,$2,0)", [retryMessage.id, retryFile.id]);
    for (let index = 0; index < 31; index++) {
      const message = await notifications.publish(quotaAccount.id, `quota-linked:${index}:${randomUUID()}`, "test.quota_seed", "seed");
      await seedFile(quotaAccount.id, 8 * 1024 * 1024, message.id);
    }
    const lastMessage = await notifications.publish(quotaAccount.id, `quota-linked:last:${randomUUID()}`, "test.quota_seed", "seed");
    await seedFile(quotaAccount.id, 8 * 1024 * 1024 - 1, lastMessage.id);
    // Existing idempotent uploads are resolved before quota checks, even after linked+pending reaches 256 MiB.
    assert.equal((await notifications.uploadFile(quotaAccount.id, retryPayload)).id, retryFile.id);
    await assert.rejects(notifications.uploadFile(quotaAccount.id, {
      idempotency_key: randomUUID(), name: "quota-over.txt", media_type: "text/plain", data_base64: Buffer.from("x").toString("base64"),
    }), error => error instanceof Error && error.message === "system_file_account_limit");
    const linkedTotal = await pool.query<{ total: string }>("SELECT COALESCE(sum(size_bytes),0)::text AS total FROM system_chat_files WHERE account_id=$1", [quotaAccount.id]);
    assert.equal(linkedTotal.rows[0]!.total, String(256 * 1024 * 1024));

    // The same inbox lock serializes distinct uploads: at a 64 MiB pending boundary exactly one 1-byte upload fits.
    for (let index = 0; index < 7; index++) await seedFile(pendingAccount.id, 8 * 1024 * 1024);
    await seedFile(pendingAccount.id, 8 * 1024 * 1024 - 1);
    const concurrentUploads = await Promise.allSettled([
      notifications.uploadFile(pendingAccount.id, { idempotency_key: randomUUID(), name: "concurrent-a.txt", media_type: "text/plain", data_base64: "YQ==" }),
      notifications.uploadFile(pendingAccount.id, { idempotency_key: randomUUID(), name: "concurrent-b.txt", media_type: "text/plain", data_base64: "Yg==" }),
    ]);
    assert.equal(concurrentUploads.filter(result => result.status === "fulfilled").length, 1);
    assert.equal(concurrentUploads.filter(result => result.status === "rejected" && result.reason?.message === "system_file_storage_limit").length, 1);
    const pendingTotal = await pool.query<{ total: string }>(`SELECT COALESCE(sum(size_bytes),0)::text AS total FROM system_chat_files f
      WHERE f.account_id=$1 AND NOT EXISTS(SELECT 1 FROM system_notification_files nf WHERE nf.file_id=f.id)`, [pendingAccount.id]);
    assert.equal(pendingTotal.rows[0]!.total, String(64 * 1024 * 1024));

    // Linking is also serialized by that lock: one concurrent message may claim an orphan, the other gets a conflict.
    const linkFile = await notifications.uploadFile(linkAccount.id, { idempotency_key: randomUUID(), name: "link.txt", media_type: "text/plain", data_base64: "bGluaw==" });
    const concurrentLinks = await Promise.allSettled([
      notifications.sendMessage("candidate", linkAccount.id, `link-a:${randomUUID()}`, "", [linkFile]),
      notifications.sendMessage("candidate", linkAccount.id, `link-b:${randomUUID()}`, "", [linkFile]),
    ]);
    assert.equal(concurrentLinks.filter(result => result.status === "fulfilled").length, 1);
    assert.equal(concurrentLinks.filter(result => result.status === "rejected" && result.reason?.message === "system_file_already_attached").length, 1);
    assert.equal((await pool.query("SELECT count(*) FROM system_notification_files WHERE file_id=$1", [linkFile.id])).rows[0]!.count, "1");
    assert.equal((await app.inject({ method: "PATCH", url: "/employer/profile", headers: owner.headers, payload: { company_name: "Changed", expected_revision: 1, owner_id: foreign.id } })).statusCode, 400);
    assert.equal((await app.inject({ method: "PATCH", url: "/employer/profile", headers: { ...owner.headers, origin: "https://foreign.example.test" }, payload: { company_name: "Blocked", expected_revision: 1 } })).statusCode, 403);
    const updated = await app.inject({ method: "PATCH", url: "/employer/profile", headers: owner.headers, payload: { company_name: " Updated ", expected_revision: 1 } });
    assert.equal(updated.statusCode, 200); assert.equal(updated.json<{ company_name: string }>().company_name, "Updated");
    const badge = await app.inject({ url: "/employer/chat-unread", headers: owner.headers });
    assert.equal(badge.statusCode, 200); assert.equal(badge.json<ChatUnreadStatus>().recruitment_unread_count, 6); assert.equal(calls, 1);
    await pool.query("UPDATE candidate_profiles SET processing_consent=false,pool_consent=false,consent_accepted_at=NULL WHERE candidate_id=$1", [candidate.id]);
    const closed = await app.inject({ url: "/employer/chat-unread", headers: owner.headers });
    assert.equal(closed.json<ChatUnreadStatus>().recruitment_unread_count, 0); assert.equal(calls, 1);
    await pool.query("UPDATE candidate_profiles SET processing_consent=true,pool_consent=true,consent_accepted_at=now() WHERE candidate_id=$1", [candidate.id]);
    await pool.query("CREATE SCHEMA feed_pipeline; CREATE TABLE feed_pipeline.decisions(receipt_id uuid PRIMARY KEY,owner_id uuid,candidate_id uuid,action text,created_at timestamptz)");
    await pool.query("INSERT INTO feed_pipeline.decisions VALUES($1,$2,$3,'like',now()),($4,$2,$3,'like',now()-interval '1 day')", [randomUUID(), owner.id, candidate.id, randomUUID()]);
    const candidatePage = await app.inject({ url: "/candidate/system-chat", headers: candidate.headers });
    assert.equal(candidatePage.statusCode, 200); assert.equal(candidatePage.json<SystemChatPage>().items.length, 2);
    assert.equal(candidatePage.json<SystemChatPage>().items[1]?.type, "matching.resume_liked");
    assert.equal((await notifications.page("candidate", candidate.id)).items.length, 2, "source reconciliation must deduplicate likes");
  } finally { await app.close(); await pool.end(); }
});
