/** Durable event mail: sources keep authority and addresses are resolved only just before delivery. */
import { randomUUID } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import { transaction } from "../auth/database.js";
import type { MailDeliveryCode } from "../auth/mail.js";
import { notificationSiteUrl, type NotificationKind, type NotificationMail } from "./template.js";

interface JobRow extends QueryResultRow {
  id: string; account_id: string; kind: NotificationKind; source_id: string;
  task_title: string | null; expires_at: Date; lease_token: string;
}

/** An in-memory guarded delivery; persisted jobs contain only account/source ids and task title. */
export interface NotificationJob extends NotificationMail { lease_token: string; }

/** Injectable worker contract; refresh resolves source, current consent, recipient and current lease. */
export interface NotificationJobStore {
  reconcileLikes(): Promise<void>;
  claim(): Promise<NotificationJob | null>;
  refresh(jobId: string, leaseToken: string): Promise<NotificationJob | null>;
  finish(jobId: string, leaseToken: string): Promise<void>;
  fail(jobId: string, leaseToken: string, code: MailDeliveryCode, retryable: boolean): Promise<void>;
}

/** Real PostgreSQL queue with fenced leases, retry cap, cancellation and read-only source reconciliation. */
export class NotificationStore implements NotificationJobStore {
  private readonly site: URL;
  constructor(private readonly pool: Pool, site: string, production = true) {
    this.site = notificationSiteUrl(site, production);
  }

  /** Collect only newly committed matching likes after migration cutover; matching tables remain read-only. */
  async reconcileLikes(): Promise<void> {
    const available = (await this.pool.query<{ ready: boolean }>(`SELECT
      to_regclass('feed_pipeline.decisions') IS NOT NULL AS ready`)).rows[0]?.ready;
    if (!available) return;
    await this.pool.query(`INSERT INTO notification_mail_outbox(id,account_id,kind,source_id,expires_at)
      SELECT gen_random_uuid(),d.candidate_id,'resume_liked',d.receipt_id,d.created_at + interval '7 days'
      FROM feed_pipeline.decisions d JOIN accounts a ON a.id=d.candidate_id AND a.role='candidate'
      WHERE d.action='like' AND d.created_at >= (SELECT installed_at FROM notification_mail_cutover)
        AND d.created_at > now()-interval '7 days'
        AND NOT EXISTS(SELECT 1 FROM notification_mail_outbox o WHERE o.account_id=d.candidate_id
          AND o.kind='resume_liked' AND o.source_id=d.receipt_id)
      ORDER BY d.created_at,d.receipt_id LIMIT 100 ON CONFLICT(account_id,kind,source_id) DO NOTHING`);
  }

  /** Integration boundary for a future authoritative assignment producer, inside its source transaction.
   * No scheduler is invented here: callers must create an actual monthly task and its usable page first.
   */
  static async enqueueMonthlyTask(client: PoolClient, accountId: string, assignmentId: string, title: string, expiresAt: Date): Promise<void> {
    if (!title.trim() || title.length > 300 || /[\u0000-\u001f\u007f]/.test(title)
      || !Number.isFinite(expiresAt.getTime()) || expiresAt.getTime() <= Date.now()) throw new TypeError("invalid_monthly_task_notification");
    await client.query(`INSERT INTO notification_mail_outbox(id,account_id,kind,source_id,task_title,expires_at)
      SELECT $1,a.id,'monthly_task',$3,$4,$5 FROM accounts a JOIN candidate_profiles p ON p.candidate_id=a.id
      WHERE a.id=$2 AND a.role='candidate' AND a.email_verified AND a.processing_consent_at IS NOT NULL
        AND p.processing_consent AND p.pool_consent
      ON CONFLICT(account_id,kind,source_id) DO NOTHING`, [randomUUID(), accountId, assignmentId, title.trim(), expiresAt]);
  }

  /** Claim at most one mail; expired/crashed leases are reclaimed and attempts never exceed five. */
  async claim(): Promise<NotificationJob | null> {
    return transaction(this.pool, async client => {
      await client.query(`UPDATE notification_mail_outbox SET status='dropped',lease_token=NULL,lease_until=NULL,error_code='source_unavailable'
        WHERE status IN ('pending','processing') AND expires_at<=now()`);
      await client.query(`UPDATE notification_mail_outbox SET status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'pending' END,
        lease_token=NULL,lease_until=NULL,error_code='smtp_unavailable' WHERE status='processing' AND lease_until<=now()`);
      // A cancelled item must not prevent the next valid job from being processed.
      for (let scan = 0; scan < 10; scan++) {
        const row = (await client.query<JobRow>(`WITH selected AS (
          SELECT id FROM notification_mail_outbox WHERE status='pending' AND available_at<=now()
            AND attempts<5 AND expires_at>now() ORDER BY available_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE notification_mail_outbox o SET status='processing',attempts=attempts+1,
          lease_token=$1,lease_until=now()+interval '60 seconds' FROM selected WHERE o.id=selected.id RETURNING o.*`, [randomUUID()])).rows[0];
        if (!row) return null;
        const job = await this.resolve(client, row);
        if (job) return job;
        await this.drop(client, row.id, row.lease_token);
      }
      return null;
    });
  }

