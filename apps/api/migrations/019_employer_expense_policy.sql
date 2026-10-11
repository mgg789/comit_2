-- Versioned tariffs apply only to sources created after this migration.
-- Existing ledger rows and their snapshots remain immutable.
CREATE OR REPLACE FUNCTION record_support_feedback_expense(owner uuid,vacancy uuid,decision uuid,explanation text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF decision IS NULL OR length(trim(explanation))<20 THEN RAISE EXCEPTION 'support_decision_required'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.accounts WHERE id=owner AND role='employer') THEN RAISE EXCEPTION 'employer_not_found'; END IF;
    IF vacancy IS NOT NULL AND NOT EXISTS(SELECT 1 FROM agents_service.drafts WHERE owner_id=owner AND vacancy_id=vacancy) THEN
        RAISE EXCEPTION 'vacancy_not_owned';
    END IF;
    INSERT INTO public.employer_expenses VALUES('support:'||decision,owner,vacancy,'penalty',39800,clock_timestamp(),
        jsonb_build_object('version','expense-v2','amount_kopeks',39800,'support_decision_id',decision),'feedback.support_decision') ON CONFLICT DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION record_support_feedback_expense(uuid,uuid,uuid,text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION account_recruitment_expense() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE p public.recruitment_processes; sent public.employer_expenses; amount bigint; category text; source text; policy_snapshot jsonb;
BEGIN
    IF NEW.type NOT IN ('invitation.sent','deadline.expired','accept') THEN RETURN NEW; END IF;
    SELECT * INTO STRICT p FROM public.recruitment_processes WHERE id=NEW.process_id;

    IF NEW.type='accept' THEN
        -- Only invitations sent under v2 carry an acceptance amount. Older invites
        -- retain their original 49 RUB total when accepted after this migration.
        SELECT * INTO sent FROM public.employer_expenses WHERE source_key='invitation:'||p.id;
        IF NOT FOUND OR sent.policy->>'version' IS DISTINCT FROM 'expense-v2' THEN RETURN NEW; END IF;
        amount := (sent.policy->>'accept_kopeks')::bigint;
        IF amount<=0 THEN RAISE EXCEPTION 'invitation_acceptance_tariff_invalid'; END IF;
        policy_snapshot := sent.policy || jsonb_build_object('amount_kopeks',amount,'phase','acceptance');
        INSERT INTO public.employer_expenses(source_key,owner_id,vacancy_id,category,amount_kopeks,occurred_at,policy,reason)
        VALUES('invitation-accept:'||p.id,sent.owner_id,sent.vacancy_id,'invitation',amount,NEW.created_at,policy_snapshot,'invitation.accepted')
        ON CONFLICT DO NOTHING;
        RETURN NEW;
    END IF;

    IF NEW.type='invitation.sent' THEN
        amount := 4900;
        category := 'invitation';
        source := 'invitation:'||p.id;
        policy_snapshot := jsonb_build_object('version','expense-v2','amount_kopeks',amount,
            'send_kopeks',4900,'accept_kopeks',14700,'total_kopeks',19600);
    ELSE
        amount := 39800;
        category := 'penalty';
        source := 'deadline:'||NEW.event_id;
        policy_snapshot := jsonb_build_object('version','expense-v2','amount_kopeks',amount);
    END IF;

    INSERT INTO public.employer_expenses(source_key,owner_id,vacancy_id,category,amount_kopeks,occurred_at,policy,reason)
    VALUES(source,p.owner_id,p.vacancy_id,category,amount,NEW.created_at,policy_snapshot,NEW.type) ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION account_recruitment_expense() FROM PUBLIC;

CREATE OR REPLACE FUNCTION account_publication_expense() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
    IF NEW.published_at IS NOT NULL THEN
        INSERT INTO public.employer_expenses(source_key,owner_id,vacancy_id,category,amount_kopeks,occurred_at,policy,reason)
        VALUES('publication:'||NEW.draft_id,NEW.owner_id,NEW.vacancy_id,'publication',39800,
            NEW.published_at,jsonb_build_object('version','expense-v2','amount_kopeks',39800),'vacancy.first_publication') ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION account_publication_expense() FROM PUBLIC;

-- New drafts are charged with the current policy when first published.
-- Existing publication rows are deliberately not replayed or rewritten.
