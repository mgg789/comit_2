-- CHECK expressions admit NULL; completed interviews require explicit non-null feedback.
ALTER TABLE recruitment_interviews ADD CONSTRAINT recruitment_completed_feedback_required
    CHECK (state <> 'completed' OR feedback IS NOT NULL);
