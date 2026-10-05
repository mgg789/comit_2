/** Matching-owned projection of already collected candidate data; snake_case is the wire format. */
export type ClaimStatus = "confirmed" | "partial" | "self_reported" | "pending" | "rejected";
/** Immutable strict coordinate families; new keys are appended through a registry migration. */
export type CoordinateGroup = "domain" | "stack" | "soft_skill";
/** Recursive JSON data retained from upstream without interpreting opaque metadata. */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
/** Ordinal level and verification state are independent inputs. */
export interface PipelineSkill {
  key: string;
  level: number;
  status: ClaimStatus;
  provenance?: string[];
}
/** Typed fact with evidence independent of its parent narrative; provenance remains private. */
export interface PipelineNarrativeValue<T> {
  value: T;
  status: ClaimStatus;
  provenance?: string[];
}
/** Domain/stack reference validated against the active strict registry. */
export interface PipelineNarrativeReference {
  key: string;
  status: ClaimStatus;
  provenance?: string[];
}
/** Explicit input facts; durations are integer months in 0..1200, never extracted from prose. */
export interface PipelineNarrativeFacts {
  domains?: PipelineNarrativeReference[];
  stacks?: PipelineNarrativeReference[];
  duration_months?: PipelineNarrativeValue<number> | null;
  role?: PipelineNarrativeValue<"developer" | "lead" | "architect" | "analyst" | "designer" | "researcher" | "qa" | "devops" | "other"> | null;
  production_support?: PipelineNarrativeValue<boolean> | null;
}
/** Narrative claims preserve evidence details in private storage. */
export interface PipelineNarrative {
  title: string;
  description?: string;
  status: ClaimStatus;
  facts?: PipelineNarrativeFacts | null;
  details?: Record<string, JsonValue>;
  provenance?: string[];
}
/** Trusted upstream profile snapshot, with a monotonically increasing source revision. */
export interface CandidateIntake {
  source_revision: number;
  display_name?: string;
  email?: string | null;
  email_verified?: boolean;
  phone?: string | null;
  city?: string;
  work_types?: ("onsite" | "remote" | "hybrid")[] | null;
  relocation_ready?: boolean | null;
  relocation_cities?: string[];
  salary?: { from_rub: number; to_rub: number } | null;
  primary_direction?: import("./index.js").Specialization | null;
  grade_result?: {
    grade: import("./index.js").Grade;
    status: "approved" | "pending" | "rejected" | "self_reported";
    test_session_id?: string | null;
    approved_at?: string | null;
  } | null;
  consents?: { processing?: boolean; pool?: boolean; accepted_at?: string | null; version?: string | null };
  domains?: PipelineSkill[];
  stacks?: PipelineSkill[];
  soft_skills?: PipelineSkill[];
  description?: string;
  projects?: PipelineNarrative[];
  experience?: PipelineNarrative[];
  achievements?: PipelineNarrative[];
  preferences?: string[];
  metadata?: Record<string, JsonValue>;
}
/** Progress is safe to expose after the API has checked candidate ownership. */
export interface CandidatePipelineStatus {
  candidate_id: string;
  profile_revision: number;
  state: "draft" | "queued" | "processing" | "retrying" | "failed" | "ready";
  missing_requirements: string[];
  category: import("./index.js").Category | null;
  strict_schema_version: number | null;
  attempt_count: number;
  error_code: string | null;
}
/** Coordinate evidence allows the combine to handle confidence independently of the level. */
export interface StrictCoordinateEvidence {
  index: number;
  group: CoordinateGroup;
  key: string;
  status: ClaimStatus | null;
  level: number;
}
/** Ready, anonymous candidate projection; vectors with different schemas must never be compared. */
export interface CandidatePoolEntry {
  candidate_id: string;
  profile_revision: number;
  display_name: string;
  city: string;
  salary: { from_rub: number; to_rub: number };
  category: import("./index.js").Category;
  rank: number;
  confirmed_strength: number;
  strict_vector: number[];
  strict_evidence: StrictCoordinateEvidence[];
  strict_schema_version: number;
  strict_schema_hash: string;
  semantic_vector: number[];
  semantic_model_version: string;
  semantic_space_id: string;
  published_at: string;
  resume_url: string;
}
