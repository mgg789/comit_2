CREATE SCHEMA IF NOT EXISTS agents_service;

CREATE TABLE agents_service.organization_chats (
    owner_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
    presence boolean NOT NULL DEFAULT false,
    last_seen_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents_service.organization_context (
    owner_id uuid PRIMARY KEY REFERENCES agents_service.organization_chats(owner_id) ON DELETE CASCADE,
    current_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents_service.organization_memory (
    owner_id uuid PRIMARY KEY REFERENCES agents_service.organization_chats(owner_id) ON DELETE CASCADE,
    value jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents_service.organization_messages (
    message_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES agents_service.organization_chats(chat_id) ON DELETE CASCADE,
    turn_id uuid,
    role text NOT NULL CHECK (role IN ('system','user','assistant','tool')),
    content text NOT NULL DEFAULT '' CHECK (length(content) <= 32000),
    tool_calls jsonb NOT NULL DEFAULT '[]'::jsonb,
    tool_call_id text,
    name text,
    attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chat_id, sequence),
    UNIQUE (chat_id, idempotency_key)
);
CREATE INDEX agents_service_org_messages_page ON agents_service.organization_messages(chat_id, sequence DESC);

CREATE TABLE agents_service.organization_turns (
    turn_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES agents_service.organization_chats(chat_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    task_id uuid,
    sequence bigint NOT NULL CHECK (sequence > 0),
    user_message text NOT NULL CHECK (length(user_message) BETWEEN 1 AND 8000),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','retrying','succeeded','failed')),
    idempotency_key text NOT NULL,
    provider_result jsonb,
    input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    rounds integer NOT NULL DEFAULT 0 CHECK (rounds >= 0),
    tool_calls integer NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    error_code text,
    error_message text,
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, idempotency_key),
    UNIQUE (chat_id, sequence)
);
CREATE INDEX agents_service_org_turns_queue ON agents_service.organization_turns(status, updated_at)
    WHERE status IN ('queued','retrying');
CREATE INDEX agents_service_org_turns_owner ON agents_service.organization_turns(owner_id, sequence DESC);
CREATE UNIQUE INDEX agents_service_org_one_processing_turn ON agents_service.organization_turns(owner_id)
    WHERE status = 'processing';

CREATE TABLE agents_service.organization_tool_journal (
    turn_id uuid NOT NULL REFERENCES agents_service.organization_turns(turn_id) ON DELETE CASCADE,
    call_id text NOT NULL,
    tool_name text NOT NULL,
    arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL CHECK (status IN ('started','completed','failed')),
    result jsonb,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (turn_id, call_id)
);

CREATE TABLE agents_service.organization_pending_answers (
    answer_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES agents_service.organization_chats(chat_id) ON DELETE CASCADE,
    turn_id uuid NOT NULL REFERENCES agents_service.organization_turns(turn_id) ON DELETE CASCADE,
    prompt text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 2000),
    answer_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
    answer jsonb,
    answered boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz
);
CREATE INDEX agents_service_org_answers_pending ON agents_service.organization_pending_answers(chat_id, answered, created_at);

CREATE TABLE agents_service.organization_tasks (
    task_id uuid PRIMARY KEY,
    chat_id uuid NOT NULL REFERENCES agents_service.organization_chats(chat_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('schedule_interview','inquiry','stand_in')),
    instruction text NOT NULL CHECK (length(instruction) BETWEEN 1 AND 8000),
    conversation_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','retrying','succeeded','failed','stopped')),
    stop_reason text,
    result jsonb,
    error_code text,
    idempotency_key text NOT NULL,
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id, idempotency_key)
);
CREATE INDEX agents_service_org_tasks_queue ON agents_service.organization_tasks(status, updated_at)
    WHERE status IN ('queued','retrying');

CREATE TABLE agents_service.organization_provider_calls (
    owner_id uuid NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('in_flight','completed','failed')),
    response jsonb,
    input_tokens integer,
    output_tokens integer,
    reservation_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(owner_id, request_hash)
);

CREATE TABLE agents_service.organization_usage_reservations (
    reservation_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES agents_service.organization_chats(owner_id) ON DELETE CASCADE,
    turn_id uuid,
    input_upper_bound_micro_usd numeric(30,6) NOT NULL DEFAULT 0 CHECK (input_upper_bound_micro_usd >= 0),
    output_upper_bound_micro_usd numeric(30,6) NOT NULL DEFAULT 0 CHECK (output_upper_bound_micro_usd >= 0),
    upper_bound_micro_usd numeric(30,6) NOT NULL CHECK (upper_bound_micro_usd >= 0),
    status text NOT NULL CHECK (status IN ('reserved','captured','released')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agents_service_org_reservations_window ON agents_service.organization_usage_reservations(owner_id, created_at)
    WHERE status = 'reserved';

CREATE TABLE agents_service.organization_usage_ledger (
    ledger_id uuid PRIMARY KEY,
    reservation_id uuid NOT NULL UNIQUE REFERENCES agents_service.organization_usage_reservations(reservation_id),
    owner_id uuid NOT NULL REFERENCES agents_service.organization_chats(owner_id) ON DELETE CASCADE,
    input_tokens integer NOT NULL CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL CHECK (output_tokens >= 0),
    input_micro_usd numeric(30,6) NOT NULL CHECK (input_micro_usd >= 0),
    output_micro_usd numeric(30,6) NOT NULL CHECK (output_micro_usd >= 0),
    total_micro_usd numeric(30,6) NOT NULL CHECK (total_micro_usd >= 0),
    rate_config jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agents_service_org_usage_window ON agents_service.organization_usage_ledger(owner_id, created_at);
