-- One immutable submission per candidate and UTC week; no claimed client reward.
CREATE TABLE IF NOT EXISTS candidate_weekly_challenges (
 candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
 week date NOT NULL,
 expires_at timestamptz NOT NULL,
 questions jsonb NOT NULL,
 answers jsonb,
 correct integer CHECK(correct BETWEEN 0 AND 3),
 completed_at timestamptz,
 PRIMARY KEY(candidate_id,week)
);
