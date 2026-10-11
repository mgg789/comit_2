-- Durable support-chat attachments. Files are account-owned until linked to a
-- notification; both rows are removed automatically with the account/message.
CREATE TABLE system_chat_files (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    idempotency_key uuid NOT NULL,
    request_hash text NOT NULL CHECK(length(request_hash)=64),
    name text NOT NULL CHECK(length(name) BETWEEN 1 AND 255),
    media_type text NOT NULL CHECK(length(media_type) BETWEEN 3 AND 127),
    size_bytes integer NOT NULL CHECK(size_bytes BETWEEN 1 AND 8388608),
    sha256 text NOT NULL CHECK(length(sha256)=64),
    content bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(account_id,idempotency_key)
);

CREATE INDEX system_chat_files_cleanup_idx ON system_chat_files(created_at);

CREATE TABLE system_notification_files (
    notification_id uuid NOT NULL REFERENCES system_notifications(id) ON DELETE CASCADE,
    file_id uuid NOT NULL REFERENCES system_chat_files(id) ON DELETE CASCADE,
    ordinal smallint NOT NULL CHECK(ordinal BETWEEN 0 AND 7),
    PRIMARY KEY(notification_id,file_id),
    UNIQUE(notification_id,ordinal)
);

CREATE INDEX system_notification_files_file_idx ON system_notification_files(file_id);
