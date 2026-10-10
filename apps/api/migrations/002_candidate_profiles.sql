-- Candidate-owned source profiles and the exact snapshots delivered to matching.
-- The API migration runner verifies this file's checksum before applying it.

CREATE TABLE IF NOT EXISTS candidate_profiles (
    candidate_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    revision bigint NOT NULL CHECK (revision > 0),
    fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    processing_consent boolean NOT NULL,
    pool_consent boolean NOT NULL,
    consent_version text NOT NULL CHECK (length(consent_version) > 0),
    consent_accepted_at timestamptz,
    CONSTRAINT candidate_profiles_pool_requires_processing CHECK (pool_consent = false OR processing_consent = true),
    CONSTRAINT candidate_profiles_processing_stamp CHECK (
        (processing_consent = true AND consent_accepted_at IS NOT NULL)
        OR (processing_consent = false AND consent_accepted_at IS NULL)
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_profile_evidence (
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    evidence_key text NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL CHECK (status IN ('confirmed', 'partial', 'self_reported', 'pending', 'rejected')),
    verified_value jsonb,
    provenance jsonb NOT NULL DEFAULT '[]'::jsonb,
    checked_at timestamptz,
    PRIMARY KEY (candidate_id, evidence_key)
);

CREATE INDEX IF NOT EXISTS candidate_profile_evidence_candidate_idx
    ON candidate_profile_evidence (candidate_id);

CREATE TABLE IF NOT EXISTS candidate_profile_outbox (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    source_revision bigint NOT NULL CHECK (source_revision > 0),
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'processing', 'retrying', 'failed', 'delivered', 'obsolete')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    projected_revision bigint CHECK (projected_revision IS NULL OR projected_revision > 0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    UNIQUE (candidate_id, source_revision)
);

CREATE INDEX IF NOT EXISTS candidate_profile_outbox_claim_idx
    ON candidate_profile_outbox (state, available_at, created_at);
CREATE INDEX IF NOT EXISTS candidate_profile_outbox_candidate_idx
    ON candidate_profile_outbox (candidate_id, source_revision DESC);
