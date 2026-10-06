-- API-owned post-selection hiring aggregates; contacts are never snapshotted here.
CREATE TABLE recruitment_processes (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES accounts(id),
    candidate_id uuid NOT NULL REFERENCES accounts(id),
    vacancy_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    profile_revision bigint NOT NULL CHECK (profile_revision > 0),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    state text NOT NULL CHECK (state IN ('sent','viewed','accepted','declined','rejected','offer_sent','hired','offer_declined','withdrawn')),
    terms jsonb,
    invited_at timestamptz,
    closure jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,vacancy_id,candidate_id),
    CHECK(owner_id <> candidate_id),
    CHECK ((invited_at IS NULL AND terms IS NULL AND state='rejected') OR (invited_at IS NOT NULL AND terms IS NOT NULL))
);
CREATE INDEX recruitment_candidate_idx ON recruitment_processes(candidate_id,created_at DESC,id);
CREATE INDEX recruitment_owner_draft_idx ON recruitment_processes(owner_id,draft_id,created_at DESC,id);

CREATE TABLE recruitment_interviews (
    id uuid PRIMARY KEY,
    process_id uuid NOT NULL REFERENCES recruitment_processes(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('technical','general')),
    state text NOT NULL CHECK(state IN ('proposed','scheduled','declined','cancelled','completed')),
    starts_at timestamptz NOT NULL,
    time_zone text NOT NULL,
    duration_minutes integer NOT NULL CHECK(duration_minutes BETWEEN 1 AND 480),
    format text NOT NULL CHECK(format IN ('remote','onsite')),
    location text NOT NULL,
    completed_at timestamptz,
    outcome text CHECK(outcome IN ('passed','failed','neutral')),
    feedback text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state='completed' AND completed_at IS NOT NULL AND outcome IS NOT NULL AND length(feedback)>0)
        OR (state<>'completed' AND completed_at IS NULL AND outcome IS NULL))
);
-- Completed meetings and current proposals each consume the only slot of their type.
CREATE UNIQUE INDEX recruitment_interview_slot_idx ON recruitment_interviews(process_id,kind)
    WHERE state IN ('proposed','scheduled','completed');

CREATE TABLE recruitment_offers (
    id uuid PRIMARY KEY,
    process_id uuid NOT NULL REFERENCES recruitment_processes(id) ON DELETE CASCADE,
    state text NOT NULL CHECK(state IN ('sent','accepted','declined','expired')),
    salary_rub bigint NOT NULL CHECK(salary_rub > 0 AND salary_rub <= 9007199254740991),
    description text NOT NULL CHECK(length(description)>0),
    expires_at timestamptz NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CHECK(expires_at > sent_at),
    CHECK ((state='sent' AND resolved_at IS NULL) OR (state<>'sent' AND resolved_at IS NOT NULL))
);
CREATE UNIQUE INDEX recruitment_active_offer_idx ON recruitment_offers(process_id) WHERE state='sent';

CREATE TABLE recruitment_receipts (
    actor_id uuid NOT NULL REFERENCES accounts(id),
    idempotency_key uuid NOT NULL,
    fingerprint text NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(actor_id,idempotency_key)
);

-- Ordered durable events for participant polling and future notification/chat consumers.
-- No contact data, invitation prose, meeting URLs or feedback in event payloads.
CREATE TABLE recruitment_events (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    process_id uuid NOT NULL REFERENCES recruitment_processes(id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    type text NOT NULL,
    state text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(process_id,revision)
);
