CREATE TABLE vacancy_pipeline.tombstones (
    vacancy_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    source_revision bigint NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE vacancy_pipeline.lifecycle_fences (
    vacancy_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    source_revision bigint NOT NULL,
    business_status text NOT NULL CHECK (business_status IN ('draft','closed'))
);

-- Cascade only references to vacancy-owned state, never candidate/shared registry rows.
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT c.conname, c.conrelid::regclass AS relation,
        pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c
        JOIN pg_class target ON target.oid=c.confrelid
        JOIN pg_namespace ns ON ns.oid=target.relnamespace
        WHERE c.contype='f' AND c.confdeltype<>'c' AND (
            (ns.nspname='vacancy_pipeline' AND target.relname='vacancies') OR
            (ns.nspname='matching_pipeline' AND target.relname='runs') OR
            (ns.nspname='rerank_pipeline' AND target.relname='runs') OR
            (ns.nspname='feed_pipeline' AND target.relname IN ('sessions','exposures','batches')) OR
            (ns.nspname='hypothesis_pipeline' AND target.relname='controls'))
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', item.relation, item.conname);
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s ON DELETE CASCADE',
            item.relation, item.conname,
            regexp_replace(item.definition, ' ON DELETE (NO ACTION|RESTRICT|SET NULL|SET DEFAULT)', ''));
    END LOOP;
END $$;
