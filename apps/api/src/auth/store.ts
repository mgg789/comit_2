import { randomUUID } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type { PurgeChatContext } from "../chat/client.js";
import {
  advisoryKey,
  createOtp,
  createSessionId,
  decryptOtpPayload,
  encryptOtpPayload,
  hashIp,
  hashOtp,
  hashSessionId,
  normalizeEmail,
  requireEmail,
  requireRole,
  safeEqual,
} from "./crypto.js";
import {
  AuthError,
  type AccountRole,
  type AuthAccount,
  type ChallengeIssued,
  type MailJob,
  type VerificationResult,
  timestamp,
} from "./database.js";

export { AuthError } from "./database.js";
export type { AccountRole, AuthAccount, ChallengeIssued, MailJob, VerificationResult } from "./database.js";

/** Fixed auth limits kept in one place for API and acceptance-test consumers. */
export const AUTH_LIMITS = {
  challengeTtlMs: 10 * 60_000,
  resendCooldownMs: 60_000,
  emailWindowMs: 15 * 60_000,
  emailIssuesPerWindow: 5,
  ipIssuesPerWindow: 20,
  ipVerificationsPerWindow: 60,
  maxAttempts: 5,
  sessionTtlMs: 7 * 24 * 60 * 60_000,
  mailLeaseMs: 120_000,
  mailMaxAttempts: 5,
} as const;

/** Optional policy values that are process configuration, rather than user input. */
export interface AuthStoreOptions {
  accountConsentVersion?: string;
}

interface ChallengeRow extends QueryResultRow {
  id: string;
  email: string;
  role: AccountRole;
  code_hmac: Buffer | null;
  status: "active" | "consumed" | "expired" | "exhausted" | "invalidated";
  attempts: number;
  expires_at: Date | string;
}

interface AccountRow extends QueryResultRow {
  id: string;
  email: string;
  role: AccountRole;
  email_verified: boolean;
  created_at: Date | string;
  processing_consent_version: string | null;
  processing_consent_at: Date | string | null;
}

interface IssueCountRow extends QueryResultRow {
  count: string;
}

interface OutboxRow extends QueryResultRow {
  id: string;
  challenge_id: string;
  email: string;
  role: AccountRole;
  expires_at: Date | string;
  encrypted_payload: Buffer | null;
  nonce: Buffer | null;
  attempts: number;
  lease_token: string;
  leased_until: Date | string;
}

interface OutboxStatusRow extends QueryResultRow {
  status: "pending" | "processing" | "delivered" | "failed" | "expired";
}

interface SessionRow extends QueryResultRow {
  id: string;
  email: string;
  role: AccountRole;
  email_verified: boolean;
  created_at: Date | string;
  processing_consent_version: string | null;
  processing_consent_at: Date | string | null;
}

function sortedUniqueLocks(values: bigint[]): bigint[] {
  return [...new Set(values.map(String))].map(BigInt).sort((left, right) => left < right ? -1 : left > right ? 1 : 0);
}

async function lockAdvisory(client: PoolClient, values: bigint[]): Promise<void> {
  for (const value of sortedUniqueLocks(values)) await client.query("SELECT pg_advisory_xact_lock($1)", [value]);
}

async function rollback(client: PoolClient): Promise<void> {
  try {
    await client.query("ROLLBACK");
  } catch {
    // Keep the original database or auth error.
  }
}

function account(row: AccountRow | SessionRow): AuthAccount {
  if (!row.email_verified) throw new Error("unverified account row");
  if (row.processing_consent_version === null || row.processing_consent_at === null) {
    throw new AuthError(409, "consent_required");
  }
  return {
    id: row.id,
    email: row.email,
    role: row.role,
    email_verified: true,
    created_at: timestamp(row.created_at),
    consent: { processing: true, version: row.processing_consent_version, accepted_at: timestamp(row.processing_consent_at) },
  };
}

function invalidInput(message: string): AuthError {
  return new AuthError(400, message);
}

/** Durable email OTP, account, session and SMTP outbox store. */
export class AuthStore {
  private readonly consentVersion: string;

  public constructor(private readonly pool: Pool, private readonly secret: string, options: AuthStoreOptions = {}) {
    if (Buffer.byteLength(secret, "utf8") < 32) throw new Error("AUTH_SECRET must be at least 32 bytes");
    this.consentVersion = options.accountConsentVersion ?? process.env.ACCOUNT_CONSENT_VERSION ?? "account-v1";
    if (this.consentVersion.length === 0) throw new Error("ACCOUNT_CONSENT_VERSION must not be empty");
  }

