-- Immutable accounting; no payment instrument or debit side effects.
CREATE TABLE employer_expenses (
    source_key text PRIMARY KEY,
    owner_id uuid NOT NULL,
    vacancy_id uuid,
    category text NOT NULL CHECK(category IN ('publication','invitation','ai','penalty')),
    amount_kopeks bigint NOT NULL CHECK(amount_kopeks >= 0),
    occurred_at timestamptz NOT NULL,
    policy jsonb NOT NULL,
    reason text NOT NULL
);
CREATE INDEX employer_expenses_owner_period ON employer_expenses(owner_id,occurred_at);
CREATE TABLE employer_expense_ai_days (
    owner_id uuid NOT NULL,
    moscow_date date NOT NULL,
    consumed_micro_usd numeric(30,6) NOT NULL DEFAULT 0,
    billed_kopeks bigint NOT NULL DEFAULT 0,
    PRIMARY KEY(owner_id,moscow_date)
);

-- Support alone records adjudicated feedback penalties. No employer route invokes this function.
CREATE FUNCTION record_support_feedback_expense(owner uuid,vacancy uuid,decision uuid,explanation text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF decision IS NULL OR length(trim(explanation))<20 THEN RAISE EXCEPTION 'support_decision_required'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.accounts WHERE id=owner AND role='employer') THEN RAISE EXCEPTION 'employer_not_found'; END IF;
    IF vacancy IS NOT NULL AND NOT EXISTS(SELECT 1 FROM agents_service.drafts WHERE owner_id=owner AND vacancy_id=vacancy) THEN
        RAISE EXCEPTION 'vacancy_not_owned';
    END IF;
    INSERT INTO public.employer_expenses VALUES('support:'||decision,owner,vacancy,'penalty',19900,clock_timestamp(),
        jsonb_build_object('version','expense-v1','amount_kopeks',19900,'support_decision_id',decision),'feedback.support_decision') ON CONFLICT DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION record_support_feedback_expense(uuid,uuid,uuid,text) FROM PUBLIC;

-- Trigger execution is transactional and source identity makes retries harmless.
CREATE FUNCTION account_recruitment_expense() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE p public.recruitment_processes; amount bigint; category text; source text;
BEGIN
    IF NEW.type NOT IN ('invitation.sent','deadline.expired') THEN RETURN NEW; END IF;
    SELECT * INTO STRICT p FROM public.recruitment_processes WHERE id=NEW.process_id;
    amount := CASE WHEN NEW.type='invitation.sent' THEN 4900 ELSE 19900 END;
    category := CASE WHEN NEW.type='invitation.sent' THEN 'invitation' ELSE 'penalty' END;
    source := CASE WHEN NEW.type='invitation.sent' THEN 'invitation:'||p.id ELSE 'deadline:'||NEW.event_id END;
    INSERT INTO public.employer_expenses VALUES(source,p.owner_id,p.vacancy_id,category,amount,NEW.created_at,
        jsonb_build_object('version','expense-v1','amount_kopeks',amount),NEW.type) ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
CREATE TRIGGER recruitment_expense AFTER INSERT ON recruitment_events FOR EACH ROW EXECUTE FUNCTION account_recruitment_expense();

CREATE FUNCTION account_publication_expense() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
    IF NEW.published_at IS NOT NULL THEN
        INSERT INTO public.employer_expenses VALUES('publication:'||NEW.draft_id,NEW.owner_id,NEW.vacancy_id,'publication',19900,
            NEW.published_at,jsonb_build_object('version','expense-v1','amount_kopeks',19900),'vacancy.first_publication') ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION account_ai_expense() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE day date; accumulated numeric; previous bigint; charged bigint;
