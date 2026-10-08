-- Durable tail refill for immutable employer feed sessions.
-- The migration deliberately adds a new namespace to keep migration 013 (ANN)
-- and the already deployed feed/session identities unchanged.

ALTER TABLE feed_pipeline.sessions
    ADD COLUMN IF NOT EXISTS pool_snapshot_version text NOT NULL DEFAULT 'pool-n1-v1',
    ADD COLUMN IF NOT EXISTS pool_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS pool_snapshot_hash text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS refill_state text NOT NULL DEFAULT 'active'
        CHECK (refill_state IN ('active','queued','running','exhausted','failed')),
    ADD COLUMN IF NOT EXISTS exhausted_at timestamptz;

CREATE TABLE IF NOT EXISTS feed_pipeline.batches (
    batch_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES feed_pipeline.sessions(session_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    batch_number integer NOT NULL CHECK (batch_number > 0),
    reason text NOT NULL CHECK (reason IN ('initial','low_water','pool_change','low_water_pool_change')),
    main_run_id uuid REFERENCES matching_pipeline.runs(run_id),
    rerank_run_id uuid REFERENCES rerank_pipeline.runs(run_id),
    pool_generation bigint NOT NULL,
    pool_snapshot_version text NOT NULL,
    pool_snapshot jsonb NOT NULL,
    pool_snapshot_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(session_id, batch_number),
    UNIQUE(batch_id, owner_id, vacancy_id)
);
CREATE INDEX IF NOT EXISTS feed_batches_session_order ON feed_pipeline.batches(session_id, batch_number);

ALTER TABLE feed_pipeline.exposures
    ADD COLUMN IF NOT EXISTS batch_id uuid,
    ADD COLUMN IF NOT EXISTS source_main_run_id uuid,
    ADD COLUMN IF NOT EXISTS source_rerank_run_id uuid,
    ADD COLUMN IF NOT EXISTS source_pool_generation bigint;
ALTER TABLE feed_pipeline.exposures
    DROP CONSTRAINT IF EXISTS exposures_batch_id_fkey;
ALTER TABLE feed_pipeline.exposures
    ADD CONSTRAINT exposures_batch_id_fkey FOREIGN KEY(batch_id) REFERENCES feed_pipeline.batches(batch_id);
CREATE INDEX IF NOT EXISTS feed_exposures_batch ON feed_pipeline.exposures(batch_id, position);

CREATE TABLE IF NOT EXISTS feed_pipeline.refill_jobs (
    job_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES feed_pipeline.sessions(session_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    reason text NOT NULL CHECK (reason IN ('low_water','pool_change','low_water_pool_change')),
    requested_pool_snapshot_version text NOT NULL,
    requested_pool_snapshot jsonb NOT NULL,
    requested_pool_snapshot_hash text NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','ready','retrying','failed','stale')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_token uuid,
    lease_until timestamptz,
    error_code text,
    batch_id uuid REFERENCES feed_pipeline.batches(batch_id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS feed_one_refill_pending
    ON feed_pipeline.refill_jobs(session_id) WHERE state IN ('queued','running','retrying');
CREATE INDEX IF NOT EXISTS feed_refill_claim ON feed_pipeline.refill_jobs(state, available_at, created_at);
