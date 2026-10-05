/** Protected main-selection contracts; ranking is provisional until the future reranker. */
import type { Category } from "./index.js";
import type { ClaimStatus } from "./candidate-pipeline.js";

/** Optional caller key makes manual retries idempotent; automatic jobs use the same queue. */
export interface CombineRequest {
  top_k?: number;
  idempotency_key?: string | null;
}
/** Snapshot identities prevent mixing vacancy, pool, strict-schema or policy generations. */
export interface CombineStatus {
  run_id: string;
  vacancy_id: string;
  vacancy_revision: number;
  strict_schema_version: number;
  strict_schema_hash: string;
  pool_generation: number;
  policy_version: string;
  policy_hash: string;
  stage: "main_selection";
  state: "queued" | "processing" | "retrying" | "failed" | "ready" | "obsolete";
  top_k: number;
  attempt_count: number;
  error_code: string | null;
  scanned_count: number;
  passed_count: number;
  selected_count: number;
  filter_counts: Record<string, number>;
  published_at: string | null;
}
/** Explicit skill target and candidate evidence, including unconfirmed contributions. */
export interface MatchedFeature {
  index: number;
  group: string;
  key: string;
  target_level: number;
  candidate_level: number;
  status: ClaimStatus | null;
  agreement: number;
  confidence: number;
  contribution: number;
}
/** Computed factors are the source of a card's explanation; no LLM selection decisions. */
export interface ScoreBreakdown {
  rating_score: number;
  strict_score: number | null;
  semantic_cosine: number | null;
  semantic_unavailable_reason: string | null;
  rating_contribution: number;
  strict_contribution: number;
  semantic_contribution: number;
  score: number;
  top_matched_features: MatchedFeature[];
}
/** Anonymous candidate card referencing only the public resume projection. */
export interface CandidateCard {
  candidate_id: string;
  profile_revision: number;
  position: number;
  display_name: string;
  city: string;
  salary: { from_rub: number; to_rub: number };
  category: Category;
  rank: number;
  confirmed_strength: number;
  resume_url: string;
  filter_factors: string[];
  explanation: ScoreBreakdown;
}
/** One current ready main batch; stale or incomplete batches return a non-success status. */
export interface SelectionPage {
  run: CombineStatus;
  items: CandidateCard[];
  limit: number;
  offset: number;
}
/** Durable delivery acknowledgement is separate from completed combine work. */
export interface DeliveryStatus {
  candidate_pending: number;
  vacancy_pending: number;
  acknowledged_count: number;
  queued_count: number;
  quarantined_count: number;
  retrying_count: number;
}
/** Server handoff from the future Tinder flow; visited may never be released. */
export interface SelectionStateInput {
  state: "eligible" | "visited" | "pending" | "attractive";
}
/** Persisted exclusion state survives replacement of disposable main batches. */
export interface SelectionState extends SelectionStateInput {
  vacancy_id: string;
  candidate_id: string;
}
