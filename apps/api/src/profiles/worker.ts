/** Durable source-profile projection worker; provider calls are lease-fenced and bounded. */
import { pathToFileURL } from "node:url";
import { Pool } from "pg";
import { CandidateProfileStore, type ProfileProjectionJob } from "./store.js";

/** Injectable HTTP transport used by unit tests; production defaults to fetch. */
export type ProfileProjectionTransport = typeof fetch;

/** Safe provider failure category persisted in the outbox, without private details. */
export type ProfileProjectionErrorCode =
  | "provider_timeout" | "provider_unavailable" | "provider_rejected" | "provider_invalid_ack" | "lease_lost";

/** Runtime provider and retry configuration; token values are never logged. */
export interface ProfileWorkerOptions {
  token: string;
  engineUrl: string;
  transport?: ProfileProjectionTransport;
  leaseSeconds?: number;
  maxAttempts?: number;
}

/** Narrow worker boundary so tests can exercise provider failures without PostgreSQL. */
export interface ProfileWorkerStore {
  claimProjection(leaseSeconds?: number, maxAttempts?: number): Promise<ProfileProjectionJob | null>;
  assertProjectionLease(jobId: string, leaseToken: string): Promise<void>;
  acknowledgeProjection(jobId: string, leaseToken: string, candidateId: string, sourceRevision: number, projectedRevision: number): Promise<boolean>;
  failProjection(jobId: string, leaseToken: string, code: string, retryable: boolean, maxAttempts?: number): Promise<boolean>;
}

function failureCode(error: unknown): { code: ProfileProjectionErrorCode; retryable: boolean } {
  if (error instanceof ProfileProviderError) return { code: error.code, retryable: error.retryable };
  if (error instanceof Error && error.message === "projection lease is no longer valid") {
    return { code: "lease_lost", retryable: false };
  }
  if (error instanceof DOMException && error.name === "TimeoutError") return { code: "provider_timeout", retryable: true };
  if (error instanceof Error && error.name === "AbortError") return { code: "provider_timeout", retryable: true };
  return { code: "provider_unavailable", retryable: true };
}

class ProfileProviderError extends Error {
  public constructor(public readonly code: ProfileProjectionErrorCode, public readonly retryable: boolean) {
    super(code);
    this.name = "ProfileProviderError";
  }
}

function ack(value: unknown): { candidate_id: string; profile_revision: number } {
  if (typeof value !== "object" || value === null) throw new ProfileProviderError("provider_invalid_ack", false);
  const candidateId = (value as { candidate_id?: unknown }).candidate_id;
  const revision = (value as { profile_revision?: unknown }).profile_revision;
  if (typeof candidateId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidateId)
    || typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 1) {
    throw new ProfileProviderError("provider_invalid_ack", false);
  }
  return { candidate_id: candidateId, profile_revision: revision };
}

/** One durable projection pass; matching receives exactly the persisted payload. */
export class ProfileWorker {
  private readonly transport: ProfileProjectionTransport;
  private readonly leaseSeconds: number;
  private readonly maxAttempts: number;

  public constructor(private readonly store: ProfileWorkerStore, private readonly options: ProfileWorkerOptions) {
    if (!options.token) throw new Error("CANDIDATE_PIPELINE_TOKEN is required");
    if (!options.engineUrl) throw new Error("MATCHING_ENGINE_URL is required");
    this.transport = options.transport ?? fetch;
    this.leaseSeconds = options.leaseSeconds ?? 60;
    this.maxAttempts = options.maxAttempts ?? 5;
    if (!Number.isInteger(this.leaseSeconds) || this.leaseSeconds < 1) throw new RangeError("leaseSeconds must be positive");
    if (!Number.isInteger(this.maxAttempts) || this.maxAttempts < 1 || this.maxAttempts > 5) throw new RangeError("maxAttempts must be 1..5");
  }

  /** Claim, deliver and fenced-acknowledge one job; false means the queue was empty. */
  public async processOnce(): Promise<boolean> {
    const job = await this.store.claimProjection(this.leaseSeconds, this.maxAttempts);
    if (!job) return false;
    try {
      await this.store.assertProjectionLease(job.job_id, job.lease_token);
      const endpoint = new URL(`/internal/candidates/${job.candidate_id}/profile`, this.options.engineUrl);
      const response = await this.transport(endpoint, {
        method: "PUT",
        headers: { "x-internal-token": this.options.token, "content-type": "application/json" },
        body: JSON.stringify(job.payload),
        signal: AbortSignal.timeout(10_000),
        redirect: "error",
      });
      if (!response.ok) {
        const retryable = response.status === 408 || response.status === 425 || response.status === 429 || response.status >= 500;
        throw new ProfileProviderError(retryable ? "provider_unavailable" : "provider_rejected", retryable);
      }
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        throw new ProfileProviderError("provider_invalid_ack", false);
      }
      const result = ack(body);
      if (result.candidate_id !== job.candidate_id) throw new ProfileProviderError("provider_invalid_ack", false);
      await this.store.acknowledgeProjection(job.job_id, job.lease_token, job.candidate_id, job.source_revision, result.profile_revision);
    } catch (error) {
      const failure = failureCode(error);
      try {
        await this.store.failProjection(job.job_id, job.lease_token, failure.code, failure.retryable, this.maxAttempts);
      } catch {
        // Lease recovery belongs to the newer worker; never overwrite its state.
      }
    }
    return true;
  }
}

/** Run the bounded polling loop until shutdown or one explicit pass. */
export async function runProfileWorker(worker: ProfileWorker, options: { once?: boolean; pollMs?: number; stop?: AbortSignal } = {}): Promise<void> {
  const pollMs = options.pollMs ?? 2_000;
  if (!Number.isInteger(pollMs) || pollMs < 100 || pollMs > 60_000) throw new RangeError("pollMs must be 100..60000");
  if (options.once) {
    await worker.processOnce();
    return;
  }
  while (!options.stop?.aborted) {
    if (!(await worker.processOnce())) await wait(pollMs, options.stop);
  }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Start the production CLI with only environment-backed provider credentials. */
export async function main(argv = process.argv.slice(2), env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const databaseUrl = env.DATABASE_URL?.trim();
  const token = env.CANDIDATE_PIPELINE_TOKEN?.trim();
  const engineUrl = env.MATCHING_ENGINE_URL?.trim();
  if (!databaseUrl || !token || !engineUrl) throw new Error("profile worker is not configured");
  const pool = new Pool({ connectionString: databaseUrl });
  const stop = new AbortController();
  const shutdown = () => stop.abort();
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
  try {
    const store = new CandidateProfileStore(pool);
    const reconciled = await store.reconcileTrustedSkillResults(100);
    if (argv.includes("--backfill-only")) console.info(JSON.stringify({ event: "testing_coordinate_backfill", ...reconciled }));
    if (argv.includes("--backfill-only")) return;
    const worker = new ProfileWorker(store, { token, engineUrl });
    await runProfileWorker(worker, { once: argv.includes("--once"), stop: stop.signal });
  } finally {
    process.removeListener("SIGINT", shutdown);
    process.removeListener("SIGTERM", shutdown);
    await pool.end();
  }
}

/** Factory-shaped export for service wiring and tests that prefer a named worker. */
export function exportProfileWorker(store: CandidateProfileStore, options: ProfileWorkerOptions): ProfileWorker {
  return new ProfileWorker(store, options);
}

export { ProfileWorker as ProfileProjectionWorker };

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
