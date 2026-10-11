-- Support console: operator sessions and per-conversation read cursors.
-- Conversation content stays in system_notifications; this migration only adds
-- the operator-side state that must survive restarts.
CREATE TABLE IF NOT EXISTS support_sessions (
    id uuid PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS support_sessions_expires_idx ON support_sessions (expires_at);

CREATE TABLE IF NOT EXISTS support_conversation_reads (
    account_id uuid PRIMARY KEY REFERENCES system_inboxes(account_id) ON DELETE CASCADE,
    read_sequence bigint NOT NULL DEFAULT 0 CHECK (read_sequence BETWEEN 0 AND 9007199254740991),
    updated_at timestamptz NOT NULL DEFAULT now()
);
