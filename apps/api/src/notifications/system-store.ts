/** Durable read-only Comit inbox, independent of QGramm and recruitment lifecycle. */
import { createHash, randomUUID } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type { AccountRole, SystemChatFile, SystemChatFileDownload, SystemChatFileUpload, SystemChatPage, SystemChatReadStatus, SystemNotification, SystemNotificationTarget, ChatUnreadStatus } from "@comit/shared";
import { fetchGlobalRecruitmentUnread, type UnreadChatGrant } from "./unread.js";
/** Safe notification validation/receipt errors for the public API. */
export class SystemNotificationsError extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}
interface InboxRow extends QueryResultRow { last_sequence: string; delivered_sequence: string; read_sequence: string }
interface NotificationRow extends QueryResultRow {
  id: string; sequence: string; type: string; text: string; created_at: Date; target: SystemNotificationTarget | null; files: SystemChatFile[];
}
const notification = (row: NotificationRow): SystemNotification => ({ id: row.id, sequence: Number(row.sequence), type: row.type,
  text: row.text, created_at: row.created_at.toISOString(), target: row.target, files: row.files ?? [] });
const validSequence = (value: number) => Number.isSafeInteger(value) && value >= 0;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_FILES = 8;
const MAX_PENDING_BYTES = 64 * 1024 * 1024;
const MAX_ACCOUNT_BYTES = 256 * 1024 * 1024;
const ALLOWED_MEDIA_TYPES = new Set(["application/pdf", "text/plain", "text/markdown", "image/png", "image/jpeg", "image/gif", "image/webp"]);
const FILES_SQL = `(SELECT COALESCE(json_agg(json_build_object('id',f.id,'name',f.name,'media_type',f.media_type,'size',f.size_bytes,'sha256',f.sha256) ORDER BY nf.ordinal),'[]'::json)
  FROM system_notification_files nf JOIN system_chat_files f ON f.id=nf.file_id WHERE nf.notification_id=n.id) AS files`;