  /** Issue a role-bound OTP with atomic limits; explicit development delivery skips SMTP and returns the generated code. */
  public async issue(roleInput: AccountRole, emailInput: string, ip: string, developmentDelivery = false): Promise<ChallengeIssued> {
    if (developmentDelivery && process.env.NODE_ENV !== "development") throw new Error("auth_dev_code_forbidden");
    let role: AccountRole;
    let email: string;
    let ipHash: Buffer;
    try {
      role = requireRole(roleInput);
      email = requireEmail(normalizeEmail(emailInput));
      ipHash = hashIp(this.secret, ip);
    } catch {
      throw invalidInput("invalid_auth_input");
    }
    const now = Date.now();
    const expiresAt = new Date(now + AUTH_LIMITS.challengeTtlMs);
    const resendAfter = new Date(now + AUTH_LIMITS.resendCooldownMs);
    const client = await this.pool.connect();
    let committed = false;
    try {
      await client.query("BEGIN");
      await lockAdvisory(client, [advisoryKey(`email:${email}`), advisoryKey(`ip:${ipHash.toString("hex")}`)]);
      const registered = await client.query<{ role: AccountRole }>("SELECT role FROM accounts WHERE email = $1", [email]);
      if (registered.rows[0] && registered.rows[0].role !== role) throw new AuthError(409, "role_mismatch");
      const recent = await client.query<IssueCountRow>(
        "SELECT count(*)::text AS count FROM auth_issue_events WHERE email = $1 AND created_at >= now() - interval '15 minutes'",
        [email],
      );
      const recentEmail = Number(recent.rows[0]?.count ?? "0");
      const latest = await client.query<{ created_at: Date | string }>(
        "SELECT created_at FROM auth_issue_events WHERE email = $1 ORDER BY created_at DESC LIMIT 1", [email],
      );
      const latestAt = latest.rows[0]?.created_at;
      if (latestAt !== undefined && now - new Date(latestAt).getTime() < AUTH_LIMITS.resendCooldownMs) {
        const retryAt = new Date(new Date(latestAt).getTime() + AUTH_LIMITS.resendCooldownMs);
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(429, "resend_cooldown", retryAt.toISOString());
      }
      if (recentEmail >= AUTH_LIMITS.emailIssuesPerWindow) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(429, "email_rate_limited");
      }
      const recentIp = await client.query<IssueCountRow>(
        "SELECT count(*)::text AS count FROM auth_issue_events WHERE ip_hmac = $1 AND created_at >= now() - interval '15 minutes'",
        [ipHash],
      );
      if (Number(recentIp.rows[0]?.count ?? "0") >= AUTH_LIMITS.ipIssuesPerWindow) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(429, "ip_rate_limited");
      }

      await client.query(
        `WITH invalidated AS (
           UPDATE auth_challenges SET status = 'invalidated', invalidated_at = now(), code_hmac = NULL
           WHERE email = $1 AND status = 'active' RETURNING id
         ) UPDATE auth_mail_outbox SET status = 'expired', encrypted_payload = NULL, nonce = NULL,
           lease_token = NULL, leased_until = NULL, last_error = 'superseded'
         WHERE challenge_id IN (SELECT id FROM invalidated) AND status IN ('pending', 'processing')`, [email],
      );
      await client.query("INSERT INTO auth_issue_events(email, ip_hmac) VALUES ($1, $2)", [email, ipHash]);

