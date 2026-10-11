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
  /** Safe provider facts shown with the anonymous card; no account identity or authorship proof. */
  external_evidence?: Array<{
    kind: "fsp" | "repository";
    subject: string;
    status: ClaimStatus;
    verified_value: import("./candidate-pipeline.js").JsonValue | null;
  }>;
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
  /** Bounded activity/evidence promotion, kept separate from model score contributions. */
  promotion?: { policy_version: string; mode: "off" | "shadow" | "active"; fsp_bonus: number; weekly_bonus: number; combined_bonus: number; max_base_gap: number; demo_registry: boolean } | null;
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
export interface FeedCard extends Omit<RerankCard, "kind" | "mode"> {
  /** A manually acknowledged suitability mismatch never claims strict eligibility. */
  strict_filter_warnings?: string[];
  mismatch_token?: string | null;
  /** Manual showcase exposures do not impersonate a trained reranker result. */
  kind: "recommended" | "reserve" | "showcase";
  mode: "learned" | "main_fallback" | "manual";
  exposure_id: string;
  session_id: string;
  served_at: string | null;
  decision: "like" | "dislike" | null;
  history_badges: string[];
  change_assessment: Record<string, unknown> | null;
  /** Live vacancy-local preference model identity; omitted by legacy baseline payloads. */
  preference_model_hash?: string | null;
  /** Preference control revision used while serving this card. */
  preference_revision?: number;
  /** Position after live preference ordering, or null when baseline ordering is active. */
  preference_position?: number | null;
  /** Live preference score, when a personalized model was applied. */
  preference_score?: number | null;
  /** Additive live preference adjustment to the baseline score. */
  preference_adjustment?: number;
  /** Machine-readable preference feature contributions. */
  preference_contributions?: Record<string, number>;
  /** Whether this card uses baseline or personalized ordering. */
  preference_state?: "baseline" | "personalized";
  /** Ordering revision frozen when this card was delivered. */
  order_revision?: number;
  /** Durable comparison identity; null for ordinary feed cards. */
  hypothesis_pair_id?: string | null;
  /** Vacancy-local hypothesis family tested by this comparison. */
  hypothesis_id?: HypothesisId | null;
  /** Assigned side of the comparison, independent of delivery order. */
  hypothesis_role?: "test" | "control" | null;
  /** Comparison policy identity frozen when the pair was selected. */
  hypothesis_policy_hash?: string | null;
  /** Fixed contrast direction and policy thresholds; excludes participant numeric facts. */
  hypothesis_parameters?: Record<string, unknown>;
  /** Conditional pair selection probability at this budget opportunity. */
  selection_probability?: number | null;
  /** Conditional randomized pair order probability. */
  order_probability?: number | null;
  /** These probabilities describe this bounded opportunity, not the full pool. */
  probability_scope?: "conditional_pair_and_order_at_budget_opportunity" | null;
  /** Whether bounded exploration added this card to the ordinary portion. */
  exploration_added?: boolean;
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
  /** Durable tail preparation, distinct from an exhausted candidate pool. */
  refill_state?: "active" | "queued" | "running" | "exhausted" | "failed";
  refill_job_id?: string | null;
  pool_change_fraction?: number;
  /** Monotonic session order revision for the session's current card order. */
  order_revision: number;
}

/** Owner-scoped result of requesting another bounded feed batch. */
export interface FeedRefillStatus {
  job_id: string; session_id: string; vacancy_id: string;
  reason: "low_water" | "pool_change" | "low_water_pool_change";
  state: "queued" | "running" | "ready" | "retrying" | "failed" | "stale";
  attempt_count: number; batch_id: string | null; error_code: string | null;
  created_at: string; completed_at: string | null;
}

/** Current vacancy-local preference control and its durable fitting job state. */
export interface PreferenceStatus {
  vacancy_id: string;
  revision: number;
  enabled: boolean;
  state: string;
  active_model_hash: string | null;
  eligible_reactions: number;
  fresh_reactions: number;
  job_id: string | null;
  job_state: string | null;
  error_code: string | null;
  policy_hash: string;
  selection_bias: "feedback_unknown_global_propensity" | "deterministic_feedback_unknown_propensity";
}

/** Employer-authorized, revision-checked preference control mutation. */
export interface PreferenceCommand {
  idempotency_key: string;
  expected_revision: number;
}

/** Fixed hypothesis families; families require observed comparable profile evidence. */
export type HypothesisId = "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "H7" | "H8" | "H9" | "H10";

/** Vacancy-local comparison evidence; only explicit opposing reactions are decisive. */
export interface HypothesisSummary {
  hypothesis_id: HypothesisId;
  status: "not_testable" | "unclear" | "supported" | "not_supported";
  supporting: number;
  opposing: number;
  decisive: number;
  total: number;
  observed_decisive: number;
  post_batch_decisive: number;
  batch_status: "collecting" | "frozen" | "invalidated";
  confidence_interval: [number, number];
  minimum_decisive_pairs: number;
  familywise_alpha: number;
  multiple_testing_families: number;
  policy_hash: string;
  calibration_status: "uncalibrated_baseline";
  /** Explicit reason when observed profile evidence cannot test a family. */
  disabled_reason?: string | null;
}

/** Employer-owned exploration control and bounded comparison report for one vacancy. */
export interface HypothesisStatus {
  vacancy_id: string;
  revision: number;
  enabled: boolean;
  context_hash: string | null;
  policy_hash: string;
  hypotheses: HypothesisSummary[];
  search_report: Record<string, unknown>;
  pairs: number;
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
export interface FeedListPage<T> { items: T[]; limit: number; offset: number; has_more: boolean; next_cursor?: string | null; }
