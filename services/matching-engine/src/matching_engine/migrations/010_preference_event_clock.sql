-- Capture actions after lock acquisition. Transaction-start timestamps can
-- include feedback committed later in an earlier already-sealed fit snapshot.
ALTER TABLE learning_pipeline.events ALTER COLUMN recorded_at SET DEFAULT clock_timestamp();
ALTER TABLE preference_pipeline.jobs ALTER COLUMN requested_at SET DEFAULT clock_timestamp();
