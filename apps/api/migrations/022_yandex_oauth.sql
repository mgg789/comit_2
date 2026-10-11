-- Browser-bound, single-use OAuth transactions and explicit provider identities.
CREATE TABLE auth_oauth_states (
  state_hash bytea PRIMARY KEY,
  role text NOT NULL CHECK (role IN ('candidate', 'employer')),
  consent_version text NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE INDEX auth_oauth_states_expiry ON auth_oauth_states(expires_at);
CREATE TABLE auth_oauth_identities (
  provider text NOT NULL CHECK (provider = 'yandex'),
  subject text NOT NULL,
  account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(provider, subject),
  UNIQUE(provider, account_id)
);
