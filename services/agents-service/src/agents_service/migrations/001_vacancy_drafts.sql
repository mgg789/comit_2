CREATE SCHEMA IF NOT EXISTS agents_service;

CREATE TABLE agents_service.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents_service.drafts (
    draft_id uuid PRIMARY KEY,
    vacancy_id uuid NOT NULL UNIQUE,
    owner_id uuid NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    state text NOT NULL DEFAULT 'editing'
        CHECK (state IN ('editing', 'summary', 'creating', 'created', 'failed')),
    fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    field_sources jsonb NOT NULL DEFAULT '{}'::jsonb,
    missing_requirements jsonb NOT NULL DEFAULT '[]'::jsonb,
    summary jsonb,
    publication jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agents_service_drafts_owner ON agents_service.drafts(owner_id, updated_at DESC);

CREATE TABLE agents_service.jobs (
    job_id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('publication', 'enrichment', 'agent_turn')),
    owner_id uuid NOT NULL,
    draft_id uuid REFERENCES agents_service.drafts(draft_id),
    session_id uuid,
    idempotency_key text,
    body_hash text NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'processing', 'retrying', 'failed', 'succeeded', 'obsolete')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, kind, idempotency_key)
);
CREATE INDEX agents_service_jobs_poll
    ON agents_service.jobs(state, available_at, lease_until);
CREATE INDEX agents_service_jobs_owner
    ON agents_service.jobs(owner_id, created_at DESC);
