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
  expenses: EmployerExpenses;
  top_vacancies: Array<{ vacancy_id: string; draft_id: string; title: string; status: string; events: number; candidates: number }>;
}

/** Durable accounting categories; monetary values are integer RUB kopeks. */
export type EmployerExpenseCategory = "publication" | "invitation" | "ai" | "penalty";

/** Owner-scoped expense projection for the selected statistics period, without personal source payloads. */
export interface EmployerExpenses {
  currency: "RUB";
  total_kopeks: number;
  categories: Array<{ category: EmployerExpenseCategory; amount_kopeks: number; events: number }>;
  daily: Array<{ date: string; amount_kopeks: number }>;
  tariffs: { publication_kopeks: number; invitation_kopeks: number; deadline_penalty_kopeks: number; ai_daily_allowance_kopeks: number; ai_usd_rub: number };
}