const validName = (name: string): boolean => name.length >= 1 && name.length <= 255 && !/[\u0000-\u001f\u007f/\\]/u.test(name) && name.trim().length > 0;
const validateFileMetadata = (file: Pick<SystemChatFile, "id" | "name" | "media_type" | "size" | "sha256">): void => {
  if (!UUID.test(file.id) || !validName(file.name) || !ALLOWED_MEDIA_TYPES.has(file.media_type)
    || !Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_FILE_BYTES || !/^[a-f0-9]{64}$/u.test(file.sha256))
    throw new SystemNotificationsError(422, "invalid_system_file");
};
const validateFiles = (files: readonly SystemChatFile[]): void => {
  if (files.length > MAX_FILES || new Set(files.map(file => file.id)).size !== files.length) throw new SystemNotificationsError(422, "invalid_system_files");
  for (const file of files) validateFileMetadata(file);
};
const requestHash = (file: SystemChatFileUpload, sha256: string): string => createHash("sha256").update(JSON.stringify({
  name: file.name, media_type: file.media_type, sha256,
}), "utf8").digest("hex");
const hasSignature = (mediaType: string, content: Buffer): boolean => {
  if (mediaType === "application/pdf") return content.subarray(0, 5).toString("ascii") === "%PDF-";
  if (mediaType === "image/png") return content.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  if (mediaType === "image/jpeg") return content.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]));
  if (mediaType === "image/gif") return ["GIF87a", "GIF89a"].includes(content.subarray(0, 6).toString("ascii"));
  if (mediaType === "image/webp") return content.subarray(0, 4).toString("ascii") === "RIFF" && content.subarray(8, 12).toString("ascii") === "WEBP";
  return !content.includes(0);
};

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
  private async verifyFiles(client: PoolClient, accountId: string, files: readonly SystemChatFile[]): Promise<void> {
    validateFiles(files);
    if (!files.length) return;
    const rows = (await client.query<SystemChatFile>(`SELECT id,name,media_type,size_bytes AS size,sha256
      FROM system_chat_files WHERE account_id=$1 AND id=ANY($2::uuid[])`, [accountId, files.map(file => file.id)])).rows;
    const byId = new Map(rows.map(file => [file.id, file]));
    for (const file of files) {
      const stored = byId.get(file.id);
      if (!stored || stored.name !== file.name || stored.media_type !== file.media_type || stored.size !== file.size || stored.sha256 !== file.sha256)
        throw new SystemNotificationsError(404, "system_file_not_found");
      const linked = await client.query("SELECT 1 FROM system_notification_files WHERE file_id=$1 LIMIT 1", [file.id]);
      if (linked.rowCount) throw new SystemNotificationsError(409, "system_file_already_attached");
    }
  }
  /** Append within the producer's existing transaction; duplicate keys return the original item without incrementing counters. */
  async append(client: PoolClient, accountId: string, key: string, type: string, text: string,
    target: SystemNotificationTarget | null = null, files: readonly SystemChatFile[] = []): Promise<SystemNotification> {
    if (!key || key.length > 200 || !/^[a-z][a-z0-9_.]{0,79}$/.test(type) || !text.trim() && files.length === 0 || [...text].length > 2000
      || target && (!/^\/(?!\/)[a-zA-Z0-9/_-]*$/.test(target.path)
        || !["recruitment", "vacancy", "candidate_profile", "settings"].includes(target.kind)
        || target.id !== undefined && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(target.id)))
      throw new SystemNotificationsError(422, "invalid_system_notification");
    validateFiles(files);
    const inbox = await this.inbox(client, accountId);
    const existing = (await client.query<NotificationRow>(`SELECT n.id,n.sequence,n.type,n.text,n.target,n.created_at,${FILES_SQL}
      FROM system_notifications n WHERE n.account_id=$1 AND n.idempotency_key=$2`, [accountId, key])).rows[0];
    if (existing) {
      // Historical platform producers use first-wins idempotency and may retry the same key
      // with a refreshed notification body. Support messages and every file-bearing payload
      // are user-visible writes, so retries must retain the exact type/text/file set.
      const strictPayload = type === "support.user_message" || type === "support.reply"
        || existing.type === "support.user_message" || existing.type === "support.reply"
        || files.length > 0 || existing.files.length > 0;
      if (strictPayload) {
        const existingIds = existing.files.map(file => file.id);
        if (existing.type !== type || existing.text !== text || existingIds.length !== files.length
          || existingIds.some((id, index) => id !== files[index]?.id)) throw new SystemNotificationsError(409, "system_idempotency_conflict");
      }
      return notification(existing);
    }
    await this.verifyFiles(client, accountId, files);
    if (Number(inbox.last_sequence) === Number.MAX_SAFE_INTEGER) throw new SystemNotificationsError(409, "system_sequence_exhausted");
    const sequence = Number(inbox.last_sequence) + 1;
    await client.query("UPDATE system_inboxes SET last_sequence=$2 WHERE account_id=$1", [accountId, sequence]);
    const notificationId = randomUUID();
    const result = await client.query<NotificationRow>(`INSERT INTO system_notifications(id,account_id,sequence,idempotency_key,type,text,target)
      VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id,sequence,type,text,target,created_at`, [notificationId, accountId, sequence, key, type, text, target]);
    for (const [ordinal, file] of files.entries()) await client.query(
      "INSERT INTO system_notification_files(notification_id,file_id,ordinal) VALUES($1,$2,$3)", [notificationId, file.id, ordinal]);
    return notification({ ...result.rows[0]!, files: [...files] });
  }
  /** Publish a standalone durable notification using the same idempotency boundary as transactional producers. */
  async publish(accountId: string, key: string, type: string, text: string, target: SystemNotificationTarget | null = null,
    files: readonly SystemChatFile[] = []): Promise<SystemNotification> {
    return this.transaction(client => this.append(client, accountId, key, type, text, target, files));
  }
  /** Record the account's own message in the Comit conversation and acknowledge delivery:
      the sender has just seen the thread, so only future Comit replies may raise unread. */
  async sendMessage(role: AccountRole, accountId: string, key: string, text: string, files: readonly SystemChatFile[] = []): Promise<SystemNotification> {
    return this.transaction(async client => {
      await this.inbox(client, accountId, role);
      const item = await this.append(client, accountId, key, "support.user_message", text || " ", null, files);
      await client.query(`UPDATE system_inboxes SET delivered_sequence=greatest(delivered_sequence,$2),
        read_sequence=greatest(read_sequence,delivered_sequence,$2) WHERE account_id=$1`, [accountId, item.sequence]);
      return item;
    });
  }
  /** Store one bounded support attachment; retries with a changed payload fail closed. */
  async uploadFile(accountId: string, upload: SystemChatFileUpload): Promise<SystemChatFile> {
    if (!UUID.test(upload.idempotency_key) || !validName(upload.name) || !ALLOWED_MEDIA_TYPES.has(upload.media_type)
      || !/^[A-Za-z0-9+/]+={0,2}$/u.test(upload.data_base64) || upload.data_base64.length > 11_184_812)
      throw new SystemNotificationsError(422, "invalid_system_file");
    let content: Buffer;
    try { content = Buffer.from(upload.data_base64, "base64"); }
    catch { throw new SystemNotificationsError(422, "invalid_system_file"); }
    if (!content.length || content.length > MAX_FILE_BYTES || content.toString("base64") !== upload.data_base64 || !hasSignature(upload.media_type, content)) throw new SystemNotificationsError(422, "invalid_system_file");
    const sha256 = createHash("sha256").update(content).digest("hex");
    const hash = requestHash(upload, sha256);
    return this.transaction(async client => {
      await this.inbox(client, accountId);
      await client.query(`DELETE FROM system_chat_files f WHERE f.account_id=$1 AND f.created_at<now()-interval '1 day'
        AND NOT EXISTS(SELECT 1 FROM system_notification_files nf WHERE nf.file_id=f.id)`, [accountId]);
      const existing = (await client.query<{ id: string; request_hash: string; name: string; media_type: string; size: number; sha256: string }>(
        "SELECT id,request_hash,name,media_type,size_bytes AS size,sha256 FROM system_chat_files WHERE account_id=$1 AND idempotency_key=$2",
        [accountId, upload.idempotency_key])).rows[0];
      if (existing) {
        if (existing.request_hash !== hash) throw new SystemNotificationsError(409, "system_file_idempotency_conflict");
        return existing;
      }
      const account = (await client.query<{ total: string }>(`SELECT COALESCE(sum(size_bytes),0)::text AS total
        FROM system_chat_files WHERE account_id=$1`, [accountId])).rows[0]!;
      if (Number(account.total) + content.length > MAX_ACCOUNT_BYTES) throw new SystemNotificationsError(413, "system_file_account_limit");
      const pending = (await client.query<{ total: string }>(`SELECT COALESCE(sum(size_bytes),0)::text AS total FROM system_chat_files f
        WHERE f.account_id=$1 AND NOT EXISTS(SELECT 1 FROM system_notification_files nf WHERE nf.file_id=f.id)`, [accountId])).rows[0]!;
      if (Number(pending.total) + content.length > MAX_PENDING_BYTES) throw new SystemNotificationsError(413, "system_file_storage_limit");
      const result = await client.query<{ id: string }>(`INSERT INTO system_chat_files(id,account_id,idempotency_key,request_hash,name,media_type,size_bytes,sha256,content)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id`, [randomUUID(), accountId, upload.idempotency_key, hash, upload.name, upload.media_type, content.length, sha256, content]);
      return { id: result.rows[0]!.id, name: upload.name, media_type: upload.media_type, size: content.length, sha256 };
    });
  }
  /** Return bytes only for an attachment already published into this account's conversation. */
  async downloadFile(accountId: string, fileId: string): Promise<SystemChatFileDownload> {
    if (!UUID.test(fileId)) throw new SystemNotificationsError(404, "system_file_not_found");
    const result = await this.pool.query<{ id: string; name: string; media_type: string; size: number; sha256: string; content: Buffer }>(`SELECT f.id,f.name,f.media_type,f.size_bytes AS size,f.sha256,f.content
      FROM system_chat_files f WHERE f.id=$1 AND f.account_id=$2
        AND EXISTS(SELECT 1 FROM system_notification_files nf WHERE nf.file_id=f.id)`, [fileId, accountId]);
    const row = result.rows[0];
    if (!row) throw new SystemNotificationsError(404, "system_file_not_found");
    return { id: row.id, name: row.name, media_type: row.media_type, size: Number(row.size), sha256: row.sha256, data_base64: row.content.toString("base64") };
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
      const rows = (await client.query<NotificationRow>(`SELECT n.id,n.sequence,n.type,n.text,n.target,n.created_at,${FILES_SQL}
        FROM system_notifications n WHERE n.account_id=$1 AND n.sequence>$2 ORDER BY n.sequence LIMIT $3`, [accountId, after, limit + 1])).rows;
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
