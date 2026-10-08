-- Durable owner-bound replay reservations for organization agent business writes.
-- A started row fences concurrent calls; stale rows are reclaimed and downstream
-- services receive the same idempotency key for deterministic recovery.
CREATE TABLE IF NOT EXISTS organization_agent_tool_receipts (
    owner_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    idempotency_key uuid NOT NULL,
    tool text NOT NULL CHECK (tool IN ('feed_decision','feed_impression','candidate_chat_send','interview_propose')),
    request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'started' CHECK (status IN ('started','completed')),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS organization_agent_tool_receipts_created_idx
    ON organization_agent_tool_receipts(status,updated_at);
