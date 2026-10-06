-- Vacancy-local incremental preferences are independent of the global R0 registry.
CREATE SCHEMA preference_pipeline;
CREATE TABLE preference_pipeline.controls (
    vacancy_id uuid PRIMARY KEY REFERENCES vacancy_pipeline.vacancies(vacancy_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    context_hash text NOT NULL,
    identity jsonb NOT NULL,
    epoch bigint NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    cutoff timestamptz,
    last_attempt_at timestamptz,
    active_model_hash text,
    revision bigint NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'collecting',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE preference_pipeline.jobs (
    job_id uuid PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES preference_pipeline.controls(vacancy_id) ON DELETE CASCADE,
    context_hash text NOT NULL,
    epoch bigint NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','rejected','stale','failed')),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token uuid,
    attempts integer NOT NULL DEFAULT 0,
    input_hash text,
    plan_hash text,
    plan jsonb,
    report jsonb,
    error_code text,
    completed_at timestamptz
);
CREATE UNIQUE INDEX preference_one_pending ON preference_pipeline.jobs(vacancy_id)
    WHERE state IN ('queued','running');
CREATE INDEX preference_claim ON preference_pipeline.jobs(available_at,requested_at);
CREATE TABLE preference_pipeline.models (
    model_hash text PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES preference_pipeline.controls(vacancy_id) ON DELETE CASCADE,
    context_hash text NOT NULL,
    epoch bigint NOT NULL,
    job_id uuid NOT NULL UNIQUE REFERENCES preference_pipeline.jobs(job_id) ON DELETE CASCADE,
    model jsonb,
    input_hash text NOT NULL,
    plan_hash text NOT NULL,
    gate jsonb NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((model IS NOT NULL AND revoked_at IS NULL) OR (model IS NULL AND revoked_at IS NOT NULL))
);
CREATE TABLE preference_pipeline.participants (
    job_id uuid NOT NULL REFERENCES preference_pipeline.jobs(job_id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id) ON DELETE CASCADE,
    PRIMARY KEY(job_id,candidate_id)
);
CREATE INDEX preference_participant_candidate ON preference_pipeline.participants(candidate_id);
CREATE TABLE preference_pipeline.events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES preference_pipeline.controls(vacancy_id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    action text NOT NULL,
    model_hash text,
    previous_model_hash text,
    job_id uuid REFERENCES preference_pipeline.jobs(job_id) ON DELETE SET NULL,
    reason text,
    idempotency_key uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX preference_command_key ON preference_pipeline.events(vacancy_id,idempotency_key)
    WHERE idempotency_key IS NOT NULL;
ALTER TABLE feed_pipeline.sessions ADD COLUMN preference_revision bigint NOT NULL DEFAULT -1;
ALTER TABLE feed_pipeline.sessions ADD COLUMN preference_model_hash text;
ALTER TABLE feed_pipeline.sessions ADD COLUMN order_revision bigint NOT NULL DEFAULT 0;
-- Permuting the unserved suffix uses its existing positive position slots atomically.
ALTER TABLE feed_pipeline.exposures DROP CONSTRAINT exposures_session_id_position_key;
ALTER TABLE feed_pipeline.exposures ADD CONSTRAINT exposures_session_id_position_key
    UNIQUE(session_id,position) DEFERRABLE INITIALLY IMMEDIATE;
