/** Contact-free employer discovery contracts, mirrored from matching-engine showcase models. */
import type { Category, Grade, Specialization } from "./index.js";
import type { ClaimStatus } from "./candidate-pipeline.js";
import type { VacancyWorkType } from "./vacancy-pipeline.js";
import type { FeedDecision } from "./matching.js";

/** All specified coordinates are required; omitted evidence states include unconfirmed claims. */
export interface SkillFilter { key: string; min_level?: number; statuses?: ClaimStatus[]; }
/** Independent conjunctive coordinate groups. */
export interface SkillGroups { domains?: SkillFilter[]; stacks?: SkillFilter[]; soft_skills?: SkillFilter[]; }
/** Inclusive salary overlap, explicit working conditions and public classification filters. */
export interface SearchFilters {
  classes?: Specialization[]; grades?: Grade[]; cities?: string[];
  salary?: { from_rub: number; to_rub: number } | null;
  work_types?: VacancyWorkType[]; relocation_ready?: boolean | null; groups?: SkillGroups;
}
/** Empty query browses the current ready pool; opaque cursors expire when its source changes. */
export interface SearchRequest { q?: string; filters?: SearchFilters; limit?: number; cursor?: string | null; }
/** Published coordinate without private evidence or provenance. */
export interface PublicSkill { key: string; level: number; status: ClaimStatus; }
/** Typed narrative reference without evidence strings. */
export interface PublicReference { key: string; status: ClaimStatus; }
/** Allowlisted fact value with its independently declared evidence status. */
export interface PublicScalar { value: number | boolean | "developer" | "lead" | "architect" | "analyst" | "designer" | "researcher" | "qa" | "devops" | "other"; status: ClaimStatus; }
/** Structured facts deliberately exclude arbitrary metadata. */
export interface PublicFacts { domains: PublicReference[]; stacks: PublicReference[]; duration_months: PublicScalar | null; role: PublicScalar | null; production_support: PublicScalar | null; }
/** Sanitized narrative and typed facts. */
export interface PublicNarrative { title: string; description: string; status: ClaimStatus; facts: PublicFacts | null; }
/** Versioned anonymous projection; contacts remain behind accepted invitation permissions. */
export interface PublicProfile {
  projection_version: 1; display_name: string; city: string; salary: { from_rub: number; to_rub: number };
  description: string; work_types: VacancyWorkType[] | null; relocation_ready: boolean | null;
  relocation_cities: string[]; preferences: string[]; domains: PublicSkill[]; stacks: PublicSkill[];
  soft_skills: PublicSkill[]; projects: PublicNarrative[]; experience: PublicNarrative[]; achievements: PublicNarrative[];
}
/** Reciprocal rank fusion factors; missing channels are explicit. */
export interface SearchFactors { lexical_rank: number | null; semantic_rank: number | null; rrf_score: number; }
/** Public current revision and protected PDF link, independent of any vacancy. */
export interface PublicCandidateCard {
  candidate_id: string; profile_revision: number; display_name: string; city: string;
  salary: { from_rub: number; to_rub: number }; category: Category; rank: number; confirmed_strength: number;
  resume_url: string; search_factors: SearchFactors;
}
/** Safe profile detail with no owner identity, proofs or contact payloads. */
export interface PublicCandidateDetail extends PublicCandidateCard { public_profile: PublicProfile; }
/** Search degradation is explicit; a changed pool invalidates continuation with HTTP 409. */
export interface SearchPage { items: PublicCandidateCard[]; total: number; limit: number; has_more: boolean; next_cursor: string | null; mode: "browse" | "lexical" | "hybrid"; fallback_reason: string | null; }
/** Manual like binds the selected open owned vacancy to the displayed profile revision. */
export interface ShowcaseLikeRequest { candidate_id: string; profile_revision: number; idempotency_key: string; mismatch_token?: string | null; }
/** HTTP 409 detail for a future modal; send this token to acknowledge these exact revisions. */
export interface ShowcaseMismatchWarning { code: "strict_filter_confirmation_required"; candidate_id: string; profile_revision: number; vacancy_revision: number; warnings: string[]; mismatch_token: string; }
/** Hard state conflicts remain strings; only structured suitability warnings permit override. */
export interface ShowcaseLikeConflict { detail: string | ShowcaseMismatchWarning; }
/** Manual selection shares the normal saved receipt and preserves its distinct source. */
export interface ShowcaseLikeReceipt extends FeedDecision { profile_revision: number; selection_source: "showcase"; strict_filter_warnings: string[]; mismatch_token: string | null; }
