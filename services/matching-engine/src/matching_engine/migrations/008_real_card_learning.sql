-- Preserve failed experiment gates: manual rollout has a separate immutable audit.
CREATE TABLE rerank_pipeline.approval_receipts (
    receipt_hash text PRIMARY KEY,
    artifact_hash text REFERENCES rerank_pipeline.artifacts(artifact_hash),
    action text NOT NULL CHECK (action IN ('activate','deactivate')),
    receipt jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE rerank_pipeline.settings
    ADD COLUMN active_approval_receipt_hash text REFERENCES rerank_pipeline.approval_receipts(receipt_hash),
    ADD COLUMN activation_revision bigint NOT NULL DEFAULT 0 CHECK (activation_revision >= 0);
ALTER TABLE rerank_pipeline.runs
    ADD COLUMN approval_receipt_hash text REFERENCES rerank_pipeline.approval_receipts(receipt_hash),
    ADD COLUMN activation_revision bigint NOT NULL DEFAULT 0 CHECK (activation_revision >= 0);
DO $$ DECLARE old_constraint text; BEGIN
    FOR old_constraint IN SELECT conname FROM pg_constraint
        WHERE conrelid='rerank_pipeline.runs'::regclass AND contype='u'
          AND pg_get_constraintdef(oid) =
              'UNIQUE NULLS NOT DISTINCT (main_run_id, artifact_hash, feature_schema_hash, tail_policy_hash)'
    LOOP
        EXECUTE format('ALTER TABLE rerank_pipeline.runs DROP CONSTRAINT %I', old_constraint);
    END LOOP;
END $$;
ALTER TABLE rerank_pipeline.runs ADD CONSTRAINT rerank_run_identity
    UNIQUE NULLS NOT DISTINCT (main_run_id,artifact_hash,feature_schema_hash,tail_policy_hash,
                              approval_receipt_hash,activation_revision);
CREATE TABLE rerank_pipeline.activation_events (
    event_id uuid PRIMARY KEY,
    action text NOT NULL CHECK (action IN ('gated_promote','manual_activate','manual_deactivate')),
    previous_artifact_hash text,
    previous_approval_receipt_hash text,
    artifact_hash text,
    approval_receipt_hash text REFERENCES rerank_pipeline.approval_receipts(receipt_hash),
    activation_revision bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION rerank_pipeline.reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable activation audit'; END $$;
CREATE TRIGGER immutable_approval_receipts BEFORE UPDATE OR DELETE
    ON rerank_pipeline.approval_receipts FOR EACH ROW EXECUTE FUNCTION rerank_pipeline.reject_audit_mutation();
CREATE TRIGGER immutable_activation_events BEFORE UPDATE OR DELETE
    ON rerank_pipeline.activation_events FOR EACH ROW EXECUTE FUNCTION rerank_pipeline.reject_audit_mutation();
ALTER TABLE feed_pipeline.sessions
    ADD COLUMN activation_revision bigint NOT NULL DEFAULT 0;

CREATE SCHEMA learning_pipeline;
CREATE TABLE learning_pipeline.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    source text NOT NULL DEFAULT 'operational' CHECK(source IN ('operational','synthetic'))
);
INSERT INTO learning_pipeline.settings(singleton) VALUES(true);
CREATE TABLE learning_pipeline.cohorts (
    main_run_id uuid PRIMARY KEY REFERENCES matching_pipeline.runs(run_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL,
    vacancy_revision bigint NOT NULL,
    schema_hash text NOT NULL,
    feature_schema_hash text NOT NULL,
    policy_hash text NOT NULL,
    source text NOT NULL CHECK(source IN ('operational','synthetic')),
    scanned_count integer NOT NULL,
    passed_count integer NOT NULL,
    selected_count integer NOT NULL,
    outside_top_k integer NOT NULL,
    published_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE learning_pipeline.samples (
    main_run_id uuid NOT NULL REFERENCES learning_pipeline.cohorts(main_run_id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES candidate_pipeline.candidates(candidate_id) ON DELETE CASCADE,
    profile_revision bigint NOT NULL,
    main_position integer NOT NULL,
    main_score double precision NOT NULL,
    features jsonb,
    feature_hash text NOT NULL,
    consent_version text,
    consent_accepted_at text,
    withdrawn_at timestamptz,
    PRIMARY KEY(main_run_id,candidate_id),
    CHECK ((withdrawn_at IS NULL AND features IS NOT NULL) OR
           (withdrawn_at IS NOT NULL AND features IS NULL))
);
CREATE TABLE learning_pipeline.events (
    event_id uuid PRIMARY KEY,
    exposure_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    vacancy_id uuid NOT NULL,
    event_type text NOT NULL CHECK(event_type IN ('allocated','delivered','impression','like','dislike')),
    idempotency_key uuid,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(exposure_id,event_type),
    UNIQUE(owner_id,event_type,idempotency_key),
    FOREIGN KEY(exposure_id,owner_id,vacancy_id)
        REFERENCES feed_pipeline.exposures(exposure_id,owner_id,vacancy_id) ON DELETE CASCADE
);
CREATE INDEX learning_samples_candidate ON learning_pipeline.samples(candidate_id);
CREATE INDEX learning_events_cursor ON learning_pipeline.events(recorded_at,event_id);
