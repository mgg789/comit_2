-- Preserve grade evidence independently of the ordinal grade in every pool read.
ALTER TABLE candidate_pipeline.pool ADD COLUMN grade_status text NOT NULL DEFAULT 'approved'
    CHECK (grade_status IN ('approved', 'self_reported'));
UPDATE candidate_pipeline.pool p
SET grade_status = CASE WHEN c.profile->'grade_result'->>'status' = 'self_reported'
    THEN 'self_reported' ELSE 'approved' END
FROM candidate_pipeline.candidates c WHERE c.candidate_id = p.candidate_id;
