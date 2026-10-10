import type { Pool, PoolClient, QueryResultRow } from "pg";

/** Roles that can own a Comit account. */
export type AccountRole = "candidate" | "employer";

/** Public account record returned after a verified login. */
export interface AuthAccount {
  id: string;
  email: string;
  role: AccountRole;
  email_verified: true;
  created_at: string;
  consent: {
    processing: true;
    version: string;
    accepted_at: string;
  };
}

/** Result of issuing a one-time email challenge. */
export interface ChallengeIssued {
  challenge_id: string;
  expires_at: string;
  resend_after: string;
  /** Random challenge code returned only by explicitly enabled development delivery. */
  dev_code?: string;
}

/** Result of consuming a valid challenge and creating a revocable session. */
export interface VerificationResult {
  account: AuthAccount;
  session_id: string;
  expires_at: string;
}

/** Plaintext mail data held only by the worker between claim and SMTP ACK. */
export interface MailJob {
  job_id: string;
  challenge_id: string;
  email: string;
  code: string;
  role: AccountRole;
  expires_at: string;
  lease_token: string;
  leased_until: string;
  attempts: number;
}

/** Safe, structured error returned by auth methods and suitable for HTTP mapping. */
export class AuthError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly retryAfter?: string;

  public constructor(status: number, code: string, retryAfter?: string) {
    super(code);
    this.name = "AuthError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

/** Keep PostgreSQL timestamp output stable for HTTP and token consumers. */
export function timestamp(value: Date | string): string {
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

/** Run a callback in a transaction while preserving the original database error. */
export async function transaction<T>(pool: Pool, callback: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await callback(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve the original error when the connection itself is unavailable.
    }
    throw error;
  } finally {
    client.release();
  }
}

/** Read one typed row without widening it to an unsafe `any` value. */
export async function queryOne<Row extends QueryResultRow>(client: PoolClient, sql: string, values: unknown[]): Promise<Row | null> {
  const result = await client.query<Row>(sql, values);
  return result.rows[0] ?? null;
}
