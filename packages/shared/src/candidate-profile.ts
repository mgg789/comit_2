import type { ClaimStatus, JsonValue } from "./candidate-pipeline.js";
import type { Grade, Specialization } from "./index.js";

/** A declared coordinate. Verification fields are never accepted from the candidate. */
export interface CandidateDeclaredSkill { key: string; level: number }

/** Structured narrative facts are declarations, independently of their surrounding prose. */
export interface CandidateDeclaredFacts {
  domains?: string[];
  stacks?: string[];
  duration_months?: number | null;
  role?: "developer" | "lead" | "architect" | "analyst" | "designer" | "researcher" | "qa" | "devops" | "other" | null;
  production_support?: boolean | null;
}

/** Stable IDs identify claims across edits; a link alone never confirms a claim. */
export interface CandidateDeclaredNarrative {
  id?: string;
  title: string;
  description?: string;
  link?: string | null;
  organization?: string | null;
  facts?: CandidateDeclaredFacts | null;
}

/** Editable profile fields. Omission preserves a field in PATCH; arrays replace whole groups. */
export interface CandidateProfileFields {
  display_name?: string;
  phone?: string | null;
  city?: string;
  work_types?: ("onsite" | "remote" | "hybrid")[] | null;
  relocation_ready?: boolean | null;
  relocation_cities?: string[];
  salary?: { from_rub: number; to_rub: number } | null;
  primary_direction?: Specialization | null;
  declared_grade?: Grade | null;
  domains?: CandidateDeclaredSkill[];
  stacks?: CandidateDeclaredSkill[];
  soft_skills?: CandidateDeclaredSkill[];
  description?: string;
  projects?: CandidateDeclaredNarrative[];
  experience?: CandidateDeclaredNarrative[];
  achievements?: CandidateDeclaredNarrative[];
  preferences?: string[];
  /** Public FSP participant id, kept separate from account identity. */
  fsp_id?: string | null;
  fsp_claim_id?: string | null;
}

/** Explicit profile consent, separate from registration consent; timestamps are server-owned. */
export interface CandidateProfileConsentInput {
  processing: boolean;
  pool: boolean;
  version: string;
  /** Optional, never inferred from processing/pool; omitted in a consent write means false. */
  training?: boolean;
  /** Required only for an explicit training opt-in. */
  training_version?: "training-v1";
}

/** Compare-and-swap profile mutation; revision zero creates the first profile. */
export interface CandidateProfileMutation {
  expected_revision: number;
  fields: CandidateProfileFields;
  consent?: CandidateProfileConsentInput;
}

/** Proofs are a separate read-only channel; unchanged input hashes can preserve future verification. */
export interface CandidateClaimEvidence {
  input_hash: string;
  status: ClaimStatus;
  verified_value: JsonValue | null;
  provenance: string[];
  checked_at: string | null;
}

/** Server-produced external evidence; candidates can read but never write this channel. */
export interface CandidateExternalEvidence extends CandidateClaimEvidence {
  kind: "fsp" | "repository" | "hh";
  subject: string;
}

/** Candidate-owned request for an external provider check. The account comes from the session. */
export interface CandidateVerificationRequest {
  expected_revision: number;
  fsp_id?: string;
  claimed_rating?: number | null;
  fsp_claim_id?: string;
  demo_fixture_id?: string;
  origin?: "mock" | "live_unbound";
  repository_url?: string;
  contribution_narrative?: string;
  hh_resume_id?: string;
  claim_kind?: "projects" | "experience" | "achievements";
  claim_id?: string;
}

/** Encrypted upload metadata. A document remains pending until an independent verifier confirms it. */
export interface CandidateVerificationDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "pending";
  uploaded_at: string;
  claim_kind: "projects" | "experience" | "achievements";
  claim_id: string;
}

export interface CandidateVerificationDocumentUpload {
  filename: string;
  content_type: string;
  content_base64: string;
  claim_kind: "projects" | "experience" | "achievements";
  claim_id: string;
}

/** Durable delivery describes intake acknowledgement, not completion of vectors or the PDF. */
export interface CandidateProjectionDelivery {
  state: "queued" | "processing" | "retrying" | "failed" | "delivered";
  source_revision: number;
  projected_revision: number;
  attempts: number;
  error_code: string | null;
}

/** Private account-owned source profile; matching owns its derived pool projection. */
export interface CandidateProfileView {
  candidate_id: string;
  revision: number;
  fields: CandidateProfileFields;
  evidence: Record<string, CandidateClaimEvidence>;
  consents: Omit<CandidateProfileConsentInput, "training_version"> & {
    accepted_at: string | null;
    training: boolean;
    training_version: "training-v1" | null;
    training_accepted_at: string | null;
  };
  projection: CandidateProjectionDelivery;
  updated_at: string;
}
