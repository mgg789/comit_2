-- Permanent UUID-only fence; private employer content is purged separately.
CREATE TABLE agents_service.employer_tombstones (
    owner_id uuid PRIMARY KEY,
    deleted_at timestamptz NOT NULL DEFAULT now()
);

-- Every owner-scoped INSERT/UPDATE shares the deletion lock, including workers
-- which acquired a lease before deletion. A retry cannot recreate the owner.
CREATE FUNCTION agents_service.guard_employer_lifecycle() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.owner_id::text, 714012));
    IF EXISTS(SELECT 1 FROM agents_service.employer_tombstones WHERE owner_id=NEW.owner_id) THEN
        RAISE EXCEPTION 'employer_deleted' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT table_name FROM information_schema.columns
        WHERE table_schema='agents_service' AND column_name='owner_id'
          AND table_name NOT IN ('employer_tombstones','vacancy_tombstones')
    LOOP
        EXECUTE format('CREATE TRIGGER employer_lifecycle_guard BEFORE INSERT OR UPDATE ON agents_service.%I FOR EACH ROW EXECUTE FUNCTION agents_service.guard_employer_lifecycle()', item.table_name);
    END LOOP;
END $$;
