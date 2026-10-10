/** Isolated real-PostgreSQL acceptance; never run against a live account database. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore } from "../src/recruitment/store.js";
import { NotificationStore } from "../src/notifications/store.js";
import { NotificationWorker } from "../src/notifications/worker.js";

const databaseUrl = process.env.NOTIFICATION_TEST_DATABASE_URL;

test("notification outbox preserves source transaction, idempotency, consent and lease fencing", { skip: !databaseUrl }, async t => {
  const pool = new Pool({ connectionString: databaseUrl });
  const notifications = new NotificationStore(pool, "https://comit.example.test");
  try {
    await migrate(pool);
    const owner = randomUUID(), candidate = randomUUID(), other = randomUUID();
    for (const [id, role] of [[owner, "employer"], [candidate, "candidate"], [other, "candidate"]]) {
      await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
        VALUES($1,$2,$3,true,'account-v1',now())`, [id, `${id}@example.test`, role]);
    }
    const profiles = new ProfileStore(pool);
    await profiles.save(candidate, { expected_revision: 0, fields: { display_name: "Synthetic candidate" }, consent: { processing: true, pool: true, version: "profile-v1" } });
    await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=$1", [candidate]);
    const recruitment = new RecruitmentStore(pool);
    let process = await recruitment.invite(owner, { candidate_id: candidate, idempotency_key: randomUUID(), company_name: "Synthetic company", description: "Synthetic interview" },
      { candidate_id: candidate, vacancy_id: randomUUID(), draft_id: randomUUID(), profile_revision: 11,
        terms: { company_name: "Synthetic company", description: "Synthetic interview", salary: { from_rub: 100000, to_rub: 200000 }, contact_method: "platform" } });
    process = await recruitment.act("candidate", candidate, process.id, { action: "accept", expected_revision: process.revision, idempotency_key: randomUUID() });
    const proposal = { action: "interview_propose" as const, expected_revision: process.revision, idempotency_key: randomUUID(), kind: "technical" as const,
      starts_at: new Date(Date.now() + 86400000).toISOString(), time_zone: "Europe/Moscow", duration_minutes: 45, format: "remote" as const, location: "https://meeting.example.test/private" };

    await t.test("real authorized interview transaction creates both recipients exactly once", async () => {
      process = await recruitment.act("employer", owner, process.id, proposal);
      await recruitment.act("employer", owner, process.id, proposal);
      const rows = (await pool.query("SELECT account_id,kind,source_id FROM notification_mail_outbox ORDER BY kind")).rows;
      assert.equal(rows.length, 2);
      assert.equal(rows[0]?.kind, "candidate_interview"); assert.equal(rows[0]?.account_id, candidate);
      assert.equal(rows[1]?.kind, "employer_interview"); assert.equal(rows[1]?.account_id, owner);
      assert.equal(rows[0]?.source_id, process.interviews[0]?.id);
    });

    await t.test("source rollback removes the queued email in the same transaction", async () => {
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        await client.query(`INSERT INTO recruitment_interviews(id,process_id,kind,state,starts_at,time_zone,duration_minutes,format,location)
          VALUES($1,$2,'general','proposed',now()+interval '1 day','UTC',30,'remote','https://meeting.example.test')`, [randomUUID(), process.id]);
        assert.equal(Number((await client.query("SELECT count(*) FROM notification_mail_outbox")).rows[0]?.count), 4);
        await client.query("ROLLBACK");
        assert.equal(Number((await pool.query("SELECT count(*) FROM notification_mail_outbox")).rows[0]?.count), 2);
      } finally { client.release(); }
    });

    await t.test("parallel workers claim different jobs; delivery uses current recipient and no meeting URL", async () => {
      const [first, second] = await Promise.all([notifications.claim(), notifications.claim()]);
      assert.ok(first); assert.ok(second); assert.notEqual(first.job_id, second.job_id);
      assert.notEqual(first.lease_token, second.lease_token);
      assert.equal(first.email.includes("@example.test"), true);
      assert.match(first.destination, /\/chat\//);
      assert.equal(JSON.stringify(first).includes("meeting.example.test"), false);
      await notifications.finish(first.job_id, first.lease_token);
      await notifications.fail(second.job_id, second.lease_token, "smtp_unavailable", true);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [first.job_id])).rows[0]?.status, "delivered");
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [second.job_id])).rows[0]?.status, "pending");
      await notifications.finish(second.job_id, first.lease_token);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [second.job_id])).rows[0]?.status, "pending");
    });

    await t.test("cancelled interview and withdrawn profile processing suppress delivery", async () => {
      await pool.query("UPDATE notification_mail_outbox SET available_at=now(),status='pending',attempts=0 WHERE status<>'delivered'");
      const claimed = await notifications.claim(); assert.ok(claimed);
      await profiles.save(candidate, { expected_revision: 1, fields: {}, consent: { processing: false, pool: false, version: "profile-v1" } });
      assert.equal(await notifications.refresh(claimed.job_id, claimed.lease_token), null);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [claimed.job_id])).rows[0]?.status, "dropped");
      await profiles.save(candidate, { expected_revision: 2, fields: {}, consent: { processing: true, pool: true, version: "profile-v1" } });
      await pool.query("UPDATE notification_mail_outbox SET status='pending',attempts=0,available_at=now() WHERE kind='candidate_interview'");
      await pool.query("UPDATE recruitment_interviews SET state='cancelled' WHERE process_id=$1", [process.id]);
      assert.equal(await notifications.claim(), null);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE kind='candidate_interview'")).rows[0]?.status, "dropped");
    });

    await t.test("matching reconciliation imports committed likes after cutover, never historical events or dislikes", async () => {
      // Minimal authoritative source fixture in this disposable DB; API never writes production matching tables.
      await pool.query("CREATE SCHEMA feed_pipeline");
      await pool.query("CREATE TABLE feed_pipeline.decisions(receipt_id uuid PRIMARY KEY,owner_id uuid,vacancy_id uuid,candidate_id uuid,action text,created_at timestamptz NOT NULL)");
      const vacancy = randomUUID();
      for (const [action, date] of [["like", "now()-interval '1 day'"], ["like", "clock_timestamp()"], ["dislike", "clock_timestamp()"]]) {
        await pool.query(`INSERT INTO feed_pipeline.decisions VALUES($1,$2,$3,$4,$5,${date})`, [randomUUID(), owner, vacancy, candidate, action]);
      }
      await notifications.reconcileLikes(); await notifications.reconcileLikes();
      assert.equal(Number((await pool.query("SELECT count(*) FROM notification_mail_outbox WHERE kind='resume_liked'")).rows[0]?.count), 1);
      const liked = await notifications.claim(); assert.ok(liked); assert.equal(liked.kind, "resume_liked");
      assert.equal(liked.company, undefined); assert.equal(liked.destination, "https://comit.example.test/candidate/");
      await pool.query("UPDATE accounts SET processing_consent_at=NULL,processing_consent_version=NULL WHERE id=$1", [candidate]);
      assert.equal(await notifications.refresh(liked.job_id, liked.lease_token), null);
      await pool.query("UPDATE accounts SET processing_consent_at=now(),processing_consent_version='account-v1' WHERE id=$1", [candidate]);
    });

    await t.test("future monthly-task producer integration is validated and deduplicated", async () => {
      const client = await pool.connect();
      const source = randomUUID();
      try {
        await client.query("BEGIN");
        await NotificationStore.enqueueMonthlyTask(client, candidate, source, "Synthetic task", new Date(Date.now() + 86400000));
        await NotificationStore.enqueueMonthlyTask(client, candidate, source, "Synthetic task", new Date(Date.now() + 86400000));
        await NotificationStore.enqueueMonthlyTask(client, other, randomUUID(), "No published profile", new Date(Date.now() + 86400000));
        await assert.rejects(() => NotificationStore.enqueueMonthlyTask(client, candidate, source, "bad\nname", new Date(Date.now() + 86400000)));
        await client.query("COMMIT");
      } finally { client.release(); }
      assert.equal(Number((await pool.query("SELECT count(*) FROM notification_mail_outbox WHERE kind='monthly_task'")).rows[0]?.count), 1);
      const sent: string[] = [];
      assert.equal(await new NotificationWorker(notifications, { sendNotification: async message => { sent.push(message.task!); } }).processOnce(), true);
      assert.deepEqual(sent, ["Synthetic task"]);
    });

    await t.test("crashed leases reclaim monotonically; stale workers and max-attempt jobs stay fenced", async () => {
      const id = randomUUID();
      await pool.query(`INSERT INTO notification_mail_outbox(id,account_id,kind,source_id,task_title,expires_at)
        VALUES($1,$2,'monthly_task',$3,'Retry synthetic task',now()+interval '1 day')`, [id, candidate, randomUUID()]);
      let job = await notifications.claim(); assert.ok(job);
      const stale = job;
      await pool.query("UPDATE notification_mail_outbox SET lease_until=now()-interval '1 second' WHERE id=$1", [id]);
      job = await notifications.claim(); assert.ok(job); assert.notEqual(job.lease_token, stale.lease_token);
      await notifications.fail(id, stale.lease_token, "rejected", false);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [id])).rows[0]?.status, "processing");
      await pool.query("UPDATE notification_mail_outbox SET attempts=5 WHERE id=$1", [id]);
      await notifications.fail(id, job.lease_token, "smtp_unavailable", true);
      assert.equal((await pool.query("SELECT status FROM notification_mail_outbox WHERE id=$1", [id])).rows[0]?.status, "failed");
      assert.equal(await notifications.claim(), null);
    });
  } finally { await pool.end(); }
});
