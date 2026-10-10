-- Company identity belongs to one employer account; terms remain immutable history.
CREATE TABLE employer_profiles (
    owner_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    company_name text NOT NULL CHECK(length(company_name) BETWEEN 1 AND 200 AND company_name=btrim(company_name)),
    revision bigint NOT NULL DEFAULT 1 CHECK(revision BETWEEN 1 AND 9007199254740991),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Backfill only the latest actual invitation snapshot, never inferred/demo identity.
INSERT INTO employer_profiles(owner_id,company_name,updated_at)
SELECT DISTINCT ON (p.owner_id) p.owner_id,btrim(p.terms->>'company_name'),p.updated_at
FROM recruitment_processes p JOIN accounts a ON a.id=p.owner_id AND a.role='employer'
WHERE p.invited_at IS NOT NULL AND jsonb_typeof(p.terms->'company_name')='string'
  AND length(btrim(p.terms->>'company_name')) BETWEEN 1 AND 200
ORDER BY p.owner_id,p.invited_at DESC,p.created_at DESC,p.id DESC;

-- Reconciliation starts at installation; historical likes/tasks are not replayed to accounts.
CREATE TABLE system_notification_cutover (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    installed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO system_notification_cutover(singleton) VALUES(true);

-- Account-local ordering is serialized by the inbox row, including transactional producers.
CREATE TABLE system_inboxes (
    account_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    last_sequence bigint NOT NULL DEFAULT 0 CHECK(last_sequence BETWEEN 0 AND 9007199254740991),
    delivered_sequence bigint NOT NULL DEFAULT 0 CHECK(delivered_sequence BETWEEN 0 AND last_sequence),
    read_sequence bigint NOT NULL DEFAULT 0 CHECK(read_sequence BETWEEN 0 AND delivered_sequence)
);
CREATE TABLE system_notifications (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES system_inboxes(account_id) ON DELETE CASCADE,
    sequence bigint NOT NULL CHECK(sequence BETWEEN 1 AND 9007199254740991),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200),
    type text NOT NULL CHECK(type ~ '^[a-z][a-z0-9_.]{0,79}$'),
    text text NOT NULL CHECK(length(text) BETWEEN 1 AND 2000),
    target jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(account_id,sequence),
    UNIQUE(account_id,idempotency_key)
);
