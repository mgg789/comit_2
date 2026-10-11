import type { Pool } from "pg";
import type { SalaryMarketAggregate, SalaryMarketQuery } from "@comit/shared";

const minimumSampleCount = 3 as const;

/** Read a bounded aggregate from currently active-schema, ready, published vacancies. */
export class SalaryMarketStore {
  public constructor(private readonly pool: Pool) {}

  /** Match an exact direction and grade plus every selected stack key; never widen sparse queries. */
  public async get(query: SalaryMarketQuery): Promise<SalaryMarketAggregate> {
    const stacks = [...new Set(query.stacks)].sort();
    const result = await this.pool.query<{ sample_count: number; average_salary_rub: string | null }>(
      `WITH eligible AS (
        SELECT v.profile
        FROM vacancy_pipeline.vacancies v
        JOIN vacancy_pipeline.ready r ON r.vacancy_id=v.vacancy_id AND r.revision=v.revision
        JOIN candidate_pipeline.settings s ON s.singleton=true AND s.active_schema_version=r.schema_version
        WHERE v.state='ready' AND v.business_status='open'
          AND v.profile->>'class'=$1
          AND v.profile->'metadata'->>'grade'=$2
          AND v.profile->>'zp_min' IS NOT NULL AND v.profile->>'zp_max' IS NOT NULL
          AND CASE WHEN cardinality($3::text[])=0 THEN true ELSE
            NOT EXISTS (SELECT 1 FROM unnest($3::text[]) AS wanted(key)
              WHERE NOT COALESCE(v.profile->'stacks' ? wanted.key, false)) END
          AND (v.profile->>'zp_min')::numeric >= 0
          AND (v.profile->>'zp_max')::numeric >= (v.profile->>'zp_min')::numeric
      )
      SELECT count(*)::int AS sample_count,
        round(avg(((profile->>'zp_min')::numeric+(profile->>'zp_max')::numeric)/2))::text AS average_salary_rub
      FROM eligible`, [query.specialization, query.grade, stacks]);
    const row = result.rows[0];
    const sampleCount = row?.sample_count ?? 0;
    const average = sampleCount >= minimumSampleCount && row?.average_salary_rub != null
      ? Number(row.average_salary_rub) : null;
    if (average !== null && (!Number.isSafeInteger(average) || average < 0)) throw new Error("salary_market_aggregate_invalid");
    return { specialization: query.specialization, grade: query.grade, stacks,
      sample_count: sampleCount, minimum_sample_count: minimumSampleCount, average_salary_rub: average };
  }
}
