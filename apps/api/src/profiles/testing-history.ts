/** Account-owned aggregation of accepted trusted grade results. */
import type { Pool } from "pg";
import type { CandidateTestingHistory } from "@comit/shared";

/** Use Moscow calendar days, including today; the query remains one consistent snapshot. */
export class CandidateTestingHistoryStore {
  public constructor(private readonly pool: Pool) {}

  /** Return exactly 30 daily buckets, excluding future results and all other owners. */
  public async get(candidateId: string, now = new Date()): Promise<CandidateTestingHistory> {
    const result = await this.pool.query<{ date: string; count: number }>(`
      WITH bounds AS (
        SELECT ($2::timestamptz AT TIME ZONE 'Europe/Moscow')::date AS today
      ), days AS (
        SELECT (today - step)::date AS day FROM bounds, generate_series(0, 29) AS step
      ), counts AS (
        SELECT (decided_at AT TIME ZONE 'Europe/Moscow')::date AS day, count(*)::integer AS count
        FROM candidate_grade_results, bounds
        WHERE candidate_id=$1 AND decided_at <= $2::timestamptz
          AND decided_at >= ((today - 29)::timestamp AT TIME ZONE 'Europe/Moscow')
        GROUP BY 1
      ) SELECT to_char(days.day, 'YYYY-MM-DD') AS date, coalesce(counts.count, 0)::integer AS count
        FROM days LEFT JOIN counts USING(day) ORDER BY days.day`, [candidateId, now.toISOString()]);
    const daily = result.rows;
    if (daily.length !== 30) throw new Error("testing_history_snapshot_incomplete");
    return { timezone: "Europe/Moscow", period: { from: daily[0]!.date, to: daily[29]!.date },
      kind: "confirmed_results", daily, total: daily.reduce((sum, day) => sum + day.count, 0) };
  }
}
