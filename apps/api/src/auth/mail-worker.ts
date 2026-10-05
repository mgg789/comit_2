/** Durable auth verification-mail worker; SMTP never runs without a valid lease. */
import { pathToFileURL } from "node:url";
import { Pool } from "pg";
import { createSmtpMailer, type Mailer, type MailDeliveryCode, type VerificationMail } from "./mail.js";
import { AuthStore } from "./store.js";

/** A leased row returned by AuthStore.claimMail(). */
export interface MailJob extends VerificationMail {
  job_id: string;
  lease_token: string;
}

/** Minimal store boundary; the concrete AuthStore owns SQL, reclaiming, retries, and encryption. */
export interface MailJobStore {
  claimMail(): Promise<MailJob | null>;
  finishMail(jobId: string, leaseToken: string): Promise<unknown>;
  failMail(jobId: string, leaseToken: string, code: MailDeliveryCode, retryable: boolean): Promise<unknown>;
  /** Optional exact-token check used immediately before entering provider I/O. */
  assertMailLease?(jobId: string, leaseToken: string): Promise<void>;
  /** Compatibility alias for AuthStore's boolean lease guard. */
  isMailJobValid?(job: Pick<MailJob, "job_id" | "challenge_id" | "lease_token">): Promise<boolean>;
}

/** Injectable one-job worker used by tests and by the long-running CLI. */
export class MailWorker {
  constructor(private readonly store: MailJobStore, private readonly mailer: Mailer) {}

  /** Claim and process one job, returning false only when the queue is empty. */
  async processOnce(): Promise<boolean> {
    const job = await this.store.claimMail();
    if (!job) return false;
    try {
      if (this.store.assertMailLease) {
        await this.store.assertMailLease(job.job_id, job.lease_token);
      } else if (this.store.isMailJobValid && !(await this.store.isMailJobValid(job))) {
        throw new Error("mail lease is no longer valid");
      }
      await this.mailer.send(job);
      await this.store.finishMail(job.job_id, job.lease_token);
    } catch (error) {
      const failure = mailFailure(error);
      try {
        await this.store.failMail(job.job_id, job.lease_token, failure.code, failure.retryable);
      } catch {
        // A reclaimed or expired lease belongs to the newer worker; never overwrite it.
      }
    }
    return true;
  }
}

function mailFailure(error: unknown): { code: MailDeliveryCode; retryable: boolean } {
  if (typeof error !== "object" || error === null) return { code: "smtp_unavailable", retryable: true };
  const code = (error as { code?: unknown }).code;
  if (code !== "smtp_unavailable" && code !== "rejected") {
    return { code: "smtp_unavailable", retryable: true };
  }
  const retryable = (error as { retryable?: unknown }).retryable;
  return { code, retryable: typeof retryable === "boolean" ? retryable : true };
}

/** Run a bounded polling loop until SIGTERM/SIGINT, without logging secrets or provider errors. */
export async function runMailWorker(store: MailJobStore, mailer: Mailer, options: {
  once?: boolean;
  pollMs?: number;
  stop?: AbortSignal;
} = {}): Promise<void> {
  const pollMs = options.pollMs ?? 2_000;
  if (!Number.isInteger(pollMs) || pollMs < 100 || pollMs > 60_000) throw new RangeError("pollMs must be 100..60000");
  if (options.once) {
    await new MailWorker(store, mailer).processOnce();
    return;
  }
  const worker = new MailWorker(store, mailer);
  while (!options.stop?.aborted) {
    if (!(await worker.processOnce())) await wait(pollMs, options.stop);
  }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Start the production CLI; AuthStore is constructed only after configuration is validated. */
export async function main(argv = process.argv.slice(2), env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const databaseUrl = env.DATABASE_URL?.trim();
  const secret = env.AUTH_SECRET?.trim() || env.JWT_SECRET?.trim();
  if (!databaseUrl || !secret) throw new Error("mail worker is not configured");
  const pool = new Pool({ connectionString: databaseUrl });
  const stop = new AbortController();
  const shutdown = () => stop.abort();
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
  try {
    await runMailWorker(new AuthStore(pool, secret), createSmtpMailer(env), { once: argv.includes("--once"), stop: stop.signal });
  } finally {
    process.removeListener("SIGINT", shutdown);
    process.removeListener("SIGTERM", shutdown);
    await pool.end();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
