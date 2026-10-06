CREATE SCHEMA IF NOT EXISTS feed_pipeline;

ALTER TABLE matching_pipeline.selection_states DROP CONSTRAINT selection_states_state_check;
ALTER TABLE matching_pipeline.selection_states ADD CONSTRAINT selection_states_state_check
    CHECK (state IN ('eligible','visited','visited_updated','pending','attractive'));

CREATE TABLE feed_pipeline.sessions (
    session_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    vacancy_revision bigint NOT NULL,
    schema_version integer NOT NULL,
    schema_hash text NOT NULL,
    pool_generation bigint NOT NULL,
    main_run_id uuid NOT NULL REFERENCES matching_pipeline.runs(run_id),
    rerank_run_id uuid NOT NULL REFERENCES rerank_pipeline.runs(run_id),
    main_policy_hash text NOT NULL,
    policy_hash text NOT NULL,
    feature_schema_hash text NOT NULL,
    tail_policy_hash text NOT NULL,
    artifact_hash text,
    include_reserve boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at),
    UNIQUE (session_id, owner_id, vacancy_id)
);
CREATE INDEX feed_sessions_owner_vacancy ON feed_pipeline.sessions(owner_id, vacancy_id, created_at DESC);

CREATE TABLE feed_pipeline.exposures (
    exposure_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES feed_pipeline.sessions(session_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL,
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    profile_revision bigint NOT NULL,
    position integer NOT NULL CHECK (position > 0),
    kind text NOT NULL CHECK (kind IN ('recommended','reserve')),
    card jsonb NOT NULL,
    served_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, candidate_id),
    UNIQUE (session_id, position),
    UNIQUE (exposure_id, owner_id, vacancy_id)
);
CREATE INDEX feed_exposures_session_position ON feed_pipeline.exposures(session_id, position);

CREATE TABLE feed_pipeline.decisions (
    receipt_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    exposure_id uuid NOT NULL UNIQUE REFERENCES feed_pipeline.exposures(exposure_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    idempotency_key uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('like','dislike')),
    profile_revision bigint NOT NULL,
    baseline jsonb NOT NULL,
    baseline_origin text NOT NULL CHECK (baseline_origin = 'exposure'),
    frozen_policy jsonb NOT NULL,
    frozen_policy_hash text NOT NULL,
    history_badges jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, vacancy_id, idempotency_key)
);
CREATE INDEX feed_decisions_owner_vacancy ON feed_pipeline.decisions(owner_id, vacancy_id, created_at DESC);

CREATE TABLE feed_pipeline.saved (
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    profile_revision bigint NOT NULL,
    exposure_id uuid NOT NULL REFERENCES feed_pipeline.exposures(exposure_id),
    card jsonb NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, vacancy_id, candidate_id)
);

CREATE TABLE feed_pipeline.visited (
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    profile_revision bigint NOT NULL,
    exposure_id uuid REFERENCES feed_pipeline.exposures(exposure_id),
    baseline_origin text NOT NULL CHECK (baseline_origin IN ('exposure','legacy_migration')),
    baseline jsonb NOT NULL,
    frozen_policy jsonb NOT NULL DEFAULT '{}',
    evaluated_revision bigint NOT NULL,
    last_assessment jsonb NOT NULL DEFAULT '{}',
    history_badges jsonb NOT NULL DEFAULT '[]',
    visited_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, vacancy_id, candidate_id)
);
CREATE INDEX feed_visited_candidate_revision ON feed_pipeline.visited(candidate_id, profile_revision);

CREATE TABLE feed_pipeline.visited_history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    baseline_revision bigint NOT NULL,
    evaluated_revision bigint NOT NULL,
    baseline jsonb NOT NULL,
    frozen_policy jsonb NOT NULL,
    assessment jsonb NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('dislike','legacy_migration','visited_updated')),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feed_visited_history_lookup ON feed_pipeline.visited_history(owner_id, vacancy_id, candidate_id, history_id);
