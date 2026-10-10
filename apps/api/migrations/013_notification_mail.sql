-- Durable event email jobs reference live authoritative sources, never snapshot contacts.
CREATE TABLE notification_mail_cutover (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    installed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO notification_mail_cutover(singleton) VALUES(true);

CREATE TABLE notification_mail_outbox (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('monthly_task','employer_interview','candidate_interview','resume_liked')),
    source_id uuid NOT NULL,
    task_title text CHECK(length(task_title) BETWEEN 1 AND 300),
    expires_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','delivered','failed','dropped')),
    attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    error_code text CHECK(error_code IN ('smtp_unavailable','rejected','source_unavailable')),
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    UNIQUE(account_id,kind,source_id),
    CHECK((kind='monthly_task') = (task_title IS NOT NULL))
);
CREATE INDEX notification_mail_poll ON notification_mail_outbox(status,available_at,lease_until);

-- Interview insertion is already inside the authorized recruitment CAS transaction.
-- Rollback and idempotent command replay therefore never create a second email.
CREATE FUNCTION enqueue_interview_mail() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE process recruitment_processes%ROWTYPE;
BEGIN
    SELECT * INTO process FROM recruitment_processes WHERE id=NEW.process_id;
    INSERT INTO notification_mail_outbox(id,account_id,kind,source_id,expires_at)
    VALUES(gen_random_uuid(),process.owner_id,'employer_interview',NEW.id,NEW.starts_at),
          (gen_random_uuid(),process.candidate_id,'candidate_interview',NEW.id,NEW.starts_at)
    ON CONFLICT(account_id,kind,source_id) DO NOTHING;
    RETURN NEW;
END;
$$;
CREATE TRIGGER recruitment_interview_notification AFTER INSERT ON recruitment_interviews
FOR EACH ROW EXECUTE FUNCTION enqueue_interview_mail();
