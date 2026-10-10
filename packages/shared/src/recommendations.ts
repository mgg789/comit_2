/** Canonical employer-selected reasons; free feedback remains participant-scoped. */
export const rejectionReasonCategories = ["technical_skills", "experience", "communication", "salary", "work_format", "location", "other"] as const;
/** Stable category keys for closure and interview feedback. */
export type RejectionReasonCategory = typeof rejectionReasonCategories[number];
/** Privacy-thresholded observations, never raw employer feedback or source IDs. */
export interface RecommendationEvidence { source: "employer_feedback" | "matching"; category: string; count: number; }
/** Candidate-owned weekly guidance; fallback states explicitly disclose generation limits. */
export interface CandidateRecommendation {
  recommendation_id: string;
  /** UTC Monday of the generation week; evidence covers the preceding seven days. */
  week_start: string;
  state: "ready" | "insufficient_data" | "provider_unavailable";
  text: string;
  generated_at: string;
  evidence: RecommendationEvidence[];
}
/** Latest published snapshot; null means no snapshot or no current processing/pool/training consent. */
export interface CandidateRecommendationsResponse { item: CandidateRecommendation | null; }
