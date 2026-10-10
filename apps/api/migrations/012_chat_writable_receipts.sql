-- Existing immutable command outcomes remain valid, conservatively read-only
-- until clients fetch their current participant-authenticated process view.
UPDATE recruitment_receipts SET result=result || '{"chat_writable":false}'::jsonb
WHERE NOT (result ? 'chat_writable');
