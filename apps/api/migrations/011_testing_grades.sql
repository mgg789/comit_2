-- Trusted grade results from the testing contour (docs/testing-design.md §7).
-- The testing engine is the reserved trusted producer: it writes approved
-- grade results through the internal token contour; browsers never do.

CREATE TABLE IF NOT EXISTS candidate_grade_results (
    id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES candidate_profiles(candidate_id) ON DELETE CASCADE,
    session_id text NOT NULL UNIQUE,
    specialization text NOT NULL,
    grade text NOT NULL CHECK (grade IN ('intern', 'junior', 'middle', 'senior')),
    supervision_badge text,
    tasks_answered integer NOT NULL DEFAULT 0,
    decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidate_grade_results_candidate
    ON candidate_grade_results(candidate_id, decided_at DESC);
