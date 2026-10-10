-- City is now required for all vacancy types. Preserve raw upstream snapshots.
INSERT INTO vacancy_pipeline.outbox(event_type,vacancy_id,revision,schema_version,payload)
SELECT 'vacancy.withdrawn',r.vacancy_id,r.revision,r.schema_version,
       '{"reason":"city_required"}'::jsonb
FROM vacancy_pipeline.ready r JOIN vacancy_pipeline.vacancies v USING(vacancy_id)
WHERE COALESCE(btrim(v.profile->>'city'),'')='';

DELETE FROM vacancy_pipeline.ready r USING vacancy_pipeline.vacancies v
WHERE r.vacancy_id=v.vacancy_id AND COALESCE(btrim(v.profile->>'city'),'')='';

UPDATE vacancy_pipeline.jobs j SET state='obsolete',lease_token=NULL,lease_until=NULL,updated_at=now()
FROM vacancy_pipeline.vacancies v
WHERE j.vacancy_id=v.vacancy_id AND COALESCE(btrim(v.profile->>'city'),'')=''
      AND j.state IN ('queued','processing','retrying','failed');

UPDATE vacancy_pipeline.vacancies
SET state='draft',updated_at=now(),
    missing_requirements=CASE WHEN missing_requirements ? 'city' THEN missing_requirements
                             ELSE missing_requirements || '["city"]'::jsonb END
WHERE COALESCE(btrim(profile->>'city'),'')='';
