CREATE SCHEMA IF NOT EXISTS vacancy_pipeline;
CREATE TABLE vacancy_pipeline.vacancies (
    vacancy_id uuid PRIMARY KEY,
    owner_id uuid,
    revision bigint NOT NULL CHECK (revision>0),
    source_revision bigint NOT NULL CHECK (source_revision>0),
    profile_hash text NOT NULL,
    profile jsonb NOT NULL,
    raw_input jsonb NOT NULL,
    business_status text NOT NULL CHECK (business_status IN ('draft','open','pending','closed','done')),
    state text NOT NULL CHECK (state IN ('draft','queued','processing','retrying','failed','ready')),
    missing_requirements jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE vacancy_pipeline.jobs (
    id bigserial PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    revision bigint NOT NULL,
    schema_version integer NOT NULL REFERENCES candidate_pipeline.strict_schemas(version),
    state text NOT NULL CHECK (state IN ('queued','processing','retrying','failed','completed','obsolete')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vacancy_id,revision,schema_version)
);
CREATE INDEX vacancy_jobs_poll ON vacancy_pipeline.jobs(state,available_at,lease_until);
CREATE TABLE vacancy_pipeline.ready (
    vacancy_id uuid PRIMARY KEY REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    revision bigint NOT NULL,
    schema_version integer NOT NULL REFERENCES candidate_pipeline.strict_schemas(version),
    schema_hash text NOT NULL,
    build_mode text NOT NULL CHECK (build_mode IN ('class_only','requirements_only','mixed')),
    strict_vector double precision[] NOT NULL,
    requirement_mask boolean[] NOT NULL,
    semantic_vector vector(640),
    semantic_model_version text,
    semantic_space_id text,
    published_at timestamptz NOT NULL DEFAULT now(),
    CHECK (cardinality(strict_vector)=cardinality(requirement_mask)),
    CHECK ((semantic_vector IS NULL AND semantic_model_version IS NULL AND semantic_space_id IS NULL)
        OR (semantic_vector IS NOT NULL AND semantic_model_version IS NOT NULL AND semantic_space_id IS NOT NULL))
);
CREATE TABLE vacancy_pipeline.outbox (
    id bigserial PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type IN ('vacancy.ready','vacancy.withdrawn')),
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    revision bigint NOT NULL,
    schema_version integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);
CREATE INDEX vacancy_outbox_pending ON vacancy_pipeline.outbox(id) WHERE delivered_at IS NULL;
