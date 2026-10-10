ALTER TABLE agents_service.drafts ADD COLUMN business_status text NOT NULL DEFAULT 'draft'
  CHECK(business_status IN ('draft','open','closed'));
ALTER TABLE agents_service.drafts ADD COLUMN published_fields jsonb;
ALTER TABLE agents_service.drafts ADD COLUMN deleted_at timestamptz;
UPDATE agents_service.drafts SET business_status='open', published_fields=fields
  WHERE state='created' AND publication->>'status'='accepted';
CREATE INDEX agents_service_drafts_keyset ON agents_service.drafts(owner_id,created_at DESC,draft_id DESC)
  WHERE deleted_at IS NULL;
