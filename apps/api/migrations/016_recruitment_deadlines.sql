-- API-owned weekday clocks and auditable system expiry, independent of browser activity.
ALTER TABLE recruitment_processes DROP CONSTRAINT recruitment_processes_state_check;
ALTER TABLE recruitment_processes ADD CONSTRAINT recruitment_processes_state_check
  CHECK(state IN ('sent','viewed','accepted','declined','rejected','offer_sent','hired','offer_declined','withdrawn','expired'));
ALTER TABLE recruitment_processes ADD COLUMN deadline jsonb;
ALTER TABLE recruitment_processes ADD COLUMN deadline_suspended boolean NOT NULL DEFAULT false;
ALTER TABLE recruitment_processes ADD COLUMN employer_contacted_at timestamptz;
ALTER TABLE recruitment_processes ADD COLUMN reminder_sent_at timestamptz;
ALTER TABLE recruitment_interviews ADD COLUMN reason_categories text[] NOT NULL DEFAULT '{}';
CREATE INDEX recruitment_deadline_active_idx ON recruitment_processes(updated_at,id) WHERE state='accepted';
CREATE TABLE recruitment_hire_applications (
  process_id uuid PRIMARY KEY REFERENCES recruitment_processes(id) ON DELETE CASCADE,
  candidate_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);
