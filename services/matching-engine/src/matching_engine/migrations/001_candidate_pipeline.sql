CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS candidate_pipeline;

CREATE TABLE candidate_pipeline.strict_schemas (
    version integer PRIMARY KEY CHECK (version > 0),
    schema_hash text UNIQUE NOT NULL,
    coordinates jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE candidate_pipeline.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_schema_version integer REFERENCES candidate_pipeline.strict_schemas(version) NOT NULL
);
CREATE TABLE candidate_pipeline.candidates (
    candidate_id uuid PRIMARY KEY,
    revision bigint NOT NULL CHECK (revision > 0),
    source_revision bigint NOT NULL CHECK (source_revision > 0),
    profile_hash text NOT NULL,
    profile jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('draft','queued','processing','retrying','failed','ready')),
    missing_requirements jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE candidate_pipeline.jobs (
    id bigserial PRIMARY KEY,
    candidate_id uuid REFERENCES candidate_pipeline.candidates(candidate_id) NOT NULL,
    revision bigint NOT NULL,
    schema_version integer REFERENCES candidate_pipeline.strict_schemas(version) NOT NULL,
    state text NOT NULL CHECK (state IN ('queued','processing','retrying','failed','completed','obsolete')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, revision, schema_version)
);
CREATE INDEX candidate_jobs_poll ON candidate_pipeline.jobs(state, available_at, lease_until);
CREATE TABLE candidate_pipeline.pool (
    candidate_id uuid PRIMARY KEY REFERENCES candidate_pipeline.candidates(candidate_id),
    revision bigint NOT NULL,
    schema_version integer REFERENCES candidate_pipeline.strict_schemas(version) NOT NULL,
    schema_hash text NOT NULL,
    specialization text NOT NULL,
    grade text NOT NULL,
    grade_rank integer NOT NULL,
    confirmed_strength integer NOT NULL,
    strict_vector double precision[] NOT NULL,
    strict_evidence jsonb NOT NULL,
    semantic_vector vector(640) NOT NULL,
    semantic_model_version text NOT NULL,
    public_profile jsonb NOT NULL,
    public_pdf bytea NOT NULL CHECK (octet_length(public_pdf) > 0),
    owner_pdf bytea NOT NULL CHECK (octet_length(owner_pdf) > 0),
    published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE candidate_pipeline.embedding_cache (
    cache_key text PRIMARY KEY,
    model_version text NOT NULL,
    semantic_vector vector(640) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE candidate_pipeline.outbox (
    id bigserial PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type IN ('candidate.ready','candidate.withdrawn')),
    candidate_id uuid REFERENCES candidate_pipeline.candidates(candidate_id) NOT NULL,
    revision bigint NOT NULL,
    schema_version integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);
CREATE INDEX candidate_outbox_pending ON candidate_pipeline.outbox(id) WHERE delivered_at IS NULL;
