-- Durable weekly jobs and candidate-owned recommendation results.
CREATE TABLE candidate_recommendations (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    week_start date NOT NULL,
    source_revision bigint NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','processing','retrying','ready','insufficient_data','provider_unavailable')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    lease_token uuid,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    evidence jsonb NOT NULL DEFAULT '[]',
    text text CHECK (text IS NULL OR length(text) BETWEEN 1 AND 3000),
    generated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(candidate_id,week_start),
    CHECK (extract(isodow FROM week_start)=1),
    CHECK ((state='processing') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK ((state IN ('ready','insufficient_data','provider_unavailable')) = (text IS NOT NULL AND generated_at IS NOT NULL))
);
CREATE INDEX candidate_recommendations_claim ON candidate_recommendations(available_at,created_at) WHERE state IN ('queued','retrying','processing');
CREATE FUNCTION purge_candidate_recommendations_on_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.processing_consent OR NOT NEW.pool_consent OR NOT NEW.training_consent THEN
        DELETE FROM candidate_recommendations WHERE candidate_id=NEW.candidate_id;
        IF to_regclass('recommendation_evidence.observations') IS NOT NULL THEN
            EXECUTE 'DELETE FROM recommendation_evidence.observations WHERE candidate_id=$1' USING NEW.candidate_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER candidate_recommendations_consent_withdrawal
AFTER UPDATE OF processing_consent,pool_consent,training_consent ON candidate_profiles
FOR EACH ROW EXECUTE FUNCTION purge_candidate_recommendations_on_withdrawal();
