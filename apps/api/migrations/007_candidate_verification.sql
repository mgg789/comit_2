-- External verification facts and encrypted candidate documents.
-- API006 is reserved for operations; this migration is intentionally API007.

CREATE TABLE IF NOT EXISTS candidate_verification_documents (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    filename text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
    content_type text NOT NULL CHECK (length(content_type) BETWEEN 1 AND 120),
    size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),
    nonce bytea NOT NULL,
    ciphertext bytea NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status = 'pending'),
    claim_kind text,
    claim_id uuid,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE candidate_verification_documents ADD COLUMN IF NOT EXISTS claim_kind text;
ALTER TABLE candidate_verification_documents ADD COLUMN IF NOT EXISTS claim_id uuid;
UPDATE candidate_verification_documents SET claim_kind='projects', claim_id='00000000-0000-0000-0000-000000000000' WHERE claim_kind IS NULL;
ALTER TABLE candidate_verification_documents ALTER COLUMN claim_kind SET NOT NULL;
ALTER TABLE candidate_verification_documents ALTER COLUMN claim_id SET NOT NULL;
DO $$ BEGIN
  ALTER TABLE candidate_verification_documents ADD CONSTRAINT candidate_verification_documents_claim_kind_check CHECK (claim_kind IN ('projects','experience','achievements'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS candidate_verification_ledger (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    evidence_key text NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('confirmed','partial','pending','rejected')),
    verified_value jsonb,
    provenance jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_fsp_demo_auth_states (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    state_hash text NOT NULL UNIQUE CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 1),
    fixture_id text NOT NULL CHECK (fixture_id IN ('fsp-001','fsp-002')),
    expires_at timestamptz NOT NULL,
    used_at timestamptz
);
ALTER TABLE candidate_fsp_demo_auth_states ADD COLUMN IF NOT EXISTS fixture_id text;
UPDATE candidate_fsp_demo_auth_states SET fixture_id='fsp-001' WHERE fixture_id IS NULL;
ALTER TABLE candidate_fsp_demo_auth_states ALTER COLUMN fixture_id SET NOT NULL;
DO $$ BEGIN
  ALTER TABLE candidate_fsp_demo_auth_states ADD CONSTRAINT candidate_fsp_demo_auth_states_fixture_check CHECK (fixture_id IN ('fsp-001','fsp-002'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS candidate_verification_documents_candidate_idx
    ON candidate_verification_documents(candidate_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS candidate_verification_ledger_candidate_idx
    ON candidate_verification_ledger(candidate_id, recorded_at DESC);
