-- Minimal deletion fence contains no vacancy fields or conversation content.
CREATE TABLE agents_service.vacancy_tombstones (
    vacancy_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    source_revision bigint NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agents_service.vacancy_lifecycle_receipts (
    draft_id uuid NOT NULL REFERENCES agents_service.drafts(draft_id) ON DELETE CASCADE,
    owner_id uuid NOT NULL,
    expected_revision bigint NOT NULL,
    action text NOT NULL CHECK (action IN ('withdraw','close')),
    response jsonb NOT NULL,
    PRIMARY KEY (draft_id, expected_revision, action)
);

-- Draft-owned records must be removed together; organization billing remains intact.
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT conname, conrelid::regclass AS relation,
        pg_get_constraintdef(oid) AS definition FROM pg_constraint
        WHERE contype='f' AND confdeltype<>'c' AND confrelid IN (
            'agents_service.drafts'::regclass,
            'agents_service.agent_sessions'::regclass,
            'agents_service.agent_turns'::regclass)
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', item.relation, item.conname);
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s ON DELETE CASCADE',
            item.relation, item.conname,
            regexp_replace(item.definition, ' ON DELETE (NO ACTION|RESTRICT|SET NULL|SET DEFAULT)', ''));
    END LOOP;
END $$;
