ALTER TABLE agents_service.organization_tasks DROP CONSTRAINT organization_tasks_status_check;
ALTER TABLE agents_service.organization_tasks ADD CONSTRAINT organization_tasks_status_check
 CHECK(status IN ('queued','processing','retrying','waiting','waiting_employer','succeeded','failed','stopped'));
ALTER TABLE agents_service.organization_tasks ADD COLUMN next_run_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX organization_tasks_due ON agents_service.organization_tasks(next_run_at,created_at)
 WHERE status IN ('queued','retrying','waiting');
ALTER TABLE agents_service.organization_turns ADD COLUMN source text NOT NULL DEFAULT 'employer'
 CHECK(source IN ('employer','workflow'));
ALTER TABLE agents_service.organization_turns ADD COLUMN task_lease_token uuid;
CREATE UNIQUE INDEX organization_owner_active_turn ON agents_service.organization_turns(owner_id)
 WHERE status IN ('queued','processing','retrying');
ALTER TABLE agents_service.organization_pending_answers ALTER COLUMN turn_id DROP NOT NULL;
ALTER TABLE agents_service.organization_pending_answers ADD COLUMN task_id uuid
 REFERENCES agents_service.organization_tasks(task_id);
CREATE UNIQUE INDEX organization_pending_task_question
 ON agents_service.organization_pending_answers(task_id,prompt) WHERE answered=false;
