/** Server-side vacancy materialization; business status and technical readiness are independent. */
import type { Specialization, VacancyStatus } from "./index.js";
import type { JsonValue } from "./candidate-pipeline.js";

/** Canonical work formats; intake also accepts the user's Russian labels. */
export type VacancyWorkType = "onsite" | "remote" | "hybrid";
/** Full snapshot; integer revision and salaries must not exceed Number.MAX_SAFE_INTEGER. */
export interface VacancyIntake {
  source_revision: number;
  owner_id?: string | null;
  status?: VacancyStatus;
  zp_min?: number | null;
  zp_max?: number | null;
  daily?: string | null;
  city?: string | null;
  type?: VacancyWorkType | "очно" | "дистант" | "гибрид" | null;
  bio?: string | null;
  domens?: Record<string, number>;
  stacks?: Record<string, number>;
  soft_skills?: Record<string, number>;
  class?: Specialization | "back" | "front" | "ML" | null;
  metadata?: Record<string, JsonValue>;
}
/** Technical status never implies readiness solely from a business status of open. */
export interface VacancyPipelineStatus {
  vacancy_id: string;
  revision: number;
  source_revision: number;
  status: VacancyStatus;
  state: "draft" | "queued" | "processing" | "retrying" | "failed" | "ready";
  missing_requirements: string[];
  strict_schema_version: number | null;
  attempt_count: number;
  error_code: string | null;
}
/** Exact immutable revision ready to call the combine; omitted requirements never constrain selection. */
export interface VacancyReady {
  vacancy_id: string;
  revision: number;
  source_revision: number;
  owner_id: string;
  status: "open";
  zp_min: number;
  zp_max: number;
  daily: string;
  city: string;
  type: VacancyWorkType;
  bio: string;
  domens: Record<string, number>;
  stacks: Record<string, number>;
  soft_skills: Record<string, number>;
  metadata: Record<string, JsonValue>;
  class: Specialization | null;
  build_mode: "class_only" | "requirements_only" | "mixed";
  strict_vector: number[];
  requirement_mask: boolean[];
  strict_schema_version: number;
  strict_schema_hash: string;
  semantic_vector: number[] | null;
  semantic_model_version: string | null;
  semantic_space_id: string | null;
  semantic_available: boolean;
  published_at: string;
}
/** Bounded page of open, complete, active-schema ready revisions. */
export interface VacancyReadyPage {
  items: VacancyReady[];
  limit: number;
  offset: number;
}
