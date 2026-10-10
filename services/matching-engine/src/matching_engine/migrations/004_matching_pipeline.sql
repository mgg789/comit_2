CREATE SCHEMA IF NOT EXISTS matching_pipeline;
CREATE TABLE matching_pipeline.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    pool_generation bigint NOT NULL DEFAULT 0 CHECK (pool_generation >= 0)
);
INSERT INTO matching_pipeline.settings(singleton) VALUES (true);

CREATE FUNCTION matching_pipeline.bump_pool_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(1440130172);
    UPDATE matching_pipeline.settings SET pool_generation=pool_generation+1 WHERE singleton;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_pool_generation AFTER INSERT ON candidate_pipeline.outbox
    FOR EACH ROW EXECUTE FUNCTION matching_pipeline.bump_pool_generation();

CREATE TABLE matching_pipeline.controls (
    vacancy_id uuid PRIMARY KEY REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    top_k integer NOT NULL DEFAULT 50 CHECK (top_k BETWEEN 1 AND 500),
    revision bigint NOT NULL DEFAULT 1,
    current_run_id uuid
);
CREATE TABLE matching_pipeline.runs (
    run_id uuid PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    vacancy_revision bigint NOT NULL,
    schema_version integer NOT NULL REFERENCES candidate_pipeline.strict_schemas(version),
    schema_hash text NOT NULL,
    pool_generation bigint NOT NULL,
    policy_version text NOT NULL,
    policy_hash text NOT NULL,
    policy jsonb NOT NULL,
    top_k integer NOT NULL CHECK (top_k BETWEEN 1 AND 500),
    control_revision bigint NOT NULL,
    vacancy_snapshot jsonb NOT NULL,
    candidates_snapshot jsonb NOT NULL,
    excluded_ids jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('queued','processing','retrying','failed','ready','obsolete')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    error_code text,
    scanned_count integer NOT NULL DEFAULT 0,
    passed_count integer NOT NULL DEFAULT 0,
    selected_count integer NOT NULL DEFAULT 0,
    filter_counts jsonb NOT NULL DEFAULT '{}',
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vacancy_id,vacancy_revision,schema_version,pool_generation,policy_hash,top_k,control_revision)
);
ALTER TABLE matching_pipeline.controls ADD FOREIGN KEY (current_run_id) REFERENCES matching_pipeline.runs(run_id);
CREATE INDEX matching_runs_poll ON matching_pipeline.runs(state,available_at,lease_until);
CREATE TABLE matching_pipeline.selections (
    run_id uuid NOT NULL REFERENCES matching_pipeline.runs(run_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    position integer NOT NULL CHECK (position > 0),
    card jsonb NOT NULL,
    PRIMARY KEY (run_id,candidate_id),
    UNIQUE (run_id,position)
);
CREATE TABLE matching_pipeline.selection_states (
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    state text NOT NULL CHECK (state IN ('eligible','visited','pending','attractive')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vacancy_id,candidate_id)
);
CREATE TABLE matching_pipeline.manual_requests (
    idempotency_key uuid PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    top_k integer NOT NULL,
    run_id uuid NOT NULL REFERENCES matching_pipeline.runs(run_id)
);
CREATE TABLE matching_pipeline.inbox (
    source text NOT NULL CHECK (source IN ('candidate','vacancy')),
    event_id bigint NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source,event_id)
);
CREATE TABLE matching_pipeline.delivery_errors (
    source text NOT NULL CHECK (source IN ('candidate','vacancy')),
    event_id bigint NOT NULL,
    attempts integer NOT NULL,
    retry_at timestamptz NOT NULL,
    quarantined boolean NOT NULL DEFAULT false,
    error_code text NOT NULL,
    PRIMARY KEY (source,event_id)
);

