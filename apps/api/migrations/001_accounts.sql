-- Auth schema for independent role views, email OTP challenges and durable mail jobs.
-- The API migration runner verifies this file's checksum before applying it.

CREATE TABLE IF NOT EXISTS accounts (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('candidate', 'employer')),
    email_verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    processing_consent_version text,
    processing_consent_at timestamptz,
    CONSTRAINT accounts_processing_consent_pair CHECK (
        (processing_consent_version IS NULL) = (processing_consent_at IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS auth_challenges (
    id uuid PRIMARY KEY,
    email text NOT NULL,
    role text NOT NULL CHECK (role IN ('candidate', 'employer')),
    code_hmac bytea,
    ip_hmac bytea NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'consumed', 'expired', 'exhausted', 'invalidated')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= 5),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    invalidated_at timestamptz
);

CREATE INDEX IF NOT EXISTS auth_challenges_email_created_idx
    ON auth_challenges (email, created_at DESC);
CREATE INDEX IF NOT EXISTS auth_challenges_active_idx
    ON auth_challenges (email, status, expires_at);

CREATE TABLE IF NOT EXISTS auth_mail_outbox (
    id uuid PRIMARY KEY,
    challenge_id uuid NOT NULL REFERENCES auth_challenges(id) ON DELETE CASCADE,
    email text NOT NULL,
    encrypted_payload bytea,
    nonce bytea,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'delivered', 'failed', 'expired')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token text,
    leased_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);

CREATE INDEX IF NOT EXISTS auth_mail_outbox_claim_idx
    ON auth_mail_outbox (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS auth_mail_outbox_challenge_idx
    ON auth_mail_outbox (challenge_id);

CREATE TABLE IF NOT EXISTS auth_issue_events (
    id bigserial PRIMARY KEY,
    email text NOT NULL,
    ip_hmac bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS auth_issue_events_email_created_idx
    ON auth_issue_events (email, created_at DESC);
CREATE INDEX IF NOT EXISTS auth_issue_events_ip_created_idx
    ON auth_issue_events (ip_hmac, created_at DESC);

CREATE TABLE IF NOT EXISTS auth_verify_events (
    id bigserial PRIMARY KEY,
    ip_hmac bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS auth_verify_events_ip_created_idx
    ON auth_verify_events (ip_hmac, created_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('candidate', 'employer')),
    session_hash bytea NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE INDEX IF NOT EXISTS sessions_account_idx ON sessions (account_id, role, revoked_at);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions (expires_at);
