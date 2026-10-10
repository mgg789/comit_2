/** Weekly generation uses only thresholded counts; no provider I/O happens inside SQL transactions. */
import { pathToFileURL } from "node:url";
import { Pool } from "pg";
import type { CandidateRecommendation, RecommendationEvidence } from "@comit/shared";
import { SystemNotificationsStore } from "../notifications/system-store.js";
import { CandidateRecommendationsStore, type RecommendationJob } from "./store.js";
import { fallbackText } from "./evidence.js";

/** Small store interface enables durable-worker behavior tests without a live provider. */
export interface RecommendationWorkerStore {
  enqueueCurrentWeek(now?: Date): Promise<number>;
  claim(): Promise<RecommendationJob | null>;
  valid(job: RecommendationJob): Promise<boolean>;
  feedback(job: RecommendationJob): Promise<RecommendationEvidence[]>;
  publish(job: RecommendationJob, state: CandidateRecommendation["state"], text: string, evidence: RecommendationEvidence[]): Promise<boolean>;
  fail(job: RecommendationJob, evidence: RecommendationEvidence[]): Promise<void>;
  discard(job: RecommendationJob): Promise<void>;
}
/** Tokens are internal transport only and never written to records or log output. */
export interface RecommendationWorkerOptions { matchingUrl: string; matchingToken: string; agentsUrl: string; agentsToken: string; transport?: typeof fetch; }
const matchingCategories = new Set(["selection_state_excluded", "class_mismatch", "salary_disjoint", "work_type_unknown", "work_type_mismatch", "location_unknown", "relocation_unknown", "relocation_not_ready", "relocation_destination_mismatch", "strict_schema_mismatch", "main_top_k_omission", "unranked_by_reranker", "reranker_reserve", "system_fallback"]);
function object(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }

/** Validate the internal matching boundary rather than trusting untyped JSON. */
export function matchingEvidence(value: unknown): RecommendationEvidence[] {
  if (!object(value) || !Array.isArray(value.evidence) || value.evidence.length > 14) throw new Error("invalid_evidence");
  const seen = new Set<string>();
  return value.evidence.map((entry: unknown) => {
    if (!object(entry) || Object.keys(entry).some(key => !["source", "category", "count"].includes(key)) || entry.source !== "matching"
      || typeof entry.category !== "string" || !matchingCategories.has(entry.category) || seen.has(entry.category)
      || typeof entry.count !== "number" || !Number.isSafeInteger(entry.count) || entry.count < 4 || entry.count > 1000000) throw new Error("invalid_evidence");
    seen.add(entry.category); return { source: "matching", category: entry.category, count: entry.count };
  });
}

/** Restart-safe generation pass; provider outages yield retries and an explicit final fallback. */
export class WeeklyRecommendationWorker {
  private readonly transport: typeof fetch;
  public constructor(private readonly store: RecommendationWorkerStore, private readonly options: RecommendationWorkerOptions) { this.transport = options.transport ?? fetch; }

  private async call(url: string, token: string, job: RecommendationJob, payload?: unknown): Promise<unknown> {
    if (!token) throw new Error("provider_not_configured");
    const response = await this.transport(url, { method: payload === undefined ? "GET" : "POST", redirect: "error",
      signal: AbortSignal.timeout(110000), headers: { "x-internal-token": token, "x-owner-id": job.candidate_id, "content-type": "application/json" },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    if (!response.ok || Number(response.headers.get("content-length") ?? 0) > 32768) throw new Error("provider_unavailable");
    const body = await response.text(); if (body.length > 32768) throw new Error("provider_invalid_response");
    return JSON.parse(body) as unknown;
  }

  /** Process one claimed candidate/week; consent and lease are checked before each provider boundary. */
  public async processOnce(): Promise<boolean> {
    const job = await this.store.claim(); if (!job) return false;
    let evidence: RecommendationEvidence[] = [];
    try {
      if (!await this.store.valid(job)) { await this.store.discard(job); return true; }
      if (job.terminal_recovery) { if (!await this.store.publish(job, "provider_unavailable", "Генерация рекомендаций временно недоступна. Следующая попытка запланирована на следующую неделю.", [])) await this.store.discard(job); return true; }
      const feedback = await this.store.feedback(job);
      const before = `${job.week_start}T00:00:00.000Z`;
      const after = new Date(new Date(before).getTime() - 7 * 86400000).toISOString();
      if (!await this.store.valid(job)) { await this.store.discard(job); return true; }
      const url = new URL(`/internal/candidates/${encodeURIComponent(job.candidate_id)}/recommendation-evidence`, this.options.matchingUrl);
      url.searchParams.set("before", before); url.searchParams.set("after", after);
      evidence = [...feedback, ...matchingEvidence(await this.call(url.toString(), this.options.matchingToken, job))];
      if (!await this.store.valid(job)) { await this.store.discard(job); return true; }
      if (!evidence.length) { if (!await this.store.publish(job, "insufficient_data", fallbackText([]), [])) await this.store.discard(job); return true; }
      const result = await this.call(new URL("/internal/candidate-recommendations/generate", this.options.agentsUrl).toString(), this.options.agentsToken, job, { evidence });
      if (!object(result) || Object.keys(result).some(key => !["text", "referenced_categories"].includes(key))
        || typeof result.text !== "string" || result.text.length < 40 || result.text.length > 3000 || /https?:\/\/|www\.|\S+@\S+|[0-9a-f]{8}-[0-9a-f]{4}-/i.test(result.text)
        || !Array.isArray(result.referenced_categories) || !result.referenced_categories.length
        || result.referenced_categories.some((category: unknown) => typeof category !== "string" || !evidence.some(entry => entry.category === category))) throw new Error("provider_invalid_response");
      if (!await this.store.publish(job, "ready", result.text, evidence)) await this.store.discard(job);
    } catch { if (!await this.store.valid(job)) await this.store.discard(job); else await this.store.fail(job, evidence); }
    return true;
  }
}

/** Standard CLI entry resumes the weekly queue; parent may inject atomic inbox publication in-process. */
export async function runRecommendationWorker(): Promise<void> {
  if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  const notifications = new SystemNotificationsStore(pool);
  const store = new CandidateRecommendationsStore(pool, { notifyInTransaction: async (client, candidateId, item) => {
    const text = item.state === "ready" ? "Готовы новые рекомендации по итогам недели." : item.state === "insufficient_data"
      ? "Пока недостаточно независимых наблюдений для недельных рекомендаций." : "AI-рекомендации временно недоступны. В статистике доступны общие подсказки по известным причинам.";
    await notifications.append(client, candidateId, `recommendation:${item.recommendation_id}`, "recommendation.weekly", text, { kind: "candidate_profile", path: "/statistics" });
  } });
  const worker = new WeeklyRecommendationWorker(store, { matchingUrl: process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101",
    matchingToken: process.env.CANDIDATE_PIPELINE_TOKEN ?? "", agentsUrl: process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104", agentsToken: process.env.AGENTS_SERVICE_TOKEN ?? "" });
  let stop = false; process.once("SIGTERM", () => { stop = true; }); process.once("SIGINT", () => { stop = true; });
  try { while (!stop) {
    try { await store.enqueueCurrentWeek(); if (await worker.processOnce()) continue; }
    catch { console.warn("recommendation_worker_unavailable"); }
    await new Promise(resolve => setTimeout(resolve, 5000));
  } } finally { await pool.end(); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runRecommendationWorker().catch(() => { console.error("recommendation_worker_failed"); process.exitCode = 1; });
}
