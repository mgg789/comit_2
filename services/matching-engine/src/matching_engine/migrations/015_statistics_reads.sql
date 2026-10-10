-- Owner-scoped bounded dashboard reads; source payloads remain private.
CREATE INDEX IF NOT EXISTS feed_exposures_statistics_time
    ON feed_pipeline.exposures(owner_id,served_at)
    INCLUDE(vacancy_id,candidate_id) WHERE served_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS learning_impressions_statistics_time
    ON learning_pipeline.events(owner_id,recorded_at)
    INCLUDE(exposure_id,vacancy_id) WHERE event_type='impression';
