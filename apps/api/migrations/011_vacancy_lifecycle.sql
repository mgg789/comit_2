-- Durable API-owned coordination; no service-owned tables are read or mutated here.
CREATE TABLE vacancy_lifecycle_commands (
    draft_id uuid NOT NULL,
    owner_id uuid NOT NULL REFERENCES accounts(id),
    expected_revision bigint NOT NULL CHECK (expected_revision > 0),
    action text NOT NULL CHECK (action IN ('withdraw','close','delete')),
    phase text NOT NULL DEFAULT 'queued' CHECK (phase IN ('queued','source_applied','completed','failed')),
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(draft_id,expected_revision,action)
);
CREATE UNIQUE INDEX vacancy_lifecycle_pending_idx ON vacancy_lifecycle_commands(draft_id)
    WHERE phase IN ('queued','source_applied');
