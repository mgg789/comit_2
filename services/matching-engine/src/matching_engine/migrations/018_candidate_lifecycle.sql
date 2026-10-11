-- Candidate deletion is fenced so an in-flight source projection cannot recreate public data.
CREATE TABLE candidate_pipeline.tombstones (
    candidate_id uuid PRIMARY KEY,
    deleted_at timestamptz NOT NULL DEFAULT now()
);