      const challengeId = randomUUID();
      const code = createOtp();
      await client.query(
        `INSERT INTO auth_challenges(id, email, role, code_hmac, ip_hmac, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6)`,
        [challengeId, email, role, hashOtp(this.secret, challengeId, code), ipHash, expiresAt],
      );
      if (!developmentDelivery) {
        const encrypted = encryptOtpPayload(this.secret, { email, code, challengeId });
        await client.query(
          `INSERT INTO auth_mail_outbox(id, challenge_id, email, encrypted_payload, nonce, available_at)
           VALUES ($1, $2, $3, $4, $5, now())`,
          [randomUUID(), challengeId, email, encrypted.ciphertext, encrypted.nonce],
        );
      }
      await client.query("COMMIT");
      committed = true;
      return { challenge_id: challengeId, expires_at: expiresAt.toISOString(), resend_after: resendAfter.toISOString(),
        ...(developmentDelivery ? { dev_code: code } : {}) };
    } catch (error) {
      if (!committed) await rollback(client);
      throw error;
    } finally {
      client.release();
    }
  }

  /** Verify one OTP, consuming it once and creating a seven-day revocable session. */
  public async verify(
    roleInput: AccountRole,
    emailInput: string,
    challengeId: string,
    code: string,
    consentVersion?: string,
    ip?: string,
  ): Promise<VerificationResult> {
    let role: AccountRole;
    let email: string;
    let ipHash: Buffer | null = null;
    try {
      role = requireRole(roleInput);
      email = requireEmail(normalizeEmail(emailInput));
      if (!/^[0-9]{6}$/.test(code)) throw new Error("invalid code");
      if (ip !== undefined) ipHash = hashIp(this.secret, ip);
    } catch {
      throw invalidInput("invalid_auth_input");
    }
    const client = await this.pool.connect();
    let committed = false;
    try {
      await client.query("BEGIN");
      const locks = [advisoryKey(`email:${email}`)];
      if (ipHash) locks.push(advisoryKey(`ip-verify:${ipHash.toString("hex")}`));
      await lockAdvisory(client, locks);
      if (ipHash) {
        const count = await client.query<IssueCountRow>(
          "SELECT count(*)::text AS count FROM auth_verify_events WHERE ip_hmac = $1 AND created_at >= now() - interval '15 minutes'",
          [ipHash],
        );
        if (Number(count.rows[0]?.count ?? "0") >= AUTH_LIMITS.ipVerificationsPerWindow) {
          await client.query("COMMIT");
          committed = true;
          throw new AuthError(429, "verification_rate_limited");
        }
        // Count every syntactically valid verification request, including unknown
        // or already-invalidated challenge IDs, in the shared IP budget.
        await client.query("INSERT INTO auth_verify_events(ip_hmac) VALUES ($1)", [ipHash]);
      }
      const challengeResult = await client.query<ChallengeRow>(
        `SELECT id, email, role, code_hmac, status, attempts, expires_at
         FROM auth_challenges WHERE id = $1 AND email = $2 AND role = $3 FOR UPDATE`,
        [challengeId, email, role],
      );
      const challenge = challengeResult.rows[0];
      if (!challenge) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(401, "invalid_challenge");
      }
      if (challenge.status !== "active") {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(challenge.status === "expired" ? 410 : 409, `challenge_${challenge.status}`);
      }
      if (new Date(challenge.expires_at).getTime() <= Date.now()) {
        await this.expireChallenge(client, challenge.id, "expired");
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(410, "challenge_expired");
      }
      const expected = challenge.code_hmac === null ? Buffer.alloc(0) : hashOtp(this.secret, challenge.id, code);
      if (challenge.code_hmac === null || !safeEqual(expected, challenge.code_hmac)) {
        const wrong = await client.query<{ attempts: number }>(
          "UPDATE auth_challenges SET attempts = attempts + 1 WHERE id = $1 RETURNING attempts", [challenge.id],
        );
        const attempts = wrong.rows[0]?.attempts ?? AUTH_LIMITS.maxAttempts;
        if (attempts >= AUTH_LIMITS.maxAttempts) await this.expireChallenge(client, challenge.id, "exhausted");
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(attempts >= AUTH_LIMITS.maxAttempts ? 429 : 401,
          attempts >= AUTH_LIMITS.maxAttempts ? "challenge_exhausted" : "invalid_code");
      }

      const existing = await client.query<AccountRow>(
        `SELECT id, email, role, email_verified, created_at, processing_consent_version, processing_consent_at
         FROM accounts WHERE email = $1 FOR UPDATE`, [email],
      );
      const current = existing.rows[0];
      if (current && current.role !== role) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(409, "role_mismatch");
      }
      if (!current && consentVersion === undefined) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(409, "consent_required");
      }
      if (!current && consentVersion !== this.consentVersion) {
        await client.query("COMMIT");
        committed = true;
        throw new AuthError(400, "consent_invalid");
      }

      if (current && (current.processing_consent_version === null || current.processing_consent_at === null)) {
        if (consentVersion === undefined) {
          await client.query("COMMIT");
          committed = true;
          throw new AuthError(409, "consent_required");
        }
        if (consentVersion !== this.consentVersion) {
          await client.query("COMMIT");
          committed = true;
          throw new AuthError(400, "consent_invalid");
        }
        await client.query(
          "UPDATE accounts SET processing_consent_version = $2, processing_consent_at = now() WHERE id = $1",
          [current.id, this.consentVersion],
        );
      }

      let accountRow: AccountRow;
      if (current) {
        const updated = await client.query<AccountRow>(
          `UPDATE accounts SET email_verified = true
           WHERE id = $1
           RETURNING id, email, role, email_verified, created_at, processing_consent_version, processing_consent_at`,
          [current.id],
        );
        accountRow = updated.rows[0] as AccountRow;
      } else {
        const inserted = await client.query<AccountRow>(
          `INSERT INTO accounts(id, email, role, email_verified, processing_consent_version, processing_consent_at)
           VALUES ($1, $2, $3, true, $4, now())
           RETURNING id, email, role, email_verified, created_at, processing_consent_version, processing_consent_at`,
          [randomUUID(), email, role, this.consentVersion],
        );
        accountRow = inserted.rows[0] as AccountRow;
      }
      await this.expireChallenge(client, challenge.id, "consumed");
      const sessionId = createSessionId();
      const sessionExpires = new Date(Date.now() + AUTH_LIMITS.sessionTtlMs);
      await client.query(
        `INSERT INTO sessions(id, account_id, role, session_hash, expires_at)
         VALUES ($1, $2, $3, $4, $5)`,
        [randomUUID(), accountRow.id, accountRow.role, hashSessionId(sessionId), sessionExpires],
      );
      await client.query("COMMIT");
      committed = true;
      return { account: account(accountRow), session_id: sessionId, expires_at: sessionExpires.toISOString() };
    } catch (error) {
      if (!committed) await rollback(client);
      throw error;
    } finally {
      client.release();
    }
  }

  /** Persist a ten-minute OAuth intent; opaque state doubles as a browser binding cookie. */
  public async issueOAuthState(role: AccountRole, state: string, consentVersion: string): Promise<void> {
    if (consentVersion !== this.consentVersion) throw new AuthError(400, "consent_invalid");
    await this.pool.query("DELETE FROM auth_oauth_states WHERE expires_at <= now()");
    await this.pool.query("INSERT INTO auth_oauth_states(state_hash, role, consent_version, expires_at) VALUES ($1,$2,$3,now()+interval '10 minutes')",
      [hashSessionId(state), requireRole(role), consentVersion]);
  }

  /** Consume before provider I/O: callback replay and concurrent redemption fail across replicas. */
  public async consumeOAuthState(role: AccountRole, state: string): Promise<boolean> {
    const result = await this.pool.query("DELETE FROM auth_oauth_states WHERE state_hash=$1 AND role=$2 AND expires_at>now() AND consent_version=$3 RETURNING state_hash",
      [hashSessionId(state), requireRole(role), this.consentVersion]);
    return result.rowCount === 1;
  }

  /** Authenticate a provider subject. Existing email accounts are never silently linked by email. */
  public async loginYandex(role: AccountRole, subject: string, emailInput: string): Promise<VerificationResult> {
    const email = requireEmail(emailInput);
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await lockAdvisory(client, [advisoryKey(`email:${email}`), advisoryKey(`yandex:${subject}`)]);
      const linked = await client.query<AccountRow>(
        `SELECT a.* FROM auth_oauth_identities i JOIN accounts a ON a.id=i.account_id WHERE i.provider='yandex' AND i.subject=$1 FOR UPDATE OF a`, [subject]);
      let row = linked.rows[0];
      if (row && row.role !== role) throw new AuthError(409, "role_mismatch");
      if (!row) {
        const existing = await client.query("SELECT id FROM accounts WHERE email=$1", [email]);
        if (existing.rowCount) throw new AuthError(409, "oauth_email_conflict");
        const inserted = await client.query<AccountRow>(
          `INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
           VALUES ($1,$2,$3,true,$4,now()) RETURNING *`, [randomUUID(), email, role, this.consentVersion]);
        row = inserted.rows[0];
        if (!row) throw new Error("oauth_account_missing");
        await client.query("INSERT INTO auth_oauth_identities(provider,subject,account_id) VALUES ('yandex',$1,$2)", [subject,row.id]);
      }
      // Record the current consent accepted on this OAuth start, including legacy accounts.
      const accepted = await client.query<AccountRow>(
        "UPDATE accounts SET processing_consent_version=$2,processing_consent_at=now() WHERE id=$1 RETURNING *", [row.id,this.consentVersion]);
      row = accepted.rows[0];
      if (!row) throw new Error("oauth_account_missing");
      const sessionId = createSessionId();
      const expiresAt = new Date(Date.now()+AUTH_LIMITS.sessionTtlMs);
      await client.query("INSERT INTO sessions(id,account_id,role,session_hash,expires_at) VALUES ($1,$2,$3,$4,$5)",
        [randomUUID(),row.id,role,hashSessionId(sessionId),expiresAt]);
      await client.query("COMMIT");
      return { account: account(row), session_id: sessionId, expires_at: expiresAt.toISOString() };
    } catch (error) { await rollback(client); throw error; }
    finally { client.release(); }
  }

  /** Resolve a live session to its account, respecting role and database revocation. */
  public async session(roleInput: AccountRole, accountId: string, sessionId: string): Promise<AuthAccount | null> {
    let role: AccountRole;
    try { role = requireRole(roleInput); } catch { return null; }
    const result = await this.pool.query<SessionRow>(
      `SELECT a.id, a.email, a.role, a.email_verified, a.created_at,
              a.processing_consent_version, a.processing_consent_at
       FROM sessions s JOIN accounts a ON a.id = s.account_id
       WHERE s.account_id = $1 AND s.role = $2 AND s.session_hash = $3
         AND s.revoked_at IS NULL AND s.expires_at > now() AND a.role = $2`,
      [accountId, role, hashSessionId(sessionId)],
    );
    const row = result.rows[0];
    if (!row || row.processing_consent_version === null || row.processing_consent_at === null) return null;
    return account(row);
  }

  /** Revoke exactly one bearer session. */
  public async revoke(roleInput: AccountRole, accountId: string, sessionId: string): Promise<boolean> {
    let role: AccountRole;
    try { role = requireRole(roleInput); } catch { return false; }
    const result = await this.pool.query(
      `UPDATE sessions SET revoked_at = now()
       WHERE account_id = $1 AND role = $2 AND session_hash = $3 AND revoked_at IS NULL`,
      [accountId, role, hashSessionId(sessionId)],
    );
    return result.rowCount === 1;
  }

  /** Revoke every session for an account in one role. */
  public async revokeAll(roleInput: AccountRole, accountId: string): Promise<number> {
    let role: AccountRole;
    try { role = requireRole(roleInput); } catch { return 0; }
    const result = await this.pool.query(
      "UPDATE sessions SET revoked_at = now() WHERE account_id = $1 AND role = $2 AND revoked_at IS NULL",
      [accountId, role],
    );
    return result.rowCount ?? 0;
  }

  /** Permanently remove one candidate account and its API-owned private graph in one transaction. */
  public async deleteCandidateAccount(accountId: string, purge?: (context: PurgeChatContext) => Promise<void>): Promise<boolean> {
    const client = await this.pool.connect();
    let committed = false;
    try {
      await client.query("BEGIN");
      const row = await client.query<{ id: string; email: string; role: AccountRole }>(
        "SELECT id,email,role FROM accounts WHERE id=$1 FOR UPDATE", [accountId],
      );
      const account = row.rows[0];
      if (!account || account.role !== "candidate") {
        await client.query("ROLLBACK");
        committed = true;
        return false;
      }
      await this.purgeAccountChats(client, accountId, purge);
      // Revoke first, then remove all rows whose foreign keys do not cascade.
      await client.query("UPDATE sessions SET revoked_at=now() WHERE account_id=$1 AND revoked_at IS NULL", [accountId]);
      await client.query("DELETE FROM recruitment_receipts WHERE actor_id=$1", [accountId]);
      await client.query("DELETE FROM recruitment_processes WHERE candidate_id=$1 OR owner_id=$1", [accountId]);
      await client.query("DELETE FROM auth_challenges WHERE email=$1", [account.email]);
      await client.query("DELETE FROM auth_issue_events WHERE email=$1", [account.email]);
      const deleted = await client.query("DELETE FROM accounts WHERE id=$1 AND role='candidate'", [accountId]);
      await client.query("COMMIT");
      committed = true;
      return deleted.rowCount === 1;
    } catch (error) {
      if (!committed) {
        try { await client.query("ROLLBACK"); } catch { /* preserve the original error */ }
      }
      throw error;
    } finally {
      client.release();
    }
  }

  /** Delete employer-owned API records atomically after service projections are fenced. */
  public async deleteEmployerAccount(accountId: string, purge?: (context: PurgeChatContext) => Promise<void>): Promise<boolean> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const selected = await client.query<{ email: string }>(
        "SELECT email FROM accounts WHERE id=$1 AND role='employer' FOR UPDATE", [accountId]);
      if (!selected.rows[0]) { await client.query("ROLLBACK"); return false; }
      await this.purgeAccountChats(client, accountId, purge);
      await client.query("UPDATE sessions SET revoked_at=now() WHERE account_id=$1 AND revoked_at IS NULL", [accountId]);
      await client.query("DELETE FROM recruitment_receipts WHERE actor_id=$1", [accountId]);
      await client.query("DELETE FROM recruitment_processes WHERE owner_id=$1", [accountId]);
      await client.query("DELETE FROM vacancy_lifecycle_commands WHERE owner_id=$1", [accountId]);
      await client.query("DELETE FROM employer_expenses WHERE owner_id=$1", [accountId]);
      await client.query("DELETE FROM employer_expense_ai_days WHERE owner_id=$1", [accountId]);
      // Challenges are role-scoped; preserve a pending candidate login for this email.
      await client.query("DELETE FROM auth_challenges WHERE email=$1 AND role='employer'", [selected.rows[0].email]);
      await client.query("DELETE FROM auth_issue_events WHERE email=$1", [selected.rows[0].email]);
      const result = await client.query("DELETE FROM accounts WHERE id=$1 AND role='employer'", [accountId]);
      await client.query("COMMIT");
      return result.rowCount === 1;
    } catch (error) { await rollback(client); throw error; }
    finally { client.release(); }
  }

  /** Hold process locks through external purge; account locks prevent concurrent process creation. */
  private async purgeAccountChats(client: PoolClient, accountId: string,
    purge?: (context: PurgeChatContext) => Promise<void>): Promise<void> {
    const processes = await client.query<PurgeChatContext & QueryResultRow>(
      `SELECT id AS process_id,owner_id,candidate_id FROM recruitment_processes
       WHERE owner_id=$1 OR candidate_id=$1 ORDER BY id FOR UPDATE`, [accountId]);
    if (processes.rows.length && !purge) throw new Error("chat_deletion_unavailable");
    if (purge) for (const process of processes.rows) await purge(process);
  }

  /** Claim one valid mail job with a 120-second lease and recover stale workers. */
  public async claimMail(): Promise<MailJob | null> {
    await this.purgeAuthExpired();
    for (let skipped = 0; skipped < AUTH_LIMITS.mailMaxAttempts; skipped += 1) {
      const client = await this.pool.connect();
      let committed = false;
      try {
        await client.query("BEGIN");
        await client.query(
          `UPDATE auth_mail_outbox SET status = 'pending', lease_token = NULL, leased_until = NULL
           WHERE status = 'processing' AND leased_until <= now()`,
        );
        const selected = await client.query<OutboxRow>(
          `SELECT o.id, o.challenge_id, o.email, c.role, c.expires_at,
                  o.encrypted_payload, o.nonce, o.attempts, o.lease_token, o.leased_until
           FROM auth_mail_outbox o JOIN auth_challenges c ON c.id = o.challenge_id
           WHERE o.status = 'pending' AND o.available_at <= now() AND o.attempts < $1
           ORDER BY o.created_at FOR UPDATE OF o SKIP LOCKED LIMIT 1`, [AUTH_LIMITS.mailMaxAttempts],
        );
        const row = selected.rows[0];
        if (!row) {
          await client.query("COMMIT");
          committed = true;
          return null;
        }
        const valid = await client.query<{ id: string }>(
          `SELECT id FROM auth_challenges WHERE id = $1 AND status = 'active'
             AND attempts < $2 AND expires_at > now()`, [row.challenge_id, AUTH_LIMITS.maxAttempts],
        );
        if (!valid.rows[0] || row.encrypted_payload === null || row.nonce === null) {
          await this.expireOutbox(client, row.id, "challenge_invalid");
          await client.query("COMMIT");
          committed = true;
          continue;
        }
        const leaseToken = createSessionId();
        const leasedUntil = new Date(Date.now() + AUTH_LIMITS.mailLeaseMs);
        await client.query(
          `UPDATE auth_mail_outbox SET status = 'processing', attempts = attempts + 1,
             lease_token = $2, leased_until = $3 WHERE id = $1`, [row.id, leaseToken, leasedUntil],
        );
        let payload: { email: string; code: string; challengeId: string };
        try {
          payload = decryptOtpPayload(this.secret, { ciphertext: row.encrypted_payload, nonce: row.nonce });
        } catch {
          await this.expireOutbox(client, row.id, "payload_invalid");
          await client.query("COMMIT");
          committed = true;
          continue;
        }
        if (payload.challengeId !== row.challenge_id || payload.email !== row.email) {
          await this.expireOutbox(client, row.id, "payload_mismatch");
          await client.query("COMMIT");
          committed = true;
          continue;
        }
        await client.query("COMMIT");
        committed = true;
        return {
          job_id: row.id,
          challenge_id: row.challenge_id,
          email: payload.email,
          code: payload.code,
          role: row.role,
          expires_at: timestamp(row.expires_at),
          lease_token: leaseToken,
          leased_until: leasedUntil.toISOString(),
          attempts: row.attempts + 1,
        };
      } catch (error) {
        if (!committed) await rollback(client);
        throw error;
      } finally {
        client.release();
      }
    }
    return null;
  }

  /** Purge stale encrypted payloads and terminalize expired challenges for a worker sweep. */
  public async purgeAuthExpired(): Promise<number> {
    const client = await this.pool.connect();
    let committed = false;
    try {
      await client.query("BEGIN");
      const challenges = await client.query(
        `UPDATE auth_challenges SET status = 'expired', code_hmac = NULL, invalidated_at = now()
         WHERE status = 'active' AND (expires_at <= now() OR attempts >= $1)`, [AUTH_LIMITS.maxAttempts],
      );
      // Match verify/issue lock order: challenge first, then its outbox rows.
      const outbox = await client.query(
        `UPDATE auth_mail_outbox o SET
           status = CASE WHEN c.status <> 'active' OR c.expires_at <= now() THEN 'expired' ELSE 'failed' END,
           encrypted_payload = NULL, nonce = NULL, lease_token = NULL, leased_until = NULL,
           last_error = CASE WHEN c.status <> 'active' OR c.expires_at <= now() THEN 'challenge_expired' ELSE 'max_attempts_exceeded' END
         FROM auth_challenges c
         WHERE c.id = o.challenge_id AND o.status IN ('pending', 'processing')
           AND (c.status <> 'active' OR c.expires_at <= now() OR
             (o.attempts >= $1 AND (o.status='pending' OR o.leased_until <= now())))`, [AUTH_LIMITS.mailMaxAttempts],
      );
      await client.query("DELETE FROM auth_issue_events WHERE created_at < now()-interval '1 day'");
      await client.query("DELETE FROM auth_verify_events WHERE created_at < now()-interval '1 day'");
      await client.query("DELETE FROM sessions WHERE expires_at < now()-interval '30 days'");
      await client.query("DELETE FROM auth_challenges WHERE expires_at < now()-interval '1 day'");
      await client.query("COMMIT");
      committed = true;
      return (outbox.rowCount ?? 0) + (challenges.rowCount ?? 0);
    } catch (error) {
      if (!committed) await rollback(client);
      throw error;
    } finally {
      client.release();
    }
  }

  /** Guard a claimed job immediately before SMTP to prevent stale challenge delivery. */
  public async isMailJobValid(job: Pick<MailJob, "job_id" | "challenge_id" | "lease_token">): Promise<boolean> {
    const result = await this.pool.query<{ id: string }>(
      `SELECT o.id FROM auth_mail_outbox o JOIN auth_challenges c ON c.id = o.challenge_id
       WHERE o.id = $1 AND o.challenge_id = $2 AND o.lease_token = $3 AND o.status = 'processing'
         AND o.leased_until > now() AND c.status = 'active' AND c.attempts < $4 AND c.expires_at > now()`,
      [job.job_id, job.challenge_id, job.lease_token, AUTH_LIMITS.maxAttempts],
    );
    return result.rows.length === 1;
  }

  /** Acknowledge an SMTP delivery and erase the encrypted OTP payload. */
  public async finishMail(jobId: string, leaseToken: string): Promise<void> {
    const result = await this.pool.query(
      `UPDATE auth_mail_outbox SET status = 'delivered', encrypted_payload = NULL, nonce = NULL,
         lease_token = NULL, leased_until = NULL, delivered_at = now()
       WHERE id = $1 AND lease_token = $2 AND status = 'processing' AND leased_until > now()`, [jobId, leaseToken],
    );
    void result;
  }

  /** Retry a failed SMTP delivery under the same lease, erasing exhausted payloads. */
  public async failMail(jobId: string, leaseToken: string, errorCode: string, retryable = true): Promise<void> {
    const safeCode = errorCode.slice(0, 200).replace(/[^a-zA-Z0-9_.:-]/g, "_");
    const result = await this.pool.query<OutboxStatusRow>(
      `UPDATE auth_mail_outbox SET
         status = CASE WHEN (attempts >= $3 OR NOT $5) THEN 'failed' ELSE 'pending' END,
         encrypted_payload = CASE WHEN (attempts >= $3 OR NOT $5) THEN NULL ELSE encrypted_payload END,
         nonce = CASE WHEN (attempts >= $3 OR NOT $5) THEN NULL ELSE nonce END,
         lease_token = NULL, leased_until = NULL, last_error = $4,
         available_at = now() + CASE WHEN (attempts >= $3 OR NOT $5) THEN interval '0 seconds'
                                     ELSE make_interval(secs => LEAST(300, 2 ^ LEAST(attempts, 8))) END
       WHERE id = $1 AND lease_token = $2 AND status = 'processing' AND leased_until > now()
       RETURNING status`, [jobId, leaseToken, AUTH_LIMITS.mailMaxAttempts, safeCode, retryable],
    );
    void result;
  }

  /** Assert the lease and challenge are still valid immediately before SMTP I/O. */
  public async assertMailLease(jobId: string, leaseToken: string): Promise<void> {
    const valid = await this.pool.query<{ id: string }>(
      `SELECT o.id FROM auth_mail_outbox o JOIN auth_challenges c ON c.id = o.challenge_id
       WHERE o.id = $1 AND o.lease_token = $2 AND o.status = 'processing' AND o.leased_until > now()
         AND c.status = 'active' AND c.attempts < $3 AND c.expires_at > now()`,
      [jobId, leaseToken, AUTH_LIMITS.maxAttempts],
    );
    if (valid.rows.length !== 1) throw new AuthError(409, "mail_lease_invalid");
  }

  /** Read durable delivery state without exposing encrypted mail contents. */
  public async deliveryStatus(roleInput: AccountRole, emailInput: string, challengeId: string): Promise<string | null> {
    let role: AccountRole;
    let email: string;
    try { role = requireRole(roleInput); email = requireEmail(normalizeEmail(emailInput)); } catch { return null; }
    const result = await this.pool.query<OutboxStatusRow>(
      `SELECT o.status FROM auth_mail_outbox o JOIN auth_challenges c ON c.id = o.challenge_id
       WHERE o.challenge_id = $1 AND o.email = $2 AND c.role = $3`, [challengeId, email, role],
    );
    return result.rows[0]?.status ?? null;
  }

  private async expireChallenge(client: PoolClient, challengeId: string, status: "consumed" | "expired" | "exhausted"): Promise<void> {
    await client.query(
      `UPDATE auth_challenges SET status = $2, code_hmac = NULL,
         consumed_at = CASE WHEN $2 = 'consumed' THEN now() ELSE consumed_at END,
         invalidated_at = CASE WHEN $2 <> 'consumed' THEN now() ELSE invalidated_at END
       WHERE id = $1`, [challengeId, status],
    );    await client.query(
      `UPDATE auth_mail_outbox SET status = 'expired', encrypted_payload = NULL, nonce = NULL,
         lease_token = NULL, leased_until = NULL
       WHERE challenge_id = $1 AND status IN ('pending', 'processing')`, [challengeId],
    );

  }

  private async expireOutbox(client: PoolClient, jobId: string, reason: string): Promise<void> {
    await client.query(
      `UPDATE auth_mail_outbox SET status = 'expired', encrypted_payload = NULL, nonce = NULL,
         lease_token = NULL, leased_until = NULL, last_error = $2 WHERE id = $1`, [jobId, reason],
    );
  }
}
