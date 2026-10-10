/** Durable weekly queue, consent fences and atomic candidate-owned publication. */
import { randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import type { CandidateRecommendation, CandidateRecommendationsResponse, RecommendationEvidence } from "@comit/shared";
import { timestamp, transaction } from "../auth/database.js";
import { aggregateFeedback, fallbackText, type FeedbackObservation } from "./evidence.js";

/** Leased job has no raw profile or employer prose. */
export interface RecommendationJob { id: string; candidate_id: string; week_start: string; source_revision: string; lease_token: string; attempts: number; terminal_recovery?: boolean; }
/** Optional notification append runs in the same transaction as publication. */
export interface RecommendationStoreOptions { notifyInTransaction?: (client: PoolClient, candidateId: string, item: CandidateRecommendation) => Promise<void>; }
/** UTC Monday prevents deployment timezone changes from creating duplicate weekly jobs. */
export function weekStart(now = new Date()): string {
  const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return date.toISOString().slice(0, 10);
}
type Published = { id: string; week_start: Date | string; state: CandidateRecommendation["state"]; text: string; generated_at: Date | string; evidence: RecommendationEvidence[] };
function item(row: Published): CandidateRecommendation {
  return { recommendation_id: row.id, week_start: timestamp(row.week_start).slice(0, 10), state: row.state, text: row.text,
    generated_at: timestamp(row.generated_at), evidence: row.evidence };
}

/** PostgreSQL source of truth for unique candidate/week jobs and revocation-safe results. */
export class CandidateRecommendationsStore {
  public constructor(private readonly pool: Pool, private readonly options: RecommendationStoreOptions = {}) {}

  /** Enqueue all consented profiles once per UTC week; remove records beyond 90-day retention. */
  public async enqueueCurrentWeek(now = new Date()): Promise<number> {
    return transaction(this.pool, async client => {
      await client.query("DELETE FROM candidate_recommendations WHERE week_start < ($1::date-interval '90 days')", [weekStart(now)]);
      const rows = await client.query<{ candidate_id: string; revision: string }>("SELECT candidate_id,revision FROM candidate_profiles WHERE processing_consent AND pool_consent AND training_consent AND training_consent_version='training-v2' FOR SHARE");
      let queued = 0;
      for (const row of rows.rows) queued += (await client.query(`INSERT INTO candidate_recommendations(id,candidate_id,week_start,source_revision)
        VALUES($1,$2,$3,$4) ON CONFLICT(candidate_id,week_start) DO NOTHING`, [randomUUID(), row.candidate_id, weekStart(now), row.revision])).rowCount ?? 0;
      return queued;
    });
  }

  /** Latest terminal result is visible only while all required source consents remain current. */
  public async latest(candidateId: string): Promise<CandidateRecommendationsResponse> {
    return transaction(this.pool, async client => {
      const consent = await client.query("SELECT 1 FROM candidate_profiles WHERE candidate_id=$1 AND processing_consent AND pool_consent AND training_consent AND training_consent_version='training-v2' FOR SHARE", [candidateId]);
      if (!consent.rowCount) { await client.query("DELETE FROM candidate_recommendations WHERE candidate_id=$1", [candidateId]); return { item: null }; }
      const rows = await client.query<Published>(`SELECT id,week_start::text AS week_start,state,text,generated_at,evidence FROM candidate_recommendations
        WHERE candidate_id=$1 AND state IN ('ready','insufficient_data','provider_unavailable') ORDER BY week_start DESC LIMIT 1`, [candidateId]);
      return { item: rows.rows[0] ? item(rows.rows[0]) : null };
    });
  }

  /** Recover expired leases, bound attempts to three and claim one eligible job. */
  public async claim(): Promise<RecommendationJob | null> {
    return transaction(this.pool, async client => {
      const row = (await client.query<RecommendationJob>(`SELECT id,candidate_id,week_start::text,source_revision::text,attempts FROM candidate_recommendations
        WHERE ((state IN ('queued','retrying') AND available_at<=now()) OR (state='processing' AND lease_until<=now())) AND (attempts<3 OR state='processing')
        ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1`)).rows[0];
      if (!row) return null;
      const lease = randomUUID();
      await client.query("UPDATE candidate_recommendations SET state='processing',attempts=least(attempts+1,3),lease_token=$2,lease_until=now()+interval '300 seconds' WHERE id=$1", [row.id, lease]);
      return { ...row, attempts: Math.min(row.attempts + 1, 3), lease_token: lease, terminal_recovery: row.attempts >= 3 };
    });
  }

  /** Recheck owner consent, source revision and the still-live lease before every external call. */
  public async valid(job: RecommendationJob): Promise<boolean> {
    const result = await this.pool.query(`SELECT 1 FROM candidate_recommendations r JOIN candidate_profiles p ON p.candidate_id=r.candidate_id
      WHERE r.id=$1 AND r.candidate_id=$2 AND r.state='processing' AND r.lease_token=$3 AND r.lease_until>now()
        AND p.revision=$4 AND p.processing_consent AND p.pool_consent AND p.training_consent AND p.training_consent_version='training-v2'`, [job.id, job.candidate_id, job.lease_token, job.source_revision]);
    return Boolean(result.rowCount);
  }

  /** Extract only employer-authored rejected closures and failed completed interviews from the previous week. */
  public async feedback(job: RecommendationJob): Promise<RecommendationEvidence[]> {
    const rows = await this.pool.query<FeedbackObservation>(`SELECT p.id::text AS process_id,p.owner_id::text AS owner_id,p.closure->'categories' AS categories,p.closure->>'reason' AS feedback
      FROM recruitment_processes p WHERE p.candidate_id=$1 AND p.state='rejected' AND p.closure->>'actor'='employer'
        AND (p.closure->>'at')::timestamptz>=($2::date::timestamp AT TIME ZONE 'UTC')-interval '7 days' AND (p.closure->>'at')::timestamptz<($2::date::timestamp AT TIME ZONE 'UTC')
      UNION ALL SELECT p.id::text,p.owner_id::text,to_jsonb(i)->'reason_categories',i.feedback
      FROM recruitment_interviews i JOIN recruitment_processes p ON p.id=i.process_id
      WHERE p.candidate_id=$1 AND i.state='completed' AND i.outcome='failed'
        AND i.completed_at>=($2::date::timestamp AT TIME ZONE 'UTC')-interval '7 days' AND i.completed_at<($2::date::timestamp AT TIME ZONE 'UTC')`, [job.candidate_id, job.week_start]);
    return aggregateFeedback(rows.rows);
  }

  /** Lease-fenced publication and notification commit together after locking canonical consent. */
  public async publish(job: RecommendationJob, state: CandidateRecommendation["state"], text: string, evidence: RecommendationEvidence[]): Promise<boolean> {
    return transaction(this.pool, async client => {
      // Source first matches profile updates and withdrawal trigger lock order.
      const consent = await client.query("SELECT 1 FROM candidate_profiles WHERE candidate_id=$1 AND revision=$2 AND processing_consent AND pool_consent AND training_consent AND training_consent_version='training-v2' FOR SHARE", [job.candidate_id, job.source_revision]);
      if (!consent.rowCount) return false;
      const result = await client.query<Published>(`UPDATE candidate_recommendations SET state=$3,text=$4,evidence=$5::jsonb,generated_at=now(),lease_token=NULL,lease_until=NULL
        WHERE id=$1 AND candidate_id=$6 AND state='processing' AND lease_token=$2 AND lease_until>now() RETURNING id,week_start::text AS week_start,state,text,generated_at,evidence`,
        [job.id, job.lease_token, state, text, JSON.stringify(evidence), job.candidate_id]);
      if (!result.rows[0]) return false;
      await this.options.notifyInTransaction?.(client, job.candidate_id, item(result.rows[0]));
      return true;
    });
  }

  /** Bounded exponential retry; final fallback is published through the same consent/notification fence. */
  public async fail(job: RecommendationJob, evidence: RecommendationEvidence[]): Promise<void> {
    if (job.attempts >= 3) { if (!await this.publish(job, "provider_unavailable", fallbackText(evidence, true), evidence)) await this.discard(job); return; }
    await this.pool.query(`UPDATE candidate_recommendations SET state='retrying',available_at=now()+(30*power(2,attempts-1))*interval '1 second',lease_token=NULL,lease_until=NULL
      WHERE id=$1 AND state='processing' AND lease_token=$2 AND lease_until>now()`, [job.id, job.lease_token]);
  }

  /** Cancel a snapshot invalidated by profile revision; the scheduler may queue the current one. */
  public async discard(job: RecommendationJob): Promise<void> {
    await this.pool.query(`DELETE FROM candidate_recommendations r WHERE r.id=$1 AND r.state='processing' AND r.lease_token=$2
      AND NOT EXISTS(SELECT 1 FROM candidate_profiles p WHERE p.candidate_id=r.candidate_id AND p.revision=$3
        AND p.processing_consent AND p.pool_consent AND p.training_consent AND p.training_consent_version='training-v2')`, [job.id, job.lease_token, job.source_revision]);
  }
}
