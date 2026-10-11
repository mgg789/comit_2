-- Hidden pair-scoped contact identities never enter the published vacancy pipeline.
ALTER TABLE recruitment_processes ADD COLUMN kind text NOT NULL DEFAULT 'vacancy' CHECK (kind IN ('vacancy','contact_only'));
ALTER TABLE recruitment_processes ADD CONSTRAINT contact_only_state CHECK (kind <> 'contact_only' OR (state IN ('accepted','rejected','withdrawn') AND deadline IS NULL));
CREATE FUNCTION account_direct_contact_expense() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE p public.recruitment_processes;
BEGIN
 IF NEW.type <> 'contact.started' THEN RETURN NEW; END IF;
 SELECT * INTO STRICT p FROM public.recruitment_processes WHERE id=NEW.process_id;
 IF p.kind <> 'contact_only' THEN RAISE EXCEPTION 'direct_contact_kind_required'; END IF;
 INSERT INTO public.employer_expenses(source_key,owner_id,vacancy_id,category,amount_kopeks,occurred_at,policy,reason)
 VALUES('contact:'||p.id,p.owner_id,NULL,'invitation',4900,NEW.created_at,jsonb_build_object('version','contact-v1','amount_kopeks',4900,'send_kopeks',4900,'accept_kopeks',0,'total_kopeks',4900),'contact.started') ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION account_direct_contact_expense() FROM PUBLIC;
CREATE TRIGGER direct_contact_expense AFTER INSERT ON recruitment_events FOR EACH ROW EXECUTE FUNCTION account_direct_contact_expense();
