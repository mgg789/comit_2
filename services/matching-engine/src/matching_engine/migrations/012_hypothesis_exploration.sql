-- Paired exploration is additive; existing main/R0 results remain immutable.
CREATE SCHEMA hypothesis_pipeline;
CREATE TABLE hypothesis_pipeline.controls (
    vacancy_id uuid PRIMARY KEY REFERENCES vacancy_pipeline.vacancies(vacancy_id),
    owner_id uuid NOT NULL,
    context_hash text NOT NULL,
    epoch bigint NOT NULL DEFAULT 1,
    revision bigint NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    budget_start timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_search_block integer NOT NULL DEFAULT -1,
    search_report jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE hypothesis_pipeline.pairs (
    pair_id uuid PRIMARY KEY,
    vacancy_id uuid NOT NULL REFERENCES hypothesis_pipeline.controls(vacancy_id),
    owner_id uuid NOT NULL,
    context_hash text NOT NULL,
    epoch bigint NOT NULL,
    session_id uuid NOT NULL REFERENCES feed_pipeline.sessions(session_id),
    hypothesis_id text NOT NULL CHECK(hypothesis_id IN ('H1','H2','H3','H4','H5','H6','H7','H8','H9','H10')),
    test_candidate uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    control_candidate uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id),
    test_exposure uuid NOT NULL REFERENCES feed_pipeline.exposures(exposure_id),
    control_exposure uuid NOT NULL REFERENCES feed_pipeline.exposures(exposure_id),
    comparison_hash text NOT NULL,
    policy_hash text NOT NULL,
    parameters jsonb,
    pair_probability double precision NOT NULL CHECK(pair_probability>0 AND pair_probability<=1),
    order_probability double precision NOT NULL DEFAULT 0.5 CHECK(order_probability=0.5),
    draw double precision NOT NULL CHECK(draw>=0 AND draw<1),
    test_first boolean NOT NULL,
    preference_model_hash text,
    invalidated_at timestamptz,
    invalidation_reason text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(test_candidate<>control_candidate),
    UNIQUE(vacancy_id,context_hash,epoch,test_candidate,control_candidate,hypothesis_id)
);
CREATE INDEX hypothesis_pairs_context ON hypothesis_pipeline.pairs(vacancy_id,context_hash,epoch,created_at);
CREATE TABLE hypothesis_pipeline.batches (
    vacancy_id uuid NOT NULL REFERENCES hypothesis_pipeline.controls(vacancy_id),
    context_hash text NOT NULL,
    epoch bigint NOT NULL,
    hypothesis_id text NOT NULL,
    pair_ids uuid[] NOT NULL,
    report jsonb,
    report_hash text NOT NULL,
    invalidated_at timestamptz,
    PRIMARY KEY(vacancy_id,context_hash,epoch,hypothesis_id)
);
CREATE TABLE hypothesis_pipeline.commands (
    vacancy_id uuid NOT NULL REFERENCES hypothesis_pipeline.controls(vacancy_id),
    idempotency_key uuid NOT NULL,
    action text NOT NULL CHECK(action IN ('pause','resume','reset')),
    revision bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(vacancy_id,idempotency_key)
);
ALTER TABLE feed_pipeline.exposures ADD COLUMN exploration_suppressed boolean NOT NULL DEFAULT false;
ALTER TABLE learning_pipeline.samples ADD COLUMN sample_scope text NOT NULL DEFAULT 'main_top_k'
    CHECK(sample_scope IN ('main_top_k','exploration'));
