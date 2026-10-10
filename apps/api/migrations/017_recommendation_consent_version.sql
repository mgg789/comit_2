-- Keep legacy ranking consent valid; new recommendation processing requires a fresh v2 choice.
ALTER TABLE candidate_profiles DROP CONSTRAINT candidate_profiles_training_stamp;
ALTER TABLE candidate_profiles ADD CONSTRAINT candidate_profiles_training_stamp CHECK (
 (training_consent AND processing_consent AND pool_consent
  AND training_consent_version IN ('training-v1','training-v2') AND training_consent_accepted_at IS NOT NULL)
 OR (NOT training_consent AND training_consent_version IS NULL AND training_consent_accepted_at IS NULL)
);
CREATE OR REPLACE FUNCTION purge_candidate_recommendations_on_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT NEW.processing_consent OR NOT NEW.pool_consent OR NOT NEW.training_consent
    OR NEW.training_consent_version IS DISTINCT FROM 'training-v2' THEN
  DELETE FROM candidate_recommendations WHERE candidate_id=NEW.candidate_id;
  IF to_regclass('recommendation_evidence.observations') IS NOT NULL THEN
   EXECUTE 'DELETE FROM recommendation_evidence.observations WHERE candidate_id=$1' USING NEW.candidate_id;
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER candidate_recommendations_consent_withdrawal ON candidate_profiles;
CREATE TRIGGER candidate_recommendations_consent_withdrawal
AFTER UPDATE OF processing_consent,pool_consent,training_consent,training_consent_version ON candidate_profiles
FOR EACH ROW EXECUTE FUNCTION purge_candidate_recommendations_on_withdrawal();
DELETE FROM candidate_recommendations r USING candidate_profiles p
WHERE r.candidate_id=p.candidate_id AND p.training_consent_version IS DISTINCT FROM 'training-v2';
