-- Process ownership is resolved through recruitment_processes before aggregate reads.
CREATE INDEX recruitment_events_statistics_time
    ON recruitment_events(process_id,created_at) INCLUDE(type);
