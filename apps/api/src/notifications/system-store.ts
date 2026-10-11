/** Durable read-only Comit inbox, independent of QGramm and recruitment lifecycle. */
import { randomUUID } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type { AccountRole, SystemChatPage, SystemChatReadStatus, SystemNotification, SystemNotificationTarget, ChatUnreadStatus } from "@comit/shared";
import { fetchGlobalRecruitmentUnread, type UnreadChatGrant } from "./unread.js";
/** Safe notification validation/receipt errors for the public API. */
export class SystemNotificationsError extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}
interface InboxRow extends QueryResultRow { last_sequence: string; delivered_sequence: string; read_sequence: string }
interface NotificationRow extends QueryResultRow {
  id: string; sequence: string; type: string; text: string; created_at: Date; target: SystemNotificationTarget | null;
}
const notification = (row: NotificationRow): SystemNotification => ({ ...row, sequence: Number(row.sequence), created_at: row.created_at.toISOString() });
const validSequence = (value: number) => Number.isSafeInteger(value) && value >= 0;

/** Serializes account-local append, delivery and monotonic read receipts in PostgreSQL. */
export class SystemNotificationsStore {
  constructor(private readonly pool: Pool) {}
  private async transaction<T>(operation: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try { await client.query("BEGIN"); const value = await operation(client); await client.query("COMMIT"); return value; }
    catch (error) { await client.query("ROLLBACK"); throw error; }
    finally { client.release(); }
  }
  private async inbox(client: PoolClient, accountId: string, role?: AccountRole): Promise<InboxRow> {
    const account = await client.query("SELECT id FROM accounts WHERE id=$1 AND ($2::text IS NULL OR role=$2)", [accountId, role ?? null]);
    if (!account.rowCount) throw new SystemNotificationsError(404, "account_not_found");
    await client.query("INSERT INTO system_inboxes(account_id) VALUES($1) ON CONFLICT DO NOTHING", [accountId]);
    return (await client.query<InboxRow>("SELECT last_sequence,delivered_sequence,read_sequence FROM system_inboxes WHERE account_id=$1 FOR UPDATE", [accountId])).rows[0]!;
  }
  /** Append within the producer's existing transaction; duplicate keys return the original item without incrementing counters. */
  async append(client: PoolClient, accountId: string, key: string, type: string, text: string,
    target: SystemNotificationTarget | null = null): Promise<SystemNotification> {
    if (!key || key.length > 200 || !/^[a-z][a-z0-9_.]{0,79}$/.test(type) || !text.trim() || [...text].length > 2000
      || target && (!/^\/(?!\/)[a-zA-Z0-9/_-]*$/.test(target.path)
        || !["recruitment", "vacancy", "candidate_profile", "settings"].includes(target.kind)
        || target.id !== undefined && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(target.id)))
      throw new SystemNotificationsError(422, "invalid_system_notification");
    const inbox = await this.inbox(client, accountId);
    const existing = (await client.query<NotificationRow>("SELECT id,sequence,type,text,target,created_at FROM system_notifications WHERE account_id=$1 AND idempotency_key=$2", [accountId, key])).rows[0];
    if (existing) return notification(existing);
    if (Number(inbox.last_sequence) === Number.MAX_SAFE_INTEGER) throw new SystemNotificationsError(409, "system_sequence_exhausted");
    const sequence = Number(inbox.last_sequence) + 1;
    await client.query("UPDATE system_inboxes SET last_sequence=$2 WHERE account_id=$1", [accountId, sequence]);
    const result = await client.query<NotificationRow>(`INSERT INTO system_notifications(id,account_id,sequence,idempotency_key,type,text,target)
      VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id,sequence,type,text,target,created_at`, [randomUUID(), accountId, sequence, key, type, text, target]);
    return notification(result.rows[0]!);
  }
  /** Publish a standalone durable notification using the same idempotency boundary as transactional producers. */
  async publish(accountId: string, key: string, type: string, text: string, target: SystemNotificationTarget | null = null): Promise<SystemNotification> {
    return this.transaction(client => this.append(client, accountId, key, type, text, target));
  }
  /** Record the account's own message in the Comit conversation and acknowledge delivery:
      the sender has just seen the thread, so only future Comit replies may raise unread. */
  async sendMessage(role: AccountRole, accountId: string, key: string, text: string): Promise<SystemNotification> {
    return this.transaction(async client => {
      await this.inbox(client, accountId, role);
      const item = await this.append(client, accountId, key, "support.user_message", text);
      await client.query(`UPDATE system_inboxes SET delivered_sequence=greatest(delivered_sequence,$2),
        read_sequence=greatest(read_sequence,delivered_sequence,$2) WHERE account_id=$1`, [accountId, item.sequence]);
      return item;
    });
  }
  /** Reconcile newly committed existing monthly-task and like sources without replaying historical mail or interview events. */
  private async reconcile(client: PoolClient, role: AccountRole, accountId: string): Promise<void> {
    if (role !== "candidate") return;
    const consent = await client.query("SELECT 1 FROM candidate_profiles WHERE candidate_id=$1 AND processing_consent AND pool_consent", [accountId]);
    if (!consent.rowCount) return;
    const sources = (await client.query<{ mail: boolean; likes: boolean }>(`SELECT
      to_regclass('notification_mail_outbox') IS NOT NULL AS mail,
      to_regclass('feed_pipeline.decisions') IS NOT NULL AS likes`)).rows[0]!;
    if (sources.mail) {
      const jobs = (await client.query<{ source_id: string }>(`SELECT o.source_id FROM notification_mail_outbox o
        WHERE o.account_id=$1 AND o.kind='monthly_task' AND o.status<>'dropped' AND o.expires_at>now()
          AND o.created_at>=(SELECT installed_at FROM system_notification_cutover)
          AND NOT EXISTS(SELECT 1 FROM system_notifications n WHERE n.account_id=$1 AND n.idempotency_key='monthly_task:'||o.source_id::text)
        ORDER BY o.created_at,o.id LIMIT 100`, [accountId])).rows;
      for (const job of jobs) await this.append(client, accountId, `monthly_task:${job.source_id}`, "testing.monthly_task", "Для вас доступно новое ежемесячное задание.");
    }
    if (sources.likes) {
      const likes = (await client.query<{ receipt_id: string }>(`SELECT d.receipt_id FROM feed_pipeline.decisions d
        JOIN accounts a ON a.id=d.owner_id AND a.role='employer' AND a.email_verified AND a.processing_consent_at IS NOT NULL
        WHERE d.candidate_id=$1 AND d.action='like' AND d.created_at>=(SELECT installed_at FROM system_notification_cutover)
          AND NOT EXISTS(SELECT 1 FROM system_notifications n WHERE n.account_id=$1 AND n.idempotency_key='resume_liked:'||d.receipt_id::text)
        ORDER BY d.created_at,d.receipt_id LIMIT 100`, [accountId])).rows;
      for (const like of likes) await this.append(client, accountId, `resume_liked:${like.receipt_id}`, "matching.resume_liked", "Работодатель сохранил ваше резюме в подборку.");
    }
  }
  private async welcome(client: PoolClient, role: AccountRole, accountId: string): Promise<void> {
    await this.inbox(client, accountId, role);
    await this.append(client, accountId, "welcome:v1", "system.welcome",
      "Добро пожаловать в Comit! Здесь будут уведомления о подборе, приглашениях и изменениях статусов.");
    await this.reconcile(client, role, accountId);
  }
  private async status(client: PoolClient, accountId: string): Promise<SystemChatReadStatus> {
    const result = await client.query<{ read_sequence: string; unread_count: string }>(`SELECT i.read_sequence,
      (SELECT count(*) FROM system_notifications n WHERE n.account_id=i.account_id AND n.sequence>i.read_sequence) AS unread_count
      FROM system_inboxes i WHERE i.account_id=$1`, [accountId]);
    const row = result.rows[0]!;
    return { read_sequence: Number(row.read_sequence), unread_count: Number(row.unread_count) };
  }
  /** Return only this role/account's durable items and record the greatest sequence actually delivered. */
  async page(role: AccountRole, accountId: string, after = 0, limit = 50): Promise<SystemChatPage> {
    if (!validSequence(after) || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new SystemNotificationsError(422, "invalid_system_cursor");
    return this.transaction(async client => {
      await this.welcome(client, role, accountId);
      const rows = (await client.query<NotificationRow>(`SELECT id,sequence,type,text,target,created_at FROM system_notifications
        WHERE account_id=$1 AND sequence>$2 ORDER BY sequence LIMIT $3`, [accountId, after, limit + 1])).rows;
      const items = rows.slice(0, limit).map(notification);
      const next = items.at(-1)?.sequence ?? after;
      if (items.length) await client.query("UPDATE system_inboxes SET delivered_sequence=greatest(delivered_sequence,$2) WHERE account_id=$1", [accountId, next]);
      return { id: "comit-system", title: "Comit", items, next_sequence: next, ...await this.status(client, accountId), has_more: rows.length > limit };
    });
  }
  /** Reject undelivered future receipts; repeated and older receipts never reduce the read watermark. */
  async read(role: AccountRole, accountId: string, sequence: number): Promise<SystemChatReadStatus> {
    if (!validSequence(sequence)) throw new SystemNotificationsError(422, "invalid_system_read_sequence");
    return this.transaction(async client => {
      const inbox = await this.inbox(client, accountId, role);
      if (sequence > Number(inbox.delivered_sequence)) throw new SystemNotificationsError(409, "system_read_not_delivered");
      await client.query("UPDATE system_inboxes SET read_sequence=greatest(read_sequence,$2) WHERE account_id=$1", [accountId, sequence]);
      return this.status(client, accountId);
    });
  }
  /** Sum every invited participant chat with active processing consent, holding consent locks through metadata transport. */
  async unread(role: AccountRole, accountId: string, configuration: { url: string; token: string } | null, transport: typeof fetch): Promise<ChatUnreadStatus> {
    // Release the inbox lock before acquiring process locks: recruitment producers lock
    // processes before appending to inboxes, so the opposite ordering could deadlock.
    const system = await this.transaction(async client => {
      await this.welcome(client, role, accountId);
      return this.status(client, accountId);
    });
    return this.transaction(async client => {
      const rows = (await client.query<{ id: string; owner_id: string; candidate_id: string }>(`SELECT p.id,p.owner_id,p.candidate_id
        FROM recruitment_processes p JOIN candidate_profiles c ON c.candidate_id=p.candidate_id
        WHERE (CASE WHEN $2='employer' THEN p.owner_id ELSE p.candidate_id END)=$1
          AND p.invited_at IS NOT NULL AND c.processing_consent=true ORDER BY p.id FOR SHARE OF p,c`, [accountId, role])).rows;
      const grants: UnreadChatGrant[] = rows.map(row => ({ process_id: row.id, owner_id: row.owner_id, candidate_id: row.candidate_id, actor_id: accountId }));
      const recruitment = await fetchGlobalRecruitmentUnread(grants, configuration, transport);
      const total = recruitment === null ? null : recruitment + system.unread_count;
      return { system_unread_count: system.unread_count, recruitment_unread_count: recruitment,
        total_unread_count: total !== null && Number.isSafeInteger(total) ? total : null };
    });
  }
}
