CREATE SCHEMA IF NOT EXISTS agents_service;

CREATE TABLE agents_service.agent_sessions (
    session_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    draft_id uuid NOT NULL REFERENCES agents_service.drafts(draft_id),
    agent_key text NOT NULL,
    agent_version text NOT NULL,
    knowledge_version text NOT NULL,
    knowledge_hash text NOT NULL,
    definition_hash text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'approved', 'closed', 'failed')),
    draft_revision bigint NOT NULL CHECK (draft_revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agents_service_sessions_owner ON agents_service.agent_sessions(owner_id, updated_at DESC);
CREATE INDEX agents_service_sessions_draft ON agents_service.agent_sessions(owner_id, draft_id, updated_at DESC);

CREATE TABLE agents_service.agent_turns (
    turn_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES agents_service.agent_sessions(session_id),
    owner_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    sequence integer NOT NULL CHECK (sequence > 0),
    user_message text NOT NULL CHECK (length(user_message) BETWEEN 1 AND 8000),
    expected_revision bigint NOT NULL CHECK (expected_revision > 0),
    draft_revision bigint NOT NULL CHECK (draft_revision > 0),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'retrying', 'failed', 'succeeded')),
    idempotency_key text NOT NULL,
    provider_result jsonb,
    input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    rounds integer NOT NULL DEFAULT 0 CHECK (rounds >= 0),
    tool_calls integer NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, idempotency_key),
    UNIQUE (session_id, sequence)
);
CREATE INDEX agents_service_turns_session ON agents_service.agent_turns(session_id, sequence);
CREATE INDEX agents_service_turns_active ON agents_service.agent_turns(session_id, status)
    WHERE status IN ('queued', 'processing', 'retrying');

CREATE TABLE agents_service.agent_messages (
    message_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES agents_service.agent_sessions(session_id),
    turn_id uuid REFERENCES agents_service.agent_turns(turn_id),
    role text NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content text NOT NULL DEFAULT '',
    tool_calls jsonb NOT NULL DEFAULT '[]'::jsonb,
    name text,
    tool_call_id text,
    sequence integer NOT NULL CHECK (sequence >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, sequence)
);
CREATE INDEX agents_service_messages_session ON agents_service.agent_messages(session_id, sequence);

CREATE TABLE agents_service.agent_interactions (
    interaction_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES agents_service.agent_sessions(session_id),
    turn_id uuid NOT NULL REFERENCES agents_service.agent_turns(turn_id),
    kind text NOT NULL CHECK (kind IN ('buttons', 'single_select', 'multi_select', 'slider')),
    prompt text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 1000),
    field_target text,
    options jsonb NOT NULL DEFAULT '[]'::jsonb,
    min_value integer,
    max_value integer,
    value jsonb,
    responded boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    responded_at timestamptz
);
CREATE INDEX agents_service_interactions_pending ON agents_service.agent_interactions(session_id, responded, created_at);

CREATE TABLE agents_service.agent_tool_journal (
    turn_id uuid NOT NULL REFERENCES agents_service.agent_turns(turn_id),
    call_id text NOT NULL,
    tool_name text NOT NULL,
    arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    result jsonb,
    draft_revision bigint NOT NULL CHECK (draft_revision > 0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (turn_id, call_id)
);

CREATE TABLE agents_service.agent_external_cache (
    owner_id uuid NOT NULL,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, request_hash)
);
