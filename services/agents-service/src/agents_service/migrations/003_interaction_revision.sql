ALTER TABLE agents_service.agent_interactions
    ADD COLUMN draft_revision bigint NOT NULL DEFAULT 1 CHECK (draft_revision > 0);
