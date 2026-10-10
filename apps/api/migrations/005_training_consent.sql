-- Optional ML permission is never inferred from profile or pool consent.
ALTER TABLE candidate_profiles
    ADD COLUMN training_consent boolean NOT NULL DEFAULT false,
    ADD COLUMN training_consent_version text,
    ADD COLUMN training_consent_accepted_at timestamptz,
    ADD CONSTRAINT candidate_profiles_training_stamp CHECK (
        (training_consent AND processing_consent AND pool_consent
         AND training_consent_version IS NOT NULL AND training_consent_version = 'training-v1'
         AND training_consent_accepted_at IS NOT NULL)
        OR (NOT training_consent AND training_consent_version IS NULL AND training_consent_accepted_at IS NULL)
    );
