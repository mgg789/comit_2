/** Read-only dashboard projection over the shared database; source services retain all writes. */
import type { Pool } from "pg";
import type { EmployerStatisticStage, EmployerStatistics, EmployerStatisticsCounts, EmployerStatisticsQuery } from "@comit/shared";

export const statisticStages: EmployerStatisticStage[] = ["delivered", "viewed", "liked", "disliked", "invited", "accepted", "interviewed", "offered", "hired"];
const day = 86400000;

/** Public validation/ownership error, without source payloads or SQL diagnostics. */
export class StatisticsError extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}

/** Resolve real calendar dates; PostgreSQL must never silently normalize invalid input. */
export function statisticsPeriod(query: EmployerStatisticsQuery, now = new Date()) {
  const today = now.toISOString().slice(0, 10);
  const parse = (value: string): number => {
    const result = Date.parse(`${value}T00:00:00.000Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(result) || new Date(result).toISOString().slice(0, 10) !== value) throw new StatisticsError(400, "invalid_statistics_period");
    return result;
  };
  const to = query.to ?? today;
  const end = parse(to) + day;
  const from = query.from ?? new Date(end - 30 * day).toISOString().slice(0, 10);
  const start = parse(from), days = (end - start) / day;
  if (days < 1 || days > 90 || parse(to) > parse(today)) throw new StatisticsError(400, "invalid_statistics_period");
  return { from, to, start: new Date(start).toISOString(), end: new Date(end).toISOString(),
    previous_from: new Date(start - days * day).toISOString().slice(0, 10), previous_to: new Date(start - day).toISOString().slice(0, 10), days };
}

const sql = `WITH owned AS MATERIALIZED (
  SELECT draft_id,vacancy_id,business_status,deleted_at,fields,published_fields FROM agents_service.drafts
  WHERE owner_id=$1 AND ($2::uuid IS NULL OR vacancy_id=$2)
), events AS (
  SELECT x.vacancy_id,x.candidate_id,'delivered'::text AS stage,x.served_at AS at
  FROM feed_pipeline.exposures x JOIN owned v USING(vacancy_id)
  WHERE x.owner_id=$1 AND x.served_at >= $3::timestamptz AND x.served_at < $5::timestamptz
  UNION ALL
  SELECT x.vacancy_id,x.candidate_id,'viewed',e.recorded_at
  FROM learning_pipeline.events e JOIN feed_pipeline.exposures x USING(exposure_id,owner_id,vacancy_id) JOIN owned v USING(vacancy_id)
  WHERE e.owner_id=$1 AND e.event_type='impression' AND e.recorded_at >= $3 AND e.recorded_at < $5
  UNION ALL
  SELECT d.vacancy_id,d.candidate_id,CASE d.action WHEN 'like' THEN 'liked' ELSE 'disliked' END,d.created_at
  FROM feed_pipeline.decisions d JOIN owned v USING(vacancy_id)
  WHERE d.owner_id=$1 AND d.created_at >= $3 AND d.created_at < $5
  UNION ALL
  SELECT p.vacancy_id,p.candidate_id,CASE e.type WHEN 'invitation.sent' THEN 'invited' WHEN 'accept' THEN 'accepted'
    WHEN 'interview.complete' THEN 'interviewed' WHEN 'offer.send' THEN 'offered' WHEN 'offer.accept' THEN 'hired' END,e.created_at
  FROM recruitment_events e JOIN recruitment_processes p ON p.id=e.process_id JOIN owned v USING(vacancy_id)
  WHERE p.owner_id=$1 AND e.type IN ('invitation.sent','accept','interview.complete','offer.send','offer.accept')
    AND e.created_at >= $3 AND e.created_at < $5
), totals AS (
  SELECT CASE WHEN at >= $4::timestamptz THEN 'current' ELSE 'previous' END AS period,stage,
    count(DISTINCT (vacancy_id,candidate_id)) AS count FROM events GROUP BY 1,2
), daily AS (
  SELECT to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD') AS date,stage,count(*) AS count
  FROM events WHERE at >= $4 GROUP BY 1,2
), top AS (
  SELECT v.vacancy_id,v.draft_id,left(COALESCE(v.published_fields->>'title',v.fields->>'title',v.published_fields->>'class',v.fields->>'class','Вакансия'),200) AS title,
    CASE WHEN v.deleted_at IS NOT NULL THEN 'archived' ELSE v.business_status END AS status,
    count(*) AS events,count(DISTINCT e.candidate_id) AS candidates
  FROM events e JOIN owned v USING(vacancy_id) WHERE e.at >= $4
  GROUP BY v.vacancy_id,v.draft_id,v.fields,v.published_fields,v.deleted_at,v.business_status
  ORDER BY events DESC,v.vacancy_id LIMIT 10
)
SELECT EXISTS(SELECT 1 FROM owned) AS has_vacancy,
  (SELECT COALESCE(jsonb_agg(to_jsonb(t)),'[]') FROM totals t) AS totals,
  (SELECT COALESCE(jsonb_agg(to_jsonb(d)),'[]') FROM daily d) AS daily,
  (SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.events DESC,t.vacancy_id),'[]') FROM top t) AS top,
  (SELECT count(DISTINCT candidate_id) FROM events WHERE at >= $4)::int AS unique_candidates,
  jsonb_build_object(
    'draft',(SELECT count(*) FROM owned WHERE deleted_at IS NULL AND business_status='draft'),
    'open',(SELECT count(*) FROM owned WHERE deleted_at IS NULL AND business_status='open'),
    'closed',(SELECT count(*) FROM owned WHERE deleted_at IS NULL AND business_status='closed'),
    'archived',(SELECT count(*) FROM owned WHERE deleted_at IS NOT NULL),
    'saved_pairs',(SELECT count(*) FROM feed_pipeline.saved s JOIN owned v USING(vacancy_id) WHERE s.owner_id=$1 AND v.deleted_at IS NULL),
    'active_processes',(SELECT count(*) FROM recruitment_processes p JOIN owned v USING(vacancy_id)
      WHERE p.owner_id=$1 AND v.deleted_at IS NULL AND p.state IN ('sent','viewed','accepted','offer_sent'))
  ) AS inventory`;

/** Return one statement-consistent snapshot, bounded to 90 calendar days plus the previous comparison period. */
export class EmployerStatisticsStore {
  constructor(private readonly pool: Pool, private readonly clock: () => Date = () => new Date()) {}

  /** Server-derived owner is mandatory; vacancy IDs outside that scope return the same 404 as absent IDs. */
  public async get(ownerId: string, query: EmployerStatisticsQuery): Promise<EmployerStatistics> {
    const now = this.clock(), period = statisticsPeriod(query, now);
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      await client.query("SET LOCAL statement_timeout='5000ms'");
      const result = await client.query<{
        has_vacancy: boolean; inventory: EmployerStatistics["inventory"]; unique_candidates: number;
        totals: Array<{ period: "current" | "previous"; stage: EmployerStatisticStage; count: number }>;
        daily: Array<{ date: string; stage: EmployerStatisticStage; count: number }>;
        top: EmployerStatistics["top_vacancies"];
      }>(sql, [ownerId, query.vacancy_id ?? null, `${period.previous_from}T00:00:00.000Z`, period.start, period.end]);
      const row = result.rows[0]!;
      if (query.vacancy_id && !row.has_vacancy) throw new StatisticsError(404, "statistics_vacancy_not_found");
      const expenses = await client.query<{ category: EmployerStatistics["expenses"]["categories"][number]["category"]; amount: string; events: string }>(
        `SELECT category,sum(amount_kopeks)::text AS amount,count(*) FILTER(WHERE amount_kopeks>0)::text AS events
         FROM employer_expenses WHERE owner_id=$1 AND ($2::uuid IS NULL OR vacancy_id=$2)
         AND occurred_at >= $3::timestamptz AND occurred_at < $4::timestamptz GROUP BY category`,
        [ownerId,query.vacancy_id ?? null,period.start,period.end]);
      const expenseDaily = await client.query<{ date: string; amount: string }>(
        `SELECT to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD') AS date,sum(amount_kopeks)::text AS amount
         FROM employer_expenses WHERE owner_id=$1 AND ($2::uuid IS NULL OR vacancy_id=$2)
         AND occurred_at >= $3::timestamptz AND occurred_at < $4::timestamptz GROUP BY 1`,
        [ownerId,query.vacancy_id ?? null,period.start,period.end]);
      await client.query("COMMIT");
      const empty = (): EmployerStatisticsCounts => ({ delivered: 0, viewed: 0, liked: 0, disliked: 0, invited: 0, accepted: 0, interviewed: 0, offered: 0, hired: 0 });
      const current = empty(), previous = empty();
      for (const item of row.totals) (item.period === "current" ? current : previous)[item.stage] = item.count;
      const daily = Array.from({ length: period.days }, (_, index) => ({ date: new Date(Date.parse(period.start) + index * day).toISOString().slice(0, 10), counts: empty() }));
      for (const item of row.daily) daily.find(bucket => bucket.date === item.date)!.counts[item.stage] = item.count;
      return { version: "employer-statistics-v1", generated_at: now.toISOString(), timezone: "UTC",
        period: { from: period.from, to: period.to, previous_from: period.previous_from, previous_to: period.previous_to, vacancy_id: query.vacancy_id ?? null },
        inventory: row.inventory, current, previous, unique_candidates: row.unique_candidates, daily, top_vacancies: row.top,
        expenses: { currency: "RUB", total_kopeks: expenses.rows.reduce((sum,item) => sum+Number(item.amount),0),
          categories: (["publication","invitation","ai","penalty"] as const).map(category => {
            const item=expenses.rows.find(value => value.category===category);
            return {category,amount_kopeks:Number(item?.amount ?? 0),events:Number(item?.events ?? 0)};
          }),
          daily: daily.map(item => ({date:item.date,amount_kopeks:Number(expenseDaily.rows.find(value => value.date===item.date)?.amount ?? 0)})),
          tariffs: {publication_kopeks:19900,invitation_kopeks:4900,deadline_penalty_kopeks:19900,ai_daily_allowance_kopeks:1000,ai_usd_rub:100} } };
    } catch (error) { await client.query("ROLLBACK"); throw error; }
    finally { client.release(); }
  }
}
