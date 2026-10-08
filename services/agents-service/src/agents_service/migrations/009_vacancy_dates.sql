-- Creation time already exists; lifecycle dates must not derive from editorial updates.
ALTER TABLE agents_service.drafts
    ADD COLUMN published_at timestamptz,
    ADD COLUMN closed_at timestamptz;

-- Preserve known historical lifecycle events. Unknown dates deliberately remain NULL.
UPDATE agents_service.drafts AS draft
SET published_at = publication.published_at
FROM (
    SELECT draft_id, max(updated_at) AS published_at
    FROM agents_service.jobs
    WHERE kind = 'publication' AND state = 'succeeded'
      AND COALESCE(payload->>'status', 'open') = 'open'
    GROUP BY draft_id
) AS publication
WHERE draft.draft_id = publication.draft_id;

UPDATE agents_service.drafts AS draft
SET closed_at = closure.closed_at
FROM (
    SELECT draft_id, max(created_at) AS closed_at
    FROM agents_service.jobs
    WHERE kind = 'publication' AND payload->>'status' = 'closed'
    GROUP BY draft_id
) AS closure
WHERE draft.draft_id = closure.draft_id AND draft.business_status = 'closed';
