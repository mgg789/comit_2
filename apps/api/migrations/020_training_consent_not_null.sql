-- PostgreSQL CHECK accepts NULL expressions: a consent version must be explicitly present.
-- Do not fabricate consent for invalid legacy rows; validation fails and preserves the transaction.
ALTER TABLE candidate_profiles DROP CONSTRAINT candidate_profiles_training_stamp;
ALTER TABLE candidate_profiles ADD CONSTRAINT candidate_profiles_training_stamp CHECK (
 (training_consent AND processing_consent AND pool_consent
  AND training_consent_version IS NOT NULL
  AND training_consent_version IN ('training-v1','training-v2')
  AND training_consent_accepted_at IS NOT NULL)
 OR (NOT training_consent AND training_consent_version IS NULL AND training_consent_accepted_at IS NULL)
);
