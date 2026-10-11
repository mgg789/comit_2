/** Durable operator sessions and read cursors for the support console; content stays in system_notifications. */
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import type { Pool, QueryResultRow } from "pg";
import type { AccountRole, SupportConversationPage, SupportMessagePage, SupportReadStatus, SystemChatFile } from "@comit/shared";
import { hashSessionId, createSessionId } from "../auth/crypto.js";
import { SystemNotificationsError } from "../notifications/system-store.js";

/** Constant-time equality for low-entropy credentials (operator email and access key). */
export function credentialMatches(candidate: string, expected: string): boolean {
  const digest = (value: string) => createHash("sha256").update(value, "utf8").digest();
  return timingSafeEqual(digest(candidate), digest(expected));
}

interface ConversationRow extends QueryResultRow {
  account_id: string; email: string; role: AccountRole; last_sequence: string;
  last_text: string | null; last_created_at: Date | null; unread_count: string;
}
interface MessageRow extends QueryResultRow {
  id: string; sequence: string; type: string; text: string; created_at: Date;
  target: import("@comit/shared").SystemNotificationTarget | null; files: SystemChatFile[];
}

const validSequence = (value: number) => Number.isSafeInteger(value) && value >= 0;

/** Operator identity is config-derived: the database only proves the session exists. */
export class SupportConsoleStore {
  constructor(private readonly pool: Pool) {}

  /** Verify credentials and mint a revocable session; null means wrong email or key. */
  async login(email: string, accessKey: string, config: { email: string; accessKey: string; sessionTtlDays: number }):
    Promise<{ token: string; expires_at: string } | null> {
    if (!credentialMatches(email, config.email) || !credentialMatches(accessKey, config.accessKey)) return null;
    return await this.create(createSessionId(), config);
  }

  /** Persist a session token (already generated with sufficient entropy). */
  async create(token: string, config: { sessionTtlDays: number }): Promise<{ token: string; expires_at: string }> {
    const expires = new Date(Date.now() + config.sessionTtlDays * 86_400_000);
    await this.pool.query("INSERT INTO support_sessions(id,token_hash,expires_at) VALUES($1,$2,$3)",
      [randomUUID(), hashSessionId(token).toString("hex"), expires]);
    // Bounded housekeeping: expired and revoked sessions are useless after a grace month.
    await this.pool.query("DELETE FROM support_sessions WHERE expires_at < now() - interval '30 days' OR revoked_at < now() - interval '30 days'");
    return { token, expires_at: expires.toISOString() };
  }

  /** True while the session exists, unexpired and not revoked. */
  async session(token: string): Promise<boolean> {
    const result = await this.pool.query("SELECT 1 FROM support_sessions WHERE token_hash=$1 AND revoked_at IS NULL AND expires_at>now()",
      [hashSessionId(token).toString("hex")]);
    return result.rowCount === 1;
  }

  /** Revoke one session; unknown tokens return silently to keep the endpoint uniform. */
  async revoke(token: string): Promise<void> {
    await this.pool.query("UPDATE support_sessions SET revoked_at=now() WHERE token_hash=$1 AND revoked_at IS NULL",
      [hashSessionId(token).toString("hex")]);
  }

