-- Per-candidate decisions from successful current runs; never derived from pool totals.
CREATE SCHEMA IF NOT EXISTS recommendation_evidence;
CREATE TABLE recommendation_evidence.observations (
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id) ON DELETE CASCADE,
    vacancy_id uuid NOT NULL,
    run_id uuid NOT NULL,
    category text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(candidate_id,vacancy_id,run_id,category)
);
CREATE INDEX recommendation_evidence_candidate_time ON recommendation_evidence.observations(candidate_id,recorded_at);