  private async resolve(client: Pool | PoolClient, row: JobRow): Promise<NotificationJob | null> {
    const recipient = (await client.query<{ email: string; role: string }>(`SELECT email,role FROM accounts
      WHERE id=$1 AND email_verified AND processing_consent_at IS NOT NULL`, [row.account_id])).rows[0];
    if (!recipient) return null;
    const base = { email: recipient.email, job_id: row.id, kind: row.kind, lease_token: row.lease_token };
    if (row.kind === "monthly_task") {
      const profile = (await client.query(`SELECT 1 FROM candidate_profiles WHERE candidate_id=$1 AND processing_consent AND pool_consent`, [row.account_id])).rowCount;
      if (recipient.role !== "candidate" || !profile || !row.task_title) return null;
      return { ...base, task: row.task_title, destination: new URL(`/testing/#/tasks/${row.source_id}`, this.site).href };
    }
    if (row.kind === "candidate_interview" || row.kind === "employer_interview") {
      const meeting = (await client.query<{ process_id: string; starts_at: Date; time_zone: string; company: string; candidate: string; owner_id: string; candidate_id: string }>(`SELECT
        p.id AS process_id,i.starts_at,i.time_zone,p.terms->>'company_name' AS company,
        c.fields->>'display_name' AS candidate,p.owner_id,p.candidate_id
        FROM recruitment_interviews i JOIN recruitment_processes p ON p.id=i.process_id
        JOIN candidate_profiles c ON c.candidate_id=p.candidate_id AND c.processing_consent
        JOIN accounts owner ON owner.id=p.owner_id AND owner.role='employer' AND owner.email_verified AND owner.processing_consent_at IS NOT NULL
        WHERE i.id=$1 AND i.state IN ('proposed','scheduled') AND i.starts_at>now()
          AND p.state IN ('accepted','offer_sent')
          AND CASE WHEN $2='employer_interview' THEN p.owner_id ELSE p.candidate_id END=$3`, [row.source_id, row.kind, row.account_id])).rows[0];
      if (!meeting || recipient.role !== (row.kind === "employer_interview" ? "employer" : "candidate")) return null;
      return { ...base, candidate: meeting.candidate || undefined, company: meeting.company || undefined,
        starts_at: meeting.starts_at, time_zone: meeting.time_zone,
        destination: new URL(`/${recipient.role}/#/chat/${meeting.process_id}`, this.site).href };
    }
    const ready = (await client.query<{ ready: boolean }>("SELECT to_regclass('feed_pipeline.decisions') IS NOT NULL AS ready")).rows[0]?.ready;
    if (!ready || recipient.role !== "candidate") return null;
    const liked = (await client.query<{ owner_id: string; vacancy_id: string }>(`SELECT d.owner_id,d.vacancy_id
      FROM feed_pipeline.decisions d JOIN candidate_profiles c ON c.candidate_id=d.candidate_id
      JOIN accounts owner ON owner.id=d.owner_id AND owner.role='employer' AND owner.email_verified AND owner.processing_consent_at IS NOT NULL
      WHERE d.receipt_id=$1 AND d.action='like' AND d.candidate_id=$2 AND c.processing_consent AND c.pool_consent`, [row.source_id, row.account_id])).rows[0];
    if (!liked) return null;
    let company: string | undefined;
    const draftsReady = (await client.query<{ ready: boolean }>("SELECT to_regclass('agents_service.drafts') IS NOT NULL AS ready")).rows[0]?.ready;
    if (draftsReady) {
      const source = (await client.query<{ company: string | null }>(`SELECT
        COALESCE(published_fields->>'company_name',published_fields->>'company') AS company
        FROM agents_service.drafts WHERE vacancy_id=$1 AND owner_id=$2 AND deleted_at IS NULL LIMIT 1`, [liked.vacancy_id, liked.owner_id])).rows[0];
      company = source?.company ?? undefined;
    }
    return { ...base, company, destination: new URL("/candidate/", this.site).href };
  }

  private async drop(client: Pool | PoolClient, id: string, token: string): Promise<void> {
    await client.query(`UPDATE notification_mail_outbox SET status='dropped',error_code='source_unavailable',lease_token=NULL,lease_until=NULL
      WHERE id=$1 AND lease_token=$2 AND status='processing'`, [id, token]);
  }

  /** Recheck live source and consent immediately before SMTP; stale workers cannot deliver. */
  async refresh(id: string, token: string): Promise<NotificationJob | null> {
    const row = (await this.pool.query<JobRow>(`SELECT * FROM notification_mail_outbox
      WHERE id=$1 AND lease_token=$2 AND status='processing' AND lease_until>now() AND expires_at>now()`, [id, token])).rows[0];
    if (!row) return null;
    const job = await this.resolve(this.pool, row);
    if (!job) await this.drop(this.pool, id, token);
    return job;
  }

  /** Fence successful SMTP acknowledgement; provider delivery itself is at-least-once. */
  async finish(id: string, token: string): Promise<void> {
    await this.pool.query(`UPDATE notification_mail_outbox SET status='delivered',delivered_at=now(),lease_token=NULL,lease_until=NULL,error_code=NULL
      WHERE id=$1 AND lease_token=$2 AND status='processing' AND lease_until>now()`, [id, token]);
  }

  /** Retry only transient failures, using exponential backoff and a five-attempt cap. */
  async fail(id: string, token: string, code: MailDeliveryCode, retryable: boolean): Promise<void> {
    await this.pool.query(`UPDATE notification_mail_outbox SET status=CASE WHEN attempts>=5 OR NOT $4 THEN 'failed' ELSE 'pending' END,
      available_at=now()+make_interval(secs=>LEAST(30*power(2,attempts-1),480)::int),
      error_code=$3,lease_token=NULL,lease_until=NULL WHERE id=$1 AND lease_token=$2 AND status='processing' AND lease_until>now()`, [id, token, code, retryable]);
  }
}
