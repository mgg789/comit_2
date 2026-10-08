CREATE TABLE agents_service.organization_documents (
    document_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
    media_type text NOT NULL CHECK(media_type IN ('text/plain','text/markdown','application/pdf')),
    content bytea NOT NULL CHECK(octet_length(content) <= 5242880),
    extracted_text text NOT NULL CHECK(length(extracted_text) <= 200000),
    content_hash text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key)
);
CREATE INDEX organization_documents_owner ON agents_service.organization_documents(owner_id,created_at,document_id);
CREATE TABLE agents_service.organization_tool_receipts (
    owner_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(owner_id,idempotency_key)
);
