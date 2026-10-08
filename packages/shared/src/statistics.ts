/** Employer-only aggregate dashboard; calendar buckets use UTC and contain no candidate identities. */
export type EmployerStatisticStage = "delivered" | "viewed" | "liked" | "disliked" | "invited" | "accepted" | "interviewed" | "offered" | "hired";

/** Inclusive UTC calendar range, at most 90 days; defaults to the last 30 days including today. */
export interface EmployerStatisticsQuery { from?: string; to?: string; vacancy_id?: string }

/** Distinct vacancy/candidate pairs per milestone, rather than repeat actions or a nested cohort funnel. */
export type EmployerStatisticsCounts = Record<EmployerStatisticStage, number>;

/** Chart-ready response: current inventory, distinct milestone pairs and raw daily event counts. */
export interface EmployerStatistics {
  version: "employer-statistics-v1";
  generated_at: string;
  timezone: "UTC";
  period: { from: string; to: string; previous_from: string; previous_to: string; vacancy_id: string | null };
  inventory: { draft: number; open: number; closed: number; archived: number; saved_pairs: number; active_processes: number };
  current: EmployerStatisticsCounts;
  previous: EmployerStatisticsCounts;
  unique_candidates: number;
  daily: Array<{ date: string; counts: EmployerStatisticsCounts }>;
  top_vacancies: Array<{ vacancy_id: string; draft_id: string; title: string; status: string; events: number; candidates: number }>;
}