  /** Offset page over every account inbox, newest activity first, with operator unread counts. */
  async conversations(offset: number, limit: number): Promise<SupportConversationPage> {
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
      throw new SystemNotificationsError(422, "invalid_support_page");
    }
    const rows = (await this.pool.query<ConversationRow>(`SELECT i.account_id,a.email,a.role::text AS role,i.last_sequence,
        last.text AS last_text,last.created_at AS last_created_at,
        (SELECT count(*) FROM system_notifications n WHERE n.account_id=i.account_id AND n.type='support.user_message'
          AND n.sequence>COALESCE(r.read_sequence,0)) AS unread_count
      FROM system_inboxes i JOIN accounts a ON a.id=i.account_id
      LEFT JOIN support_conversation_reads r ON r.account_id=i.account_id
      LEFT JOIN LATERAL (SELECT n.text,n.created_at FROM system_notifications n WHERE n.account_id=i.account_id
        ORDER BY n.sequence DESC LIMIT 1) last ON true
      ORDER BY last.created_at DESC NULLS LAST,i.account_id
      OFFSET $1 LIMIT $2`, [offset, limit + 1])).rows;
    return {
      items: rows.slice(0, limit).map(row => ({
        account_id: row.account_id, email: row.email, role: row.role, last_sequence: Number(row.last_sequence),
        last_text: row.last_text, last_created_at: row.last_created_at?.toISOString() ?? null,
        unread_count: Number(row.unread_count),
      })),
      has_more: rows.length > limit,
    };
  }

  /** Ascending message page for one conversation; never advances the account's own receipts. */
  async messages(accountId: string, after = 0, limit = 100): Promise<SupportMessagePage> {
    if (!validSequence(after) || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
      throw new SystemNotificationsError(422, "invalid_support_cursor");
    }
    const rows = (await this.pool.query<MessageRow>(`SELECT n.id,n.sequence,n.type,n.text,n.target,n.created_at,
        (SELECT COALESCE(json_agg(json_build_object('id',f.id,'name',f.name,'media_type',f.media_type,'size',f.size_bytes,'sha256',f.sha256) ORDER BY nf.ordinal),'[]'::json)
          FROM system_notification_files nf JOIN system_chat_files f ON f.id=nf.file_id WHERE nf.notification_id=n.id) AS files
      FROM system_notifications n WHERE n.account_id=$1 AND n.sequence>$2 ORDER BY n.sequence LIMIT $3`, [accountId, after, limit + 1])).rows;
    const items = rows.slice(0, limit).map(row => ({ id: row.id, sequence: Number(row.sequence), type: row.type, text: row.text,
      target: row.target, files: row.files ?? [], created_at: row.created_at.toISOString() }));
    return { items, next_sequence: items.at(-1)?.sequence ?? after, has_more: rows.length > limit };
  }

  /** Monotonic operator watermark bounded by existing messages; unknown inbox is 404, future cursor is 409. */
  async markRead(accountId: string, sequence: number): Promise<SupportReadStatus> {
    if (!validSequence(sequence)) throw new SystemNotificationsError(422, "invalid_support_read_sequence");
    const result = await this.pool.query<QueryResultRow & { read_sequence: string | null; unread_count: string; last_sequence: string }>(`WITH inbox AS (
        SELECT account_id,last_sequence FROM system_inboxes WHERE account_id=$1
      ), upsert AS (
        INSERT INTO support_conversation_reads(account_id,read_sequence) SELECT account_id,$2 FROM inbox WHERE $2<=last_sequence
        ON CONFLICT (account_id) DO UPDATE SET read_sequence=greatest(support_conversation_reads.read_sequence,excluded.read_sequence), updated_at=now()
        RETURNING read_sequence
      ) SELECT inbox.last_sequence, COALESCE((SELECT read_sequence FROM upsert),(SELECT read_sequence FROM support_conversation_reads WHERE account_id=$1)) AS read_sequence,
        (SELECT count(*) FROM system_notifications n WHERE n.account_id=$1 AND n.type='support.user_message'
          AND n.sequence>COALESCE((SELECT read_sequence FROM upsert),(SELECT read_sequence FROM support_conversation_reads WHERE account_id=$1),0)) AS unread_count FROM inbox`,
      [accountId, sequence]);
    const row = result.rows[0];
    if (!row) throw new SystemNotificationsError(404, "account_not_found");
    if (sequence > Number(row.last_sequence)) throw new SystemNotificationsError(409, "support_read_not_available");
    return { read_sequence: Number(row.read_sequence), unread_count: Number(row.unread_count) };
  }
}
