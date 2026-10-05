/**
 * Domain types shared across all Comit services and apps.
 * Mirrors context/30-domain-model.md — keep both in sync in the same commit.
 * Salary range is mandatory in vacancies AND invitations (TZ rule 2.2.1).
 */

export type {
  CandidateIntake, CandidatePipelineStatus, CandidatePoolEntry, ClaimStatus,
  CoordinateGroup, JsonValue, PipelineNarrative, PipelineSkill, StrictCoordinateEvidence,
} from "./candidate-pipeline.js";
export type {
  VacancyIntake, VacancyPipelineStatus, VacancyReady, VacancyReadyPage, VacancyWorkType,
} from "./vacancy-pipeline.js";

export type Role = "candidate" | "employer";

export const GRADES = ["intern", "junior", "middle", "senior"] as const;
export type Grade = (typeof GRADES)[number];

export const SPECIALIZATIONS = [
  "backend",
  "frontend",
  "fullstack",
  "mobile",
  "devops",
  "data",
  "ml",
  "qa",
] as const;
export type Specialization = (typeof SPECIALIZATIONS)[number];

/** Category = specialization × grade, assigned only via survey + grade test (TZ §2). */
export interface Category {
  specialization: Specialization;
  grade: Grade;
}

export interface SalaryRange {
  /** Lower bound in RUB, inclusive. */
  fromRub: number;
  /** Upper bound in RUB, inclusive; must be >= fromRub. */
  toRub: number;
}

/** Vacancy lifecycle (context/30-domain-model.md). */
export type VacancyStatus =
  | "draft"
  | "open"
  | "pending"
  | "closed"
  | "done";

/** Candidate position inside one vacancy's selection funnel. */
export type SelectionStage =
  | "in_pool"
  | "selected"
  | "visited"
  | "attractive"
  | "invited"
  | "hired"
  | "rejected";

/** Invitation/application statuses visible to BOTH sides (TZ rule 2.2.3). */
export type InvitationStatus = "sent" | "viewed" | "accepted" | "declined";

export interface Invitation {
  id: string;
  /** May be null: TZ allows inviting without a published vacancy. */
  vacancyId: string | null;
  companyName: string;
  offerDescription: string;
  salary: SalaryRange;
  contactMethod: string;
  status: InvitationStatus;
}

/** FSP (Federacija sportivnogo programmirovanija) achievements block. Absence is a normal state. */
export interface FspProfile {
  fspId: string;
  /** III → I, KMS, MS. */
  rank: string | null;
  rating: number | null;
  events: Array<{ title: string; year: number; place: number | null }>;
  certificates: string[];
}

export interface CandidateProfile {
  id: string;
  displayName: string;
  categories: Category[];
  ratingPoints: number;
  fsp: FspProfile | null;
}
