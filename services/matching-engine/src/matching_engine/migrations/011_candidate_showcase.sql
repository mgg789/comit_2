-- Manual selections share saved/exposure/decision storage without inventing ML runs.
ALTER TABLE feed_pipeline.sessions
    ADD COLUMN source text NOT NULL DEFAULT 'tinder' CHECK (source IN ('tinder','showcase')),
    ALTER COLUMN main_run_id DROP NOT NULL,
    ALTER COLUMN rerank_run_id DROP NOT NULL;
ALTER TABLE feed_pipeline.sessions ADD CONSTRAINT feed_session_source_runs CHECK (
    (source='tinder' AND main_run_id IS NOT NULL AND rerank_run_id IS NOT NULL) OR
    (source='showcase' AND main_run_id IS NULL AND rerank_run_id IS NULL AND artifact_hash IS NULL
        AND activation_revision=0 AND preference_model_hash IS NULL)
);
ALTER TABLE feed_pipeline.exposures DROP CONSTRAINT exposures_kind_check;
ALTER TABLE feed_pipeline.exposures ADD CONSTRAINT exposures_kind_check
    CHECK (kind IN ('recommended','reserve','showcase'));

CREATE TABLE candidate_pipeline.search_embeddings (
    cache_key text PRIMARY KEY,
    model_version text NOT NULL,
    semantic_space_id text NOT NULL,
    semantic_vector vector(640) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX candidate_search_cache_expiry ON candidate_pipeline.search_embeddings(created_at);
-- Only the versioned redacted public projection enters lexical retrieval.
CREATE INDEX candidate_showcase_lexical ON candidate_pipeline.pool USING gin
    (jsonb_to_tsvector('russian'::regconfig,public_profile,'["string"]'::jsonb));
