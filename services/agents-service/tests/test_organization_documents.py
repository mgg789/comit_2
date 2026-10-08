"""Private vault contracts and isolated PostgreSQL upload/retrieval acceptance."""

import base64
import os
from io import BytesIO
from uuid import uuid4

import pytest
from agents_service.database import migrate
from agents_service.organization_documents import (
    DocumentStore,
    DocumentUpload,
    extract_document,
)
from agents_service.store import ConflictError, NotFoundError
from pypdf import PdfWriter


def test_supported_text_and_path_rejection():
    """Read strict UTF-8 and reject path traversal and unsupported binary files."""
    assert extract_document("policy.md", "Пожелания\n".encode()) == ("text/markdown", "Пожелания\n")
    for name, content in [("../policy.md", b"data"), ("C:\\policy.md", b"data"),
                          ("policy.exe", b"data"), ("bad.txt", b"\xff"), ("empty.md", b"")]:
        with pytest.raises(ValueError):
            extract_document(name, content)


def test_pdf_extract_and_encryption_rejection():
    """The extractor accepts bounded PDFs and never decrypts private password-protected files."""
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    assert extract_document("sample.pdf", buffer.getvalue()) == ("application/pdf", "")
    writer.encrypt("synthetic-password")
    encrypted = BytesIO()
    writer.write(encrypted)
    with pytest.raises(ValueError, match="encrypted"):
        extract_document("encrypted.pdf", encrypted.getvalue())


@pytest.mark.integration
def test_private_idempotent_durable_vault():
    """Owner isolation, conflicting retry and paged retrieval survive a new store instance."""
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(url)
    owner, other = uuid4(), uuid4()
    store = DocumentStore(url)
    request = DocumentUpload(name="report.md", content_base64=base64.b64encode(b"0123456789").decode(),
                             idempotency_key="document-one")
    first = store.upload(owner, request)
    assert store.upload(owner, request) == first
    page = DocumentStore(url).read(owner, first.document_id, limit=4)
    assert page.text == "0123" and page.next_offset == 4 and page.untrusted
    assert store.list(other) == []
    with pytest.raises(NotFoundError):
        store.download(other, first.document_id)
    with pytest.raises(ConflictError):
        store.upload(owner, request.model_copy(update={"name": "different.md"}))
    assert store.download(owner, first.document_id).content_base64 == request.content_base64
