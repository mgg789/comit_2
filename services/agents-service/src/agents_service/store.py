"""Owner-scoped durable vacancy drafts, approval snapshots and worker jobs."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from .database import connect, migrate
from .models import (
    CreateRequest,
    DraftFields,
    DraftPage,
    DraftPatch,
    DraftView,
    FieldSource,
    JobKind,
    JobView,
    RevisionRequest,
    SchemaSnapshot,
    SummarySnapshot,
)


class StoreError(RuntimeError):
    """Base class for errors that the API maps to a stable response."""


class NotFoundError(StoreError):
    """The requested resource is absent or belongs to another owner."""


class ConflictError(StoreError):
    """A compare-and-swap, state, idempotency or registry precondition failed."""


class StaleRevisionError(ConflictError):
    """The client attempted to mutate a revision older than the stored row."""


class LeaseError(ConflictError):
    """A worker operation did not present the current unexpired lease token."""


class InvalidDraftError(ConflictError):
    """The draft cannot be approved because mandatory publication requirements are missing."""


def _json_bytes(value: object) -> bytes:
    """Serialize JSON deterministically for hashes and idempotency comparisons."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _body_hash(value: object) -> str:
    """Return the SHA-256 digest used to detect changed idempotent requests."""
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _uuid(value: UUID | str) -> UUID:
    """Normalize trusted UUID inputs before constructing SQL parameters."""
    return value if isinstance(value, UUID) else UUID(value)


