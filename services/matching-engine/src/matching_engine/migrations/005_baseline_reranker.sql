CREATE SCHEMA rerank_pipeline;
CREATE TABLE rerank_pipeline.artifacts (
 artifact_hash text PRIMARY KEY, artifact jsonb NOT NULL,
 approved boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE rerank_pipeline.settings (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 active_artifact_hash text REFERENCES rerank_pipeline.artifacts(artifact_hash)
);
INSERT INTO rerank_pipeline.settings(singleton) VALUES(true);
CREATE TABLE rerank_pipeline.runs (
 run_id uuid PRIMARY KEY, main_run_id uuid NOT NULL REFERENCES matching_pipeline.runs(run_id),
 vacancy_id uuid NOT NULL, artifact_hash text REFERENCES rerank_pipeline.artifacts(artifact_hash),
 feature_schema_hash text NOT NULL, tail_policy_hash text NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','processing','retrying','failed','ready','obsolete')),
 mode text CHECK(mode IN ('learned','main_fallback')), fallback_reason text,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), error_code text,
 lease_token uuid, lease_until timestamptz, available_at timestamptz NOT NULL DEFAULT now(),
 recommended_count integer NOT NULL DEFAULT 0, reserve_count integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
 UNIQUE NULLS NOT DISTINCT(main_run_id,artifact_hash,feature_schema_hash,tail_policy_hash)
);
CREATE INDEX rerank_pending ON rerank_pipeline.runs(available_at) WHERE state IN ('queued','processing','retrying');
CREATE TABLE rerank_pipeline.controls (
 vacancy_id uuid PRIMARY KEY, current_run_id uuid NOT NULL REFERENCES rerank_pipeline.runs(run_id)
);
CREATE TABLE rerank_pipeline.cards (
 run_id uuid NOT NULL REFERENCES rerank_pipeline.runs(run_id), candidate_id uuid NOT NULL,
 profile_revision integer NOT NULL, position integer NOT NULL CHECK(position>0),
 kind text NOT NULL CHECK(kind IN ('recommended','reserve')), card jsonb NOT NULL,
 PRIMARY KEY(run_id,candidate_id), UNIQUE(run_id,position)
);
CREATE TABLE rerank_pipeline.manual_requests (
 idempotency_key uuid PRIMARY KEY, vacancy_id uuid NOT NULL,
 run_id uuid NOT NULL REFERENCES rerank_pipeline.runs(run_id)
);
