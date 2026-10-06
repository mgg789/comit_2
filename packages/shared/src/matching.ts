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
  grade_status?: "approved" | "self_reported";
  grade_confidence?: number;
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
/** Trusted handoff cannot manually release visited; structural feed evaluation owns that exception. */
export interface SelectionStateInput {
  state: "eligible" | "visited" | "pending" | "attractive";
}
/** Persisted exclusion state survives replacement of disposable main batches. */
export interface SelectionState {
  state: SelectionStateInput["state"] | "visited_updated";
  vacancy_id: string;
  candidate_id: string;
}

/** Optional durable identity binds manual requests to the original immutable run. */
export interface RerankRequest { idempotency_key?: string | null; }
/** Versioned baseline top-k result; reserve never implies the full pool is exhausted. */
export interface RerankStatus {
  activation_revision?: number;
  approval_receipt_hash?: string | null;
  activation_kind?: "gated" | "manual_override" | "none";
  run_id: string;
  main_run_id: string;
  vacancy_id: string;
  artifact_hash: string | null;
  feature_schema_hash: string;
  tail_policy_hash: string;
  stage: "baseline_rerank";
  state: CombineStatus["state"];
  mode: "learned" | "main_fallback" | null;
  fallback_reason: string | null;
  attempt_count: number;
  error_code: string | null;
  selected_count: number;
  recommended_count: number;
  reserve_count: number;
  unranked_by_reranker_count: number;
  published_at: string | null;
}
/** Main factors are preserved with explicit scalar R0 score and feature contributions. */
export interface RerankCard extends CandidateCard {
  activation_revision?: number;
  approval_receipt_hash?: string | null;
  activation_kind?: "gated" | "manual_override" | "none";
  main_position: number;
  rerank_position: number | null;
  mode: "learned" | "main_fallback";
  kind: "recommended" | "reserve";
  rerank_score: number | null;
  rerank_contributions: Record<string, number>;
  artifact_hash: string | null;
}
/** Separate recommended/reserve page from a single current immutable run. */
export interface RerankPage {
  run: RerankStatus;
  kind: "recommended" | "reserve";
  items: RerankCard[];
  limit: number;
  offset: number;
}


/** Open a 24-hour portion from a current ready rerank; reserve is explicitly opt-in. */
export interface FeedRequest { include_reserve?: boolean; limit?: number; }
/** An immutable reranked card occurrence, bound to one served exposure. */
export interface FeedCard extends RerankCard {
  exposure_id: string;
  session_id: string;
  served_at: string | null;
  decision: "like" | "dislike" | null;
  history_badges: string[];
  change_assessment: Record<string, unknown> | null;
}
/** Stable opaque position cursor; own feedback does not reorder this portion. */
export interface FeedPage {
  session_id: string;
  vacancy_id: string;
  vacancy_revision: number;
  policy_hash: string;
  expires_at: string;
  cursor: string | null;
  next_cursor: string | null;
  has_more: boolean;
  items: FeedCard[];
}
/** One reaction per served exposure; UUID key is scoped to employer and vacancy. */
export interface FeedDecisionRequest { exposure_id: string; action: "like" | "dislike"; idempotency_key: string; }

/** Client visibility acknowledgement; server delivery alone is not a human view. */
export interface FeedImpressionRequest { exposure_id: string; idempotency_key: string; }
/** First visible-impression receipt with owner-scoped idempotency. */
export interface FeedImpression { event_id: string; exposure_id: string; recorded_at: string; replayed: boolean; }
/** Immutable action identity; duplicate requests return this receipt with replayed=true. */
export interface FeedDecision {
  receipt_id: string;
  exposure_id: string;
  vacancy_id: string;
  candidate_id: string;
  action: "like" | "dislike";
  idempotency_key: string;
  saved: boolean;
  visited: boolean;
  replayed: boolean;
  history_badges: string[];
}
/** Saved membership remains auditable even when its old card is no longer current. */
export interface SavedCard {
  vacancy_id: string;
  candidate_id: string;
  profile_revision: number;
  exposure_id: string;
  saved_at: string;
  card: FeedCard | null;
}
/** Durable last-dislike baseline and current blocking state for this vacancy. */
export interface VisitedCard {
  vacancy_id: string;
  candidate_id: string;
  profile_revision: number;
  exposure_id: string | null;
  baseline_origin: "exposure" | "legacy_migration";
  visited_at: string | null;
  updated_at: string;
  history_badges: string[];
  evaluated_revision: number;
  state: SelectionState["state"];
  assessment: Record<string, unknown>;
}
/** Bounded membership pagination; independent of a current main run. */
export interface FeedListPage<T> { items: T[]; limit: number; offset: number; has_more: boolean; }