class Store:
    """PostgreSQL repository for one owner-scoped draft and its durable worker jobs."""

    def __init__(self, database_url: str, *, migrate_schema: bool = False) -> None:
        """Create a repository; migration is opt-in so API startup controls lifecycle."""
        self.database_url = database_url
        if migrate_schema:
            migrate(database_url)

    @staticmethod
    def _canonical_fields(value: DraftFields | Mapping[str, Any] | None) -> DraftFields:
        """Parse a partial payload into the canonical full field shape."""
        parsed = value if isinstance(value, DraftFields) else DraftFields.model_validate(value or {})
        data = parsed.model_dump(by_alias=True)
        # A published matching snapshot always has the empty string bio and empty maps.
        data.setdefault("bio", "")
        return DraftFields.model_validate(data)

    @staticmethod
    def _patch_fields(value: DraftFields | Mapping[str, Any]) -> dict[str, Any]:
        """Extract exactly fields present in a patch, including explicit nulls."""
        parsed = value if isinstance(value, DraftFields) else DraftFields.model_validate(value)
        return parsed.model_dump(by_alias=True, exclude_unset=True)

    @staticmethod
    def _validate_coordinates(fields: DraftFields, schema: SchemaSnapshot) -> None:
        """Reject a coordinate key that is absent from the supplied registry snapshot."""
        known = {(item.group, item.key) for item in schema.coordinates}
        groups = (("domain", fields.domens), ("stack", fields.stacks), ("soft_skill", fields.soft_skills))
        for group, values in groups:
            for key in values:
                if (group, key) not in known:
                    raise ConflictError(f"unknown coordinate: {group}/{key}")

    @staticmethod
    def _missing(fields: DraftFields) -> list[str]:
        """Return the mandatory approval checklist in matching's established names."""
        return [
            name
            for name, present in (
                ("title", fields.title is not None),
                ("zp_min", fields.zp_min is not None),
                ("zp_max", fields.zp_max is not None),
                ("daily", fields.daily is not None),
                ("city", fields.city is not None),
                ("type", fields.type is not None),
                ("class_or_domens_or_stacks", bool(fields.class_key or fields.domens or fields.stacks)),
            )
            if not present
        ]

    @staticmethod
    def _source_map(raw: object) -> dict[str, FieldSource]:
        """Parse stored provenance, accepting the early string representation for compatibility."""
        if not isinstance(raw, dict):
            return {}
        result: dict[str, FieldSource] = {}
        for key, value in raw.items():
            result[key] = FieldSource(source=value if isinstance(value, str) else value.get("source", "system"),
                                      metadata={} if isinstance(value, str) else value.get("metadata", {}))
        return result

    @classmethod
    def _view(cls, row: Mapping[str, Any]) -> DraftView:
        """Convert a database row into the public typed draft response."""
        fields = cls._canonical_fields(row["fields"])
        summary = SummarySnapshot.model_validate(row["summary"]) if row.get("summary") else None
        publication = row.get("publication")
        return DraftView(
            draft_id=row["draft_id"], vacancy_id=row["vacancy_id"], owner_id=row["owner_id"],
            revision=row["revision"], state=row["state"], fields=fields,
            field_sources=cls._source_map(row.get("field_sources")),
            missing_requirements=list(row.get("missing_requirements") or []), summary=summary,
            publication=publication,
            business_status=row.get("business_status", "draft"), archived=row.get("deleted_at") is not None,
            created_at=row.get("created_at"), published_at=row.get("published_at"), closed_at=row.get("closed_at"),
        )

    @staticmethod
    def _job(row: Mapping[str, Any]) -> JobView:
        """Convert a database row into the public typed worker job response."""
        iso = lambda value: value.isoformat() if value is not None else None
        return JobView(
            job_id=row["job_id"], kind=row["kind"], owner_id=row["owner_id"], draft_id=row.get("draft_id"),
            session_id=row.get("session_id"), payload=row["payload"], state=row["state"],
            attempts=row["attempts"], max_attempts=row["max_attempts"], available_at=iso(row.get("available_at")),
            lease_token=row.get("lease_token"), lease_until=iso(row.get("lease_until")), result=row.get("result"),
            error=row.get("error"),
        )

    @staticmethod
    def _publication_payload(owner_id: UUID, revision: int, fields: DraftFields) -> dict[str, Any]:
        """Build the frozen full matching intake payload, retaining title in metadata only."""
        metadata = dict(fields.metadata)
        if fields.title is not None:
            metadata["title"] = fields.title
        return {
            "source_revision": revision,
            "owner_id": str(owner_id),
            "status": "open",
            "zp_min": fields.zp_min,
            "zp_max": fields.zp_max,
            "daily": fields.daily,
            "city": fields.city,
            "type": fields.type,
            "bio": fields.bio or "",
            "domens": dict(fields.domens),
            "stacks": dict(fields.stacks),
            "soft_skills": dict(fields.soft_skills),
            "class": fields.class_key,
            "metadata": metadata,
        }

    def _get_row(self, connection: psycopg.Connection, owner_id: UUID, draft_id: UUID, *, lock: bool = False) -> Mapping[str, Any]:
        """Fetch a draft under owner scope, optionally taking its row lock."""
        suffix = " FOR UPDATE" if lock else ""
        row = connection.execute(
            f"SELECT * FROM agents_service.drafts WHERE owner_id = %s AND draft_id = %s{suffix}",
            (owner_id, draft_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("draft not found")
        return row

    def _patch_tx(
        self,
        connection: psycopg.Connection,
        owner_id: UUID,
        draft_id: UUID,
        request: DraftPatch,
        schema: SchemaSnapshot,
        *,
        source: str,
        source_metadata: Mapping[str, JsonValue] | None,
    ) -> Mapping[str, Any]:
        """Apply one locked patch inside an existing transaction."""
        row = self._get_row(connection, owner_id, draft_id, lock=True)
        if row["revision"] != request.expected_revision:
            raise StaleRevisionError("draft revision is stale")
        if row["state"] in {"creating", "created", "failed"}:
            raise ConflictError("published drafts are immutable")
        incoming = self._patch_fields(request.fields)
        current = self._canonical_fields(row["fields"])
        current_data = current.model_dump(by_alias=True)
        sources = self._source_map(row.get("field_sources"))
        metadata = dict(source_metadata or {})
        for key, value in incoming.items():
            old = sources.get(key)
            if source == "laya" and old is not None and old.source in {"manual", "agent"}:
                continue
            current_data[key] = value
            sources[key] = FieldSource(source=source, metadata=metadata)
        merged = DraftFields.model_validate(current_data)
        self._validate_coordinates(merged, schema)
        revision = row["revision"] + 1
        source_json = {key: item.model_dump() for key, item in sources.items()}
        connection.execute(
            """
            UPDATE agents_service.drafts
            SET revision=%s, state='editing', fields=%s, field_sources=%s,
                missing_requirements=%s, summary=NULL, publication=NULL, updated_at=now()
            WHERE owner_id=%s AND draft_id=%s
            """,
            (revision, Jsonb(merged.model_dump(by_alias=True)), Jsonb(source_json), Jsonb(self._missing(merged)),
             owner_id, draft_id),
        )
        return self._get_row(connection, owner_id, draft_id)

    def create(self, owner_id: UUID | str, fields: DraftFields | Mapping[str, Any] | None, schema: SchemaSnapshot) -> DraftView:
        """Create one owner-scoped draft whose draft and vacancy identifiers are identical."""
        with connect(self.database_url) as connection:
            return self.create_in_connection(connection, owner_id, fields, schema)

    def create_in_connection(self, connection: psycopg.Connection, owner_id: UUID | str,
                             fields: DraftFields | Mapping[str, Any] | None, schema: SchemaSnapshot) -> DraftView:
        """Create a draft in the caller transaction so agent effects and receipts commit together."""
        owner = _uuid(owner_id)
        canonical = self._canonical_fields(fields)
        self._validate_coordinates(canonical, schema)
        draft_id = uuid4()
        present = fields.model_fields_set if isinstance(fields, DraftFields) else set((fields or {}).keys())
        sources = {key: {"source": "manual", "metadata": {}} for key in present if key != "class_key"}
        if "class" in present or "class_key" in present:
            sources["class"] = {"source": "manual", "metadata": {}}
        connection.execute(
            """
            INSERT INTO agents_service.drafts
                (draft_id, vacancy_id, owner_id, fields, field_sources, missing_requirements)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (draft_id, draft_id, owner, Jsonb(canonical.model_dump(by_alias=True)), Jsonb(sources),
             Jsonb(self._missing(canonical))),
        )
        row = self._get_row(connection, owner, draft_id)
        return self._view(row)

    def get(self, owner_id: UUID | str, draft_id: UUID | str) -> DraftView:
        """Read a draft only when the caller owns it."""
        with connect(self.database_url) as connection:
            row = self._get_row(connection, _uuid(owner_id), _uuid(draft_id))
        return self._view(row)

    def list(self, owner_id: UUID | str, *, limit: int = 50, offset: int = 0) -> list[DraftView]:
        """Return a bounded, owner-scoped page ordered by most recently updated."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        with connect(self.database_url) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agents_service.drafts
                WHERE owner_id = %s AND deleted_at IS NULL ORDER BY updated_at DESC, draft_id LIMIT %s OFFSET %s
                """, (_uuid(owner_id), limit, offset),
            ).fetchall()
        return [self._view(row) for row in rows]

    def page(self, owner_id: UUID | str, *, limit: int = 50, offset: int = 0, cursor: str | None = None) -> DraftPage:
        """Page immutable creation keys, preserving offset callers without mixing pagination modes."""
        owner = _uuid(owner_id)
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000 or cursor and offset:
            raise ConflictError("invalid_pagination")
        boundary = None
        if cursor:
            try:
                if len(cursor) > 1000:
                    raise ValueError("cursor length")
                value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if value["owner"] != str(owner) or value["v"] != 1:
                    raise ValueError("cursor scope")
                timestamp = datetime.fromisoformat(value["created"])
                if timestamp.tzinfo is None:
                    raise ValueError("cursor timezone")
                boundary = (timestamp, UUID(value["id"]))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                raise ConflictError("invalid_cursor") from None
        clause = "AND (created_at,draft_id) < (%s,%s)" if boundary else ""
        params = (owner, *(boundary or ()), limit + 1, offset)
        with connect(self.database_url) as connection:
            rows = connection.execute(f"""SELECT * FROM agents_service.drafts WHERE owner_id=%s
                AND deleted_at IS NULL {clause} ORDER BY created_at DESC,draft_id DESC LIMIT %s OFFSET %s""", params).fetchall()
        more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = None
        if more:
            last = selected[-1]
            value = {"v": 1, "owner": str(owner), "created": last["created_at"].isoformat(), "id": str(last["draft_id"])}
            next_cursor = base64.urlsafe_b64encode(_json_bytes(value)).decode().rstrip("=")
        return DraftPage(items=[self._view(row) for row in selected], limit=limit, has_more=more, next_cursor=next_cursor)

    def revise(self, owner_id: UUID | str, draft_id: UUID | str, request: DraftPatch, schema: SchemaSnapshot) -> DraftView:
        """Edit a published vacancy into a new draft; the previous publication remains until explicit publish."""
        with connect(self.database_url) as connection:
            return self.revise_in_connection(connection, owner_id, draft_id, request, schema)

    def revise_in_connection(self, connection: psycopg.Connection, owner_id: UUID | str, draft_id: UUID | str, request: DraftPatch, schema: SchemaSnapshot) -> DraftView:
        """Stage published vacancy edits in the same transaction as the agent tool receipt."""
        owner, draft = _uuid(owner_id), _uuid(draft_id)
        row = self._get_row(connection, owner, draft, lock=True)
        if row["deleted_at"] is not None or row["state"] in {"creating", "failed"}:
            raise ConflictError("vacancy_not_editable")
        if row["revision"] != request.expected_revision:
            raise StaleRevisionError("draft revision is stale")
        if row["state"] == "created":
            connection.execute("UPDATE agents_service.drafts SET state='editing' WHERE draft_id=%s", (draft,))
        changed = self._patch_tx(connection, owner, draft, request, schema, source="manual", source_metadata=None)
        return self._view(changed)

    def close(self, owner_id: UUID | str, draft_id: UUID | str, request: RevisionRequest, *, archive: bool = False) -> DraftView:
        """CAS-close or archive while preserving audit and durably enqueueing a higher closed source revision."""
        owner, draft = _uuid(owner_id), _uuid(draft_id)
        with connect(self.database_url) as connection:
            row = self._get_row(connection, owner, draft, lock=True)
            if row["revision"] != request.expected_revision:
                raise StaleRevisionError("draft revision is stale")
            if row["state"] in {"creating", "failed"}:
                raise ConflictError("publication_pending_or_failed")
            if row["deleted_at"] is not None:
                raise ConflictError("vacancy_archived")
            revision = row["revision"] + 1
            publication = row.get("publication")
            state = "editing"
            if row.get("published_fields") is not None:
                fields = self._canonical_fields(row["published_fields"])
                payload = self._publication_payload(owner, revision, fields)
                payload["status"] = "closed"
                job = self._enqueue_tx(connection, owner_id=owner, kind="publication", payload=payload,
                    idempotency_key=f"closure:{draft}:{revision}", draft_id=draft, session_id=None, max_attempts=5)
                publication = {"status": "pending", "source_revision": revision, "job_id": str(job["job_id"])}
                state = "creating"
            connection.execute("""UPDATE agents_service.drafts SET revision=%s,business_status='closed',state=%s,
                summary=NULL,publication=%s,
                closed_at=CASE WHEN business_status='closed' THEN closed_at ELSE now() END,
                deleted_at=CASE WHEN %s THEN now() ELSE deleted_at END,updated_at=now()
                WHERE owner_id=%s AND draft_id=%s""", (revision, state, Jsonb(publication) if publication else None, archive, owner, draft))
            changed = self._get_row(connection, owner, draft)
        return self._view(changed)

    def patch(
        self,
        owner_id: UUID | str,
        draft_id: UUID | str,
        payload: DraftPatch | Mapping[str, Any],
        schema: SchemaSnapshot,
        *,
        source: str = "manual",
        source_metadata: Mapping[str, JsonValue] | None = None,
    ) -> DraftView:
        """CAS-update fields and invalidate an approved snapshot whenever content changes."""
        request = payload if isinstance(payload, DraftPatch) else DraftPatch.model_validate(payload)
        if source not in {"manual", "agent", "laya", "system", "fsp"}:
            raise ValueError("unsupported field source")
        with connect(self.database_url) as connection:
            updated = self._patch_tx(
                connection, _uuid(owner_id), _uuid(draft_id), request, schema,
                source=source, source_metadata=source_metadata,
            )
        return self._view(updated)

    def approve(self, owner_id: UUID | str, draft_id: UUID | str, request: RevisionRequest | Mapping[str, Any], schema: SchemaSnapshot) -> DraftView:
        """Seal the exact current revision as an immutable typed approval snapshot."""
        guard = request if isinstance(request, RevisionRequest) else RevisionRequest.model_validate(request)
        owner, draft = _uuid(owner_id), _uuid(draft_id)
        with connect(self.database_url) as connection:
            updated = self._approve_tx(connection, owner, draft, guard, schema)
        return self._view(updated)

    def _approve_tx(
        self,
        connection: psycopg.Connection,
        owner_id: UUID,
        draft_id: UUID,
        request: RevisionRequest,
        schema: SchemaSnapshot,
    ) -> Mapping[str, Any]:
        """Approve one draft while retaining the caller's transaction boundary."""
        row = self._get_row(connection, owner_id, draft_id, lock=True)
        if row["revision"] != request.expected_revision:
            raise StaleRevisionError("draft revision is stale")
        if row["state"] == "summary":
            current_summary = SummarySnapshot.model_validate(row["summary"])
            if (current_summary.strict_schema_version == schema.version
                    and current_summary.strict_schema_hash == schema.schema_hash):
                return row
            # Registry rollover requires a fresh seal even when field revision is unchanged.
        if row["state"] in {"creating", "created", "failed"}:
            raise ConflictError("published draft cannot be approved again")
        fields = self._canonical_fields(row["fields"])
        self._validate_coordinates(fields, schema)
        missing = self._missing(fields)
        if missing:
            raise InvalidDraftError("missing requirements: " + ",".join(missing))
        summary_data = {
            "revision": row["revision"],
            "fields": fields.model_dump(by_alias=True),
            "strict_schema_version": schema.version,
            "strict_schema_hash": schema.schema_hash,
        }
        digest = _body_hash(summary_data)
        summary = SummarySnapshot(
            revision=row["revision"], summary_hash=digest, fields=fields,
            strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash,
        )
        connection.execute(
            "UPDATE agents_service.drafts SET state='summary', summary=%s, updated_at=now() WHERE owner_id=%s AND draft_id=%s",
            (Jsonb(summary.model_dump(by_alias=True)), owner_id, draft_id),
        )
        return self._get_row(connection, owner_id, draft_id)

    def patch_in_connection(
        self,
        connection: psycopg.Connection,
        owner_id: UUID | str,
        draft_id: UUID | str,
        expected_revision: int,
        fields: DraftFields | Mapping[str, Any],
        schema: SchemaSnapshot,
        *,
        source: str = "agent",
        source_metadata: Mapping[str, JsonValue] | None = None,
    ) -> DraftView:
        """Apply an agent/form patch without committing the caller's journal transaction."""
        request = DraftPatch(
            expected_revision=expected_revision,
            fields=fields if isinstance(fields, DraftFields) else DraftFields.model_validate(fields),
        )
        row = self._patch_tx(
            connection, _uuid(owner_id), _uuid(draft_id), request, schema,
            source=source, source_metadata=source_metadata,
        )
        return self._view(row)

    def approve_in_connection(
        self,
        connection: psycopg.Connection,
        owner_id: UUID | str,
        draft_id: UUID | str,
        expected_revision: int,
        schema: SchemaSnapshot,
    ) -> DraftView:
        """Approve without committing so an agent journal can share the same transaction."""
        row = self._approve_tx(connection, _uuid(owner_id), _uuid(draft_id), RevisionRequest(expected_revision=expected_revision), schema)
        return self._view(row)

    def _enqueue_tx(
        self,
        connection: psycopg.Connection,
        *,
        owner_id: UUID,
        kind: JobKind,
        payload: Mapping[str, Any],
        idempotency_key: str | None,
        draft_id: UUID | None,
        session_id: UUID | None,
        max_attempts: int,
    ) -> Mapping[str, Any]:
        """Insert or return one idempotent job inside the caller's transaction."""
        digest = _body_hash(payload)
        if idempotency_key is not None:
            existing = connection.execute(
                "SELECT * FROM agents_service.jobs WHERE owner_id=%s AND kind=%s AND idempotency_key=%s FOR UPDATE",
                (owner_id, kind, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["body_hash"] != digest:
                    raise ConflictError("idempotency key was reused with a different payload")
                return existing
        job_id = uuid4()
        connection.execute(
            """
            INSERT INTO agents_service.jobs
                (job_id, kind, owner_id, draft_id, session_id, idempotency_key, body_hash, payload, max_attempts)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (owner_id, kind, idempotency_key) DO NOTHING
            """,
            (job_id, kind, owner_id, draft_id, session_id, idempotency_key, digest, Jsonb(dict(payload)), max_attempts),
        )
        inserted = connection.execute("SELECT * FROM agents_service.jobs WHERE job_id=%s", (job_id,)).fetchone()
        if inserted is not None:
            return inserted
        existing = connection.execute(
            "SELECT * FROM agents_service.jobs WHERE owner_id=%s AND kind=%s AND idempotency_key=%s FOR UPDATE",
            (owner_id, kind, idempotency_key),
        ).fetchone()
        if existing is None or existing["body_hash"] != digest:
            raise ConflictError("idempotency key was reused with a different payload")
        return existing

    def submit(self, owner_id: UUID | str, draft_id: UUID | str, request: CreateRequest | Mapping[str, Any], schema: SchemaSnapshot) -> DraftView:
        """Record the human publication confirmation and enqueue one frozen matching intake."""
        with connect(self.database_url) as connection:
            return self.submit_in_connection(connection, owner_id, draft_id, request, schema)

    def submit_in_connection(self, connection: psycopg.Connection, owner_id: UUID | str, draft_id: UUID | str, request: CreateRequest | Mapping[str, Any], schema: SchemaSnapshot) -> DraftView:
        """Enqueue frozen publication atomically with an agent tool receipt."""
        create = request if isinstance(request, CreateRequest) else CreateRequest.model_validate(request)
        owner, draft = _uuid(owner_id), _uuid(draft_id)
        row = self._get_row(connection, owner, draft, lock=True)
        existing_pub = row.get("publication") or {}
        if row.get("deleted_at") is not None:
            raise ConflictError("vacancy_archived")
        if row["state"] in {"creating", "created", "failed"}:
            if row["revision"] == create.expected_revision and existing_pub.get("summary_hash") == create.summary_hash:
                return self._view(row)
            raise ConflictError("publication request does not match the current snapshot")
        if row["revision"] != create.expected_revision:
            raise StaleRevisionError("draft revision is stale")
        if row["state"] != "summary" or not row.get("summary"):
            raise ConflictError("draft must be approved before publication")
        summary = SummarySnapshot.model_validate(row["summary"])
        if summary.summary_hash != create.summary_hash:
            raise ConflictError("summary hash does not match the approved snapshot")
        if summary.strict_schema_version != schema.version or summary.strict_schema_hash != schema.schema_hash:
            raise ConflictError("matching schema changed since approval")
        fields = self._canonical_fields(summary.fields)
        publication_payload = self._publication_payload(owner, row["revision"], fields)
        job = self._enqueue_tx(
            connection, owner_id=owner, kind="publication", payload=publication_payload,
            idempotency_key=f"publication:{draft}:{row['revision']}:{summary.summary_hash}",
            draft_id=draft, session_id=None, max_attempts=5,
        )
        publication = {
            "status": "pending", "source_revision": row["revision"], "summary_hash": summary.summary_hash,
            "job_id": str(job["job_id"]),
        }
        connection.execute(
            "UPDATE agents_service.drafts SET state='creating', business_status='open', publication=%s, updated_at=now() WHERE owner_id=%s AND draft_id=%s",
            (Jsonb(publication), owner, draft),
        )
        updated = self._get_row(connection, owner, draft)
        return self._view(updated)

    def apply_enrichment(
        self,
        owner_id: UUID | str,
        draft_id: UUID | str,
        expected_revision: int,
        fields: Mapping[str, Any] | DraftFields,
        schema: SchemaSnapshot,
        *,
        input_hash: str,
        model_version: str,
        schema_version: int,
        schema_hash: str,
        provenance: Mapping[str, JsonValue] | None = None,
    ) -> DraftView:
        """Apply a Laya suggestion through the normal CAS path and preserve human fields."""
        if schema_version != schema.version or schema_hash != schema.schema_hash:
            raise ConflictError("enrichment schema changed")
        return self.patch(
            owner_id,
            draft_id,
            DraftPatch(expected_revision=expected_revision, fields=fields if isinstance(fields, DraftFields) else DraftFields.model_validate(fields)),
            schema,
            source="laya",
            source_metadata={
                "input_hash": input_hash,
                "model_version": model_version,
                "schema_version": schema_version,
                "schema_hash": schema_hash,
                **dict(provenance or {}),
            },
        )

    def apply_enrichment_job(
        self,
        job_id: UUID | str,
        lease_token: UUID | str,
        expected_revision: int,
        fields: Mapping[str, Any] | DraftFields,
        schema: SchemaSnapshot,
        *,
        input_hash: str,
        model_version: str,
        schema_version: int,
        schema_hash: str,
        result: Mapping[str, Any] | None = None,
        provenance: Mapping[str, JsonValue] | None = None,
    ) -> DraftView:
        """Apply Laya fields and acknowledge its lease atomically in one transaction."""
        if schema_version != schema.version or schema_hash != schema.schema_hash:
            raise ConflictError("enrichment schema changed")
        token, job_uuid = _uuid(lease_token), _uuid(job_id)
        metadata = {
            "input_hash": input_hash,
            "model_version": model_version,
            "schema_version": schema_version,
            "schema_hash": schema_hash,
            **dict(provenance or {}),
        }
        request = DraftPatch(
            expected_revision=expected_revision,
            fields=fields if isinstance(fields, DraftFields) else DraftFields.model_validate(fields),
        )
        with connect(self.database_url) as connection:
            job = connection.execute(
                """
                SELECT * FROM agents_service.jobs
                WHERE job_id=%s AND state='processing' AND lease_token=%s AND lease_until > now()
                FOR UPDATE
                """, (job_uuid, token),
            ).fetchone()
            if job is None or job["kind"] != "enrichment":
                raise LeaseError("job lease is stale or not an enrichment job")
            if job["draft_id"] is None:
                raise ConflictError("enrichment job has no draft")
            updated_draft = self._patch_tx(
                connection, job["owner_id"], job["draft_id"], request, schema,
                source="laya", source_metadata=metadata,
            )
            connection.execute(
                """
                UPDATE agents_service.jobs
                SET state='succeeded', result=%s, lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE job_id=%s
                """, (Jsonb(dict(result or {})), job_uuid),
            )
        return self._view(updated_draft)

    def enqueue_job(
        self,
        owner_id: UUID | str,
        kind: JobKind,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        draft_id: UUID | str | None = None,
        session_id: UUID | str | None = None,
        max_attempts: int = 5,
    ) -> JobView:
        """Enqueue a generic durable job, deduplicating equal owner/key/body tuples."""
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be 1..20")
        owner = _uuid(owner_id)
        with connect(self.database_url) as connection:
            row = self._enqueue_tx(
                connection, owner_id=owner, kind=kind, payload=payload,
                idempotency_key=idempotency_key, draft_id=_uuid(draft_id) if draft_id else None,
                session_id=_uuid(session_id) if session_id else None, max_attempts=max_attempts,
            )
        return self._job(row)

    def enqueue_in_connection(
        self,
        connection: psycopg.Connection,
        owner_id: UUID | str,
        kind: JobKind,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        draft_id: UUID | str | None = None,
        session_id: UUID | str | None = None,
        max_attempts: int = 5,
    ) -> JobView:
        """Enqueue while retaining the caller's transaction for a journaled side effect."""
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be 1..20")
        row = self._enqueue_tx(
            connection, owner_id=_uuid(owner_id), kind=kind, payload=payload,
            idempotency_key=idempotency_key, draft_id=_uuid(draft_id) if draft_id else None,
            session_id=_uuid(session_id) if session_id else None, max_attempts=max_attempts,
        )
        return self._job(row)

    def retry_job(self, owner_id: UUID | str, job_id: UUID | str) -> JobView:
        """Manually requeue a failed job and restore a failed publication to creating state."""
        owner, job_uuid = _uuid(owner_id), _uuid(job_id)
        with connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT * FROM agents_service.jobs WHERE owner_id=%s AND job_id=%s FOR UPDATE",
                (owner, job_uuid),
            ).fetchone()
            if row is None:
                raise NotFoundError("job not found")
            if row["state"] not in {"failed", "retrying"}:
                raise ConflictError("only failed or retrying jobs can be manually retried")
            updated = connection.execute(
                """
                UPDATE agents_service.jobs
                SET state='queued', attempts=0, available_at=now(), error=NULL,
                    lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE job_id=%s RETURNING *
                """, (job_uuid,),
            ).fetchone()
            if row["kind"] == "publication" and row["draft_id"] is not None:
                connection.execute(
                    """
                    UPDATE agents_service.drafts
                    SET state='creating', publication=jsonb_set(COALESCE(publication,'{}'::jsonb), '{status}', '"pending"'::jsonb), updated_at=now()
                    WHERE draft_id=%s AND state='failed'
                    """, (row["draft_id"],),
                )
        return self._job(updated)

    def get_job(self, owner_id: UUID | str, job_id: UUID | str) -> JobView:
        """Read one owner-scoped durable job."""
        with connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT * FROM agents_service.jobs WHERE owner_id=%s AND job_id=%s",
                (_uuid(owner_id), _uuid(job_id)),
            ).fetchone()
            if row is None:
                raise NotFoundError("job not found")
        return self._job(row)

    def claim_job(self, kind: JobKind | None = None, *, lease_seconds: int = 60) -> JobView | None:
        """Claim the next available job with a fresh token and bounded lease."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be 1..3600")
        with connect(self.database_url) as connection:
            quarantined = connection.execute(
                """
                UPDATE agents_service.jobs
                SET state='failed', error='max_attempts_exceeded', lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE attempts >= max_attempts
                  AND ((state IN ('queued','retrying') AND available_at <= now())
                    OR (state='processing' AND lease_until <= now()))
                RETURNING draft_id, kind
                """
            ).fetchall()
            for poisoned in quarantined:
                if poisoned["kind"] == "publication" and poisoned["draft_id"] is not None:
                    connection.execute(
                        """
                        UPDATE agents_service.drafts
                        SET state='failed', publication=jsonb_set(
                            jsonb_set(COALESCE(publication,'{}'::jsonb), '{status}', '"failed"'::jsonb),
                            '{error}', to_jsonb('max_attempts_exceeded'::text)), updated_at=now()
                        WHERE draft_id=%s AND state='creating'
                        """,
                        (poisoned["draft_id"],),
                    )
            params: tuple[Any, ...] = (kind,) if kind else ()
            kind_clause = "AND kind=%s" if kind else ""
            row = connection.execute(
                f"""
                SELECT * FROM agents_service.jobs
                WHERE ((state IN ('queued','retrying') AND available_at <= now())
                    OR (state='processing' AND lease_until <= now()))
                  AND attempts < max_attempts {kind_clause}
                ORDER BY available_at, created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
                """, params,
            ).fetchone()
            if row is None:
                return None
            token = uuid4()
            updated = connection.execute(
                """
                UPDATE agents_service.jobs
                SET state='processing', attempts=attempts+1, lease_token=%s,
                    lease_until=now() + (%s * interval '1 second'), updated_at=now()
                WHERE job_id=%s RETURNING *
                """, (token, lease_seconds, row["job_id"]),
            ).fetchone()
        return self._job(updated)

    def heartbeat_job(self, job_id: UUID | str, lease_token: UUID | str, *, lease_seconds: int = 60) -> JobView:
        """Extend a lease only when its exact token is still current and unexpired."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be 1..3600")
        with connect(self.database_url) as connection:
            row = connection.execute(
                """
                UPDATE agents_service.jobs SET lease_until=now() + (%s * interval '1 second'), updated_at=now()
                WHERE job_id=%s AND state='processing' AND lease_token=%s AND lease_until > now()
                RETURNING *
                """, (lease_seconds, _uuid(job_id), _uuid(lease_token)),
            ).fetchone()
            if row is None:
                raise LeaseError("job lease is stale or expired")
        return self._job(row)

    def succeed_job(self, job_id: UUID | str, lease_token: UUID | str, result: Mapping[str, Any] | None = None) -> JobView:
        """Complete a leased job and atomically mark its publication draft as created."""
        job_uuid, token = _uuid(job_id), _uuid(lease_token)
        with connect(self.database_url) as connection:
            row = connection.execute(
                """
                UPDATE agents_service.jobs
                SET state='succeeded', result=%s, lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE job_id=%s AND state='processing' AND lease_token=%s AND lease_until > now()
                RETURNING *
                """, (Jsonb(dict(result or {})), job_uuid, token),
            ).fetchone()
            if row is None:
                raise LeaseError("job lease is stale or expired")
            if row["kind"] == "publication" and row["draft_id"] is not None:
                matching_status = str((result or {}).get("status", (result or {}).get("state", "accepted")))
                connection.execute(
                    """
                    UPDATE agents_service.drafts
                    SET state='created', business_status=%s,
                        published_fields=CASE WHEN %s='open' THEN fields ELSE published_fields END,
                        published_at=CASE WHEN %s='open' THEN now() ELSE published_at END,
                        publication=jsonb_set(
                        jsonb_set(COALESCE(publication,'{}'::jsonb), '{status}', '"accepted"'::jsonb),
                        '{matching_status}', to_jsonb(%s::text)),
                        updated_at=now()
                    WHERE draft_id=%s AND state='creating' AND revision=%s
                    """, (row["payload"].get("status", "open"), row["payload"].get("status", "open"),
                        row["payload"].get("status", "open"),
                        matching_status, row["draft_id"], row["payload"]["source_revision"]),
                )
        return self._job(row)

    def fail_job(
        self,
        job_id: UUID | str,
        lease_token: UUID | str,
        error: str,
        *,
        retryable: bool = True,
    ) -> JobView:
        """Fail a leased job, retrying with bounded exponential backoff until max attempts."""
        if not error or len(error) > 500:
            raise ValueError("error must be 1..500 characters")
        job_uuid, token = _uuid(job_id), _uuid(lease_token)
        with connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT * FROM agents_service.jobs WHERE job_id=%s FOR UPDATE", (job_uuid,)
            ).fetchone()
            if row is None or row["state"] != "processing" or row["lease_token"] != token or row["lease_until"] <= datetime.now(timezone.utc):
                raise LeaseError("job lease is stale or expired")
            terminal = not retryable or row["attempts"] >= row["max_attempts"]
            if terminal:
                state, delay = "failed", 0
            else:
                state, delay = "retrying", min(300, 2 ** max(0, row["attempts"] - 1))
            updated = connection.execute(
                """
                UPDATE agents_service.jobs SET state=%s, error=%s, available_at=now() + (%s * interval '1 second'),
                    lease_token=NULL, lease_until=NULL, updated_at=now() WHERE job_id=%s RETURNING *
                """, (state, error, delay, job_uuid),
            ).fetchone()
            if terminal and row["kind"] == "publication" and row["draft_id"] is not None:
                connection.execute(
                    """
                    UPDATE agents_service.drafts
                    SET state='failed', publication=jsonb_set(jsonb_set(COALESCE(publication,'{}'::jsonb), '{status}', '"failed"'::jsonb), '{error}', to_jsonb(%s::text)), updated_at=now()
                    WHERE draft_id=%s AND state='creating'
                    """, (error, row["draft_id"]),
                )
        return self._job(updated)

    def succeed_publication(self, job_id: UUID | str, lease_token: UUID | str, result: Mapping[str, Any] | None = None) -> JobView:
        """Explicit publication alias used by the matching worker integration."""
        return self.succeed_job(job_id, lease_token, result)

    def fail_publication(self, job_id: UUID | str, lease_token: UUID | str, error: str, *, retryable: bool = True) -> JobView:
        """Explicit publication alias retaining generic lease and retry semantics."""
        return self.fail_job(job_id, lease_token, error, retryable=retryable)
