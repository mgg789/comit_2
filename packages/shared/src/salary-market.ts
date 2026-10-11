import type { Grade, Specialization } from "./index.js";

/** Filters for an aggregate over active, published Comit vacancy ranges. */
export interface SalaryMarketQuery {
  specialization: Specialization;
  grade: Grade;
  stacks: string[];
}

/** Average vacancy-range midpoint; small categories expose the count but no salary value. */
export interface SalaryMarketAggregate {
  specialization: Specialization;
  grade: Grade;
  stacks: string[];
  sample_count: number;
  minimum_sample_count: 3;
  average_salary_rub: number | null;
}
