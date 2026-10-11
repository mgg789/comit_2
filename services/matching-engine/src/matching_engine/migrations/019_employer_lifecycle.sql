-- Minimal permanent UUID fence; employer profile data belongs to the API source.
CREATE TABLE vacancy_pipeline.employer_tombstones (
    owner_id uuid PRIMARY KEY,
    deleted_at timestamptz NOT NULL DEFAULT now()
);
