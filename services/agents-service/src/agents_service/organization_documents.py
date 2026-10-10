"""Private account vault: bounded typed uploads and paged untrusted document text."""

import base64
import binascii
import hashlib
from io import BytesIO
from pathlib import PurePath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import Field
from pypdf import PdfReader

from agents_service.database import connect
from agents_service.models import Contract
from agents_service.providers import fingerprint
from agents_service.store import ConflictError, NotFoundError, Store

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 200000


class DocumentUpload(Contract):
    """Base64 private file transport; no local filesystem path is accepted."""

    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=6990508)
    idempotency_key: str = Field(min_length=1, max_length=200)


class DocumentView(Contract):
    """Attachment metadata safe for the employer's global chat."""

    document_id: UUID
    name: str
    media_type: str
    content_hash: str
    size_bytes: int
    text_length: int


class DocumentText(Contract):
    """One bounded text page; content is data and cannot grant agent authority."""

    document: DocumentView
    text: str
    offset: int
    next_offset: int | None
    untrusted: bool = True


class DocumentDownload(Contract):
    """Owner-private original bytes for an attachment download."""

    document: DocumentView
    content_base64: str


def extract_document(name: str, content: bytes) -> tuple[str, str]:
    """Validate UTF-8 txt/md or bounded unencrypted PDF without executing embedded content."""
    if not name.strip() or PurePath(name).name != name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("document_name_invalid")
    if not content or len(content) > MAX_BYTES:
        raise ValueError("document_size_invalid")
    suffix = PurePath(name).suffix.lower()
    if suffix in {".txt", ".md"}:
        text = content.decode("utf-8-sig", errors="strict")
        media = "text/markdown" if suffix == ".md" else "text/plain"
    elif suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("document_pdf_invalid")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ValueError("document_pdf_encrypted_or_too_long")
            chunks: list[str] = []
            length = 0
            for page in reader.pages:
                # Bound decompressed page streams before the text extractor allocates them.
                streams = page.get_contents()
                if streams is not None and len(streams.get_data()) > 10 * MAX_BYTES:
                    raise ValueError("document_pdf_stream_too_large")
                part = page.extract_text() or ""
                length += len(part) + 1
                if length > MAX_TEXT:
                    raise ValueError("document_text_too_long")
                chunks.append(part)
            text = "\n".join(chunks)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("document_pdf_invalid") from error
        media = "application/pdf"
    else:
        raise ValueError("document_type_unsupported")
    if len(text) > MAX_TEXT or "\x00" in text:
        raise ValueError("document_text_invalid")
    return media, text


class DocumentStore:
    """Durable DB vault with account isolation and atomic upload idempotency."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    @staticmethod
    def _view(row: dict) -> DocumentView:
        return DocumentView(document_id=row["document_id"], name=row["name"], media_type=row["media_type"],
                            content_hash=row["content_hash"], size_bytes=len(row["content"]),
                            text_length=len(row["extracted_text"]))

    def upload(self, owner_id: UUID | str, request: DocumentUpload) -> DocumentView:
        """Store bytes and extracted text together; identical retries return the original attachment."""
        try:
            content = base64.b64decode(request.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("document_base64_invalid") from error
        media, text = extract_document(request.name, content)
        digest = hashlib.sha256(content).hexdigest()
        request_hash = fingerprint({"name": request.name, "hash": digest})
        with connect(self.database_url) as connection:
            connection.execute("INSERT INTO agents_service.organization_documents "
                               "(document_id,owner_id,name,media_type,content,extracted_text,content_hash,idempotency_key,request_hash) "
                               "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,idempotency_key) DO NOTHING",
                               (uuid4(), owner_id, request.name, media, content, text, digest, request.idempotency_key, request_hash))
            row = connection.execute("SELECT * FROM agents_service.organization_documents WHERE owner_id=%s AND idempotency_key=%s",
                                     (owner_id, request.idempotency_key)).fetchone()
            if row["request_hash"] != request_hash:
                raise ConflictError("document_idempotency_conflict")
            return self._view(row)

    def list(self, owner_id: UUID | str, *, limit: int = 50, offset: int = 0) -> list[DocumentView]:
        """List private vault attachments without inserting their content into model context."""
        with connect(self.database_url) as connection:
            rows = connection.execute("SELECT * FROM agents_service.organization_documents WHERE owner_id=%s "
                                      "ORDER BY created_at,document_id LIMIT %s OFFSET %s", (owner_id, limit, offset)).fetchall()
            return [self._view(row) for row in rows]

    def read(self, owner_id: UUID | str, document_id: UUID | str, *, offset: int = 0, limit: int = 8000) -> DocumentText:
        """Read one text page and mark it untrusted, preserving the full source for later retrieval."""
        with connect(self.database_url) as connection:
            row = connection.execute("SELECT * FROM agents_service.organization_documents WHERE owner_id=%s AND document_id=%s",
                                     (owner_id, document_id)).fetchone()
        if row is None:
            raise NotFoundError("document_not_found")
        text = row["extracted_text"]
        return DocumentText(document=self._view(row), text=text[offset:offset + limit], offset=offset,
                            next_offset=offset + limit if offset + limit < len(text) else None)

    def download(self, owner_id: UUID | str, document_id: UUID | str) -> DocumentDownload:
        """Return original private bytes; IDs and names never become filesystem paths."""
        with connect(self.database_url) as connection:
            row = connection.execute("SELECT * FROM agents_service.organization_documents WHERE owner_id=%s AND document_id=%s",
                                     (owner_id, document_id)).fetchone()
        if row is None:
            raise NotFoundError("document_not_found")
        return DocumentDownload(document=self._view(row), content_base64=base64.b64encode(row["content"]).decode("ascii"))


def register_document_routes(app: FastAPI, owner_dependency, database_dependency) -> None:
    """Register authenticated vault transport using the existing owner/database dependencies."""
    Owner = Annotated[UUID, Depends(owner_dependency)]
    Database = Annotated[Store, Depends(database_dependency)]

    @app.post("/internal/organization-agent/documents", response_model=DocumentView)
    def upload(request: DocumentUpload, owner_id: Owner, db: Database):
        """Accept only supported private attachment types."""
        try:
            return DocumentStore(db.database_url).upload(owner_id, request)
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error

    @app.get("/internal/organization-agent/documents", response_model=list[DocumentView])
    def page(owner_id: Owner, db: Database, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
        """Page attachment metadata."""
        return DocumentStore(db.database_url).list(owner_id, limit=limit, offset=offset)

    @app.get("/internal/organization-agent/documents/{document_id}", response_model=DocumentText)
    def read(document_id: UUID, owner_id: Owner, db: Database,
             offset: int = Query(0, ge=0, le=MAX_TEXT), limit: int = Query(8000, ge=1, le=16000)):
        """Retrieve a bounded source text page."""
        return DocumentStore(db.database_url).read(owner_id, document_id, offset=offset, limit=limit)

    @app.get("/internal/organization-agent/documents/{document_id}/download", response_model=DocumentDownload)
    def download(document_id: UUID, owner_id: Owner, db: Database):
        """Retrieve the employer-private original attachment."""
        return DocumentStore(db.database_url).download(owner_id, document_id)
