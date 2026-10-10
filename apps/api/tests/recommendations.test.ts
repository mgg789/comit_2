/** Weekly privacy and provider boundaries tested without any paid model request. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { CandidateRecommendation, RecommendationEvidence } from "@comit/shared";
import { aggregateFeedback, fallbackText } from "../src/recommendations/evidence.js";
import { weekStart, type RecommendationJob } from "../src/recommendations/store.js";
import { matchingEvidence, WeeklyRecommendationWorker, type RecommendationWorkerStore } from "../src/recommendations/worker.js";

test("feedback suppresses sparse categories, deduplicates process and requires three distinct employers", () => {
  const base = { categories: ["experience"], feedback: "Private name, email and feedback must stay local" };
  assert.deepEqual(aggregateFeedback([{ ...base, process_id: "1", owner_id: "a" }, { ...base, process_id: "2", owner_id: "a" }, { ...base, process_id: "3", owner_id: "b" }]), []);
  const rows = ["a", "b", "c"].map((owner_id, index) => ({ ...base, owner_id, process_id: String(index) }));
  assert.deepEqual(aggregateFeedback([...rows, ...rows]), [{ source: "employer_feedback", category: "experience", count: 3 }]);
  assert.deepEqual(aggregateFeedback(rows.map(row => ({ ...row, categories: [], feedback: "Достаточно опыта, хорошие технические навыки" }))), []);
  assert.deepEqual(aggregateFeedback(rows.map(row => ({ ...row, categories: [], feedback: "Недостаточно опыта" }))), [{ source: "employer_feedback", category: "experience", count: 3 }]);
});
test("matching count threshold rejects sparse or arbitrary prose; UTC weeks cross year boundaries", () => {
  assert.equal(weekStart(new Date("2026-01-01T02:00:00+03:00")), "2025-12-29");
  assert.throws(() => matchingEvidence({ evidence: [{ source: "matching", category: "salary_disjoint", count: 3 }] }));
  assert.throws(() => matchingEvidence({ evidence: [{ source: "matching", category: "Private employer text", count: 4 }] }));
  assert.throws(() => matchingEvidence({ evidence: [{ source: "matching", category: "salary_disjoint", count: 4, private: "secret" }] }));
  assert.match(fallbackText([{ source: "matching", category: "system_fallback", count: 4 }], true), /не связано с недостатками/);
  assert.match(fallbackText([]), /недостаточно независимых/);
});

const job: RecommendationJob = { id: "job", candidate_id: "candidate", week_start: "2026-10-05", source_revision: "1", lease_token: "lease", attempts: 1 };
class Store implements RecommendationWorkerStore {
  public checks = 0; public revokeAt = Infinity; public published: CandidateRecommendation["state"] | null = null; public failures = 0; public discarded = 0;
  public feedbackRows: RecommendationEvidence[] = [];
  public currentJob = job; public publishResult = true;
  public async enqueueCurrentWeek(): Promise<number> { return 0; }
  public async claim(): Promise<RecommendationJob> { return this.currentJob; }
  public async valid(): Promise<boolean> { return ++this.checks < this.revokeAt; }
  public async feedback(): Promise<RecommendationEvidence[]> { return this.feedbackRows; }
  public async publish(_job: RecommendationJob, state: CandidateRecommendation["state"]): Promise<boolean> { this.published = state; return this.publishResult; }
  public async fail(): Promise<void> { this.failures++; }
  public async discard(): Promise<void> { this.discarded++; }
}
const options = { matchingUrl: "http://matching.test", matchingToken: "synthetic", agentsUrl: "http://agents.test", agentsToken: "synthetic" };
test("insufficient observations publish explicit state without a DeepSeek call", async () => {
  const store = new Store(); let calls = 0;
  await new WeeklyRecommendationWorker(store, { ...options, transport: async () => { calls++; return Response.json({ evidence: [] }); } }).processOnce();
  assert.equal(calls, 1); assert.equal(store.published, "insufficient_data");
});
test("provider receives counts only and exact previous UTC week; response publishes ready", async () => {
  const store = new Store(); store.feedbackRows = [{ source: "employer_feedback", category: "experience", count: 3 }]; let calls = 0;
  await new WeeklyRecommendationWorker(store, { ...options, transport: async (url, init) => {
    calls++; if (calls === 1) { assert.match(String(url), /after=2026-09-28T00/); return Response.json({ evidence: [] }); }
    assert.deepEqual(JSON.parse(String(init?.body)), { evidence: store.feedbackRows });
    assert.ok(!String(init?.body).includes("candidate"));
    return Response.json({ text: "В отзывах повторяется тема опыта. Уточните свой вклад в проектах.", referenced_categories: ["experience"] });
  } }).processOnce();
  assert.equal(calls, 2); assert.equal(store.published, "ready");
});
test("revocation before synthesis discards claimed work and never calls the paid provider", async () => {
  const store = new Store(); store.revokeAt = 3; store.feedbackRows = [{ source: "employer_feedback", category: "experience", count: 3 }]; let calls = 0;
  await new WeeklyRecommendationWorker(store, { ...options, transport: async () => { calls++; return Response.json({ evidence: [] }); } }).processOnce();
  assert.equal(calls, 1); assert.equal(store.discarded, 1); assert.equal(store.published, null);
});
test("provider failures retry; exhausted expired lease publishes fallback without a fourth call", async () => {
  const store = new Store();
  await new WeeklyRecommendationWorker(store, { ...options, transport: async () => Response.json({}, { status: 503 }) }).processOnce();
  assert.equal(store.failures, 1);
  store.currentJob = { ...job, attempts: 3, terminal_recovery: true };
  await new WeeklyRecommendationWorker(store, { ...options, transport: async () => { throw new Error("must not call"); } }).processOnce();
  assert.equal(store.published, "provider_unavailable");
});


test("terminal recovery publication race discards invalid source snapshot immediately", async () => {
  const store = new Store(); store.currentJob = { ...job, attempts: 3, terminal_recovery: true }; store.publishResult = false;
  await new WeeklyRecommendationWorker(store, { ...options, transport: async () => { throw new Error("must not call"); } }).processOnce();
  assert.equal(store.discarded, 1);
});


test("offline matching guidance uses plain language without internal selection terms", () => {
  for (const category of ["main_top_k_omission", "unranked_by_reranker", "reranker_reserve", "system_fallback", "strict_schema_mismatch"]) {
    assert.doesNotMatch(fallbackText([{source:"matching",category,count:4}],true), /top[- ]?k|rerank|реранк|комбайн|pipeline|агрегат|строгий фильтр|[a-z]+_[a-z]+/i);
  }
});