BEGIN
    day := (NEW.created_at AT TIME ZONE 'Europe/Moscow')::date;
    -- Row-level UPSERT lock serializes simultaneous settled calls for the same Moscow day.
    INSERT INTO public.employer_expense_ai_days(owner_id,moscow_date) VALUES(NEW.owner_id,day) ON CONFLICT DO NOTHING;
    SELECT consumed_micro_usd,billed_kopeks INTO accumulated,previous FROM public.employer_expense_ai_days
        WHERE owner_id=NEW.owner_id AND moscow_date=day FOR UPDATE;
    IF EXISTS(SELECT 1 FROM public.employer_expenses WHERE source_key='ai:'||NEW.ledger_id) THEN RETURN NEW; END IF;
    accumulated := accumulated+NEW.total_micro_usd;
    -- 100 RUB/USD, daily allowance 10 RUB. Round only accumulated excess, never each call.
    charged := ceil(greatest(0,accumulated*0.01-1000));
    INSERT INTO public.employer_expenses VALUES('ai:'||NEW.ledger_id,NEW.owner_id,NULL,'ai',charged-previous,NEW.created_at,
        jsonb_build_object('version','expense-v1','usd_rub',100,'daily_allowance_kopeks',1000,'moscow_date',day,
            'provider_micro_usd',NEW.total_micro_usd),'ai.settled');
    UPDATE public.employer_expense_ai_days SET consumed_micro_usd=accumulated,billed_kopeks=charged
        WHERE owner_id=NEW.owner_id AND moscow_date=day;
    RETURN NEW;
END $$;

-- Source schemas can be migrated before or after API; installation is deployment-only.
CREATE FUNCTION install_employer_expense_sources() RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE record record;
BEGIN
    IF to_regclass('agents_service.drafts') IS NOT NULL THEN
        EXECUTE 'DROP TRIGGER IF EXISTS publication_expense ON agents_service.drafts';
        EXECUTE 'CREATE TRIGGER publication_expense AFTER INSERT OR UPDATE OF published_at ON agents_service.drafts FOR EACH ROW EXECUTE FUNCTION public.account_publication_expense()';
        EXECUTE $query$INSERT INTO public.employer_expenses
            SELECT 'publication:'||draft_id,owner_id,vacancy_id,'publication',19900,published_at,
                jsonb_build_object('version','expense-v1','amount_kopeks',19900,'historical',true),'vacancy.first_publication'
            FROM agents_service.drafts WHERE published_at IS NOT NULL ON CONFLICT DO NOTHING$query$;
    END IF;
    IF to_regclass('agents_service.organization_usage_ledger') IS NOT NULL THEN
        EXECUTE 'DROP TRIGGER IF EXISTS ai_expense ON agents_service.organization_usage_ledger';
        EXECUTE 'CREATE TRIGGER ai_expense AFTER INSERT ON agents_service.organization_usage_ledger FOR EACH ROW EXECUTE FUNCTION public.account_ai_expense()';
        EXECUTE 'CREATE TRIGGER ai_expense_backfill AFTER UPDATE OF ledger_id ON agents_service.organization_usage_ledger FOR EACH ROW EXECUTE FUNCTION public.account_ai_expense()';
        -- Existing settled records are replayed in chronological order through the same trigger.
        FOR record IN EXECUTE 'SELECT ledger_id FROM agents_service.organization_usage_ledger ORDER BY created_at,ledger_id' LOOP
            EXECUTE 'UPDATE agents_service.organization_usage_ledger SET ledger_id=ledger_id WHERE ledger_id=$1' USING record.ledger_id;
        END LOOP;
        EXECUTE 'DROP TRIGGER ai_expense_backfill ON agents_service.organization_usage_ledger';
    END IF;
END $$;
SELECT install_employer_expense_sources();
INSERT INTO employer_expenses
    SELECT CASE WHEN e.type='invitation.sent' THEN 'invitation:'||p.id ELSE 'deadline:'||e.event_id END,
        p.owner_id,p.vacancy_id,CASE WHEN e.type='invitation.sent' THEN 'invitation' ELSE 'penalty' END,
        CASE WHEN e.type='invitation.sent' THEN 4900 ELSE 19900 END,e.created_at,
        jsonb_build_object('version','expense-v1','historical',true),e.type
    FROM recruitment_events e JOIN recruitment_processes p ON p.id=e.process_id
    WHERE e.type IN ('invitation.sent','deadline.expired') ON CONFLICT DO NOTHING;
REVOKE ALL ON FUNCTION account_recruitment_expense(),account_publication_expense(),account_ai_expense(),install_employer_expense_sources() FROM PUBLIC;
