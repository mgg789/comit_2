-- Preserve the selected mode and private reasoning for resumed tool rounds.
ALTER TABLE agents_service.organization_turns
 ADD COLUMN reasoning_effort text NOT NULL DEFAULT 'off'
 CHECK (reasoning_effort IN ('off','medium','max'));
ALTER TABLE agents_service.organization_turns
 ADD COLUMN reasoning_history jsonb NOT NULL DEFAULT '{}'
 CHECK (jsonb_typeof(reasoning_history) = 'object');
