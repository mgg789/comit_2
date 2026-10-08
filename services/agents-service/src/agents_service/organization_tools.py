"""Owner-scoped tools used by the organization worker.

The registry is intentionally small and boring: local editorial tools execute in
the agents database, while cross-service tools go through the API bridge.  Tool
arguments are validated before either path is touched and all local mutations
use the same receipt fence as their business write.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urljoin

import httpx
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .database import connect
from .models import (
    CreateRequest,
    DraftFields,
    DraftPatch,
    SchemaSnapshot,
)
from .organization_documents import DocumentStore, DocumentView, extract_document
from .providers import MatchingClient
from .store import ConflictError, NotFoundError, Store


class OrganizationToolError(RuntimeError):
    """Stable error raised for invalid, unavailable, or conflicting tool calls."""

    def __init__(self, code: str, *, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class ToolArgs(BaseModel):
    """Strict base class for all worker-facing tool arguments."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EmptyArgs(ToolArgs):
    """Arguments for a no-argument read."""


class VacancyListArgs(ToolArgs):
    """Bounded owner vacancy page."""

    limit: int = Field(default=50, strict=True, ge=1, le=100)
    offset: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    cursor: str | None = Field(default=None, max_length=1000)


class VacancyIdArgs(ToolArgs):
    """One owner-scoped vacancy identifier."""

    vacancy_id: uuid.UUID


class VacancyCreateArgs(ToolArgs):
    """Create an editable vacancy draft."""

    fields: DraftFields = Field(default_factory=DraftFields)


class VacancyEditArgs(VacancyIdArgs):
    """Compare-and-swap vacancy edit."""

    expected_revision: int = Field(strict=True, ge=1, le=2**53 - 1)
    fields: DraftFields = Field(default_factory=DraftFields)


class VacancyRevisionArgs(VacancyIdArgs):
    """Compare-and-swap approval argument."""

    expected_revision: int = Field(strict=True, ge=1, le=2**53 - 1)


class VacancyPublishArgs(VacancyRevisionArgs):
    """Human-confirmed publication of one exact approved summary."""

    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_confirmation: Literal[True]


class DocumentListArgs(ToolArgs):
    """Bounded private attachment listing."""

    limit: int = Field(default=50, strict=True, ge=1, le=100)
    offset: int = Field(default=0, strict=True, ge=0, le=1_000_000)


class DocumentReadArgs(ToolArgs):
    """Bounded private attachment text page."""

    document_id: uuid.UUID
    offset: int = Field(default=0, strict=True, ge=0, le=200_000)
    limit: int = Field(default=8_000, strict=True, ge=1, le=16_000)


class DocumentCreateMdArgs(ToolArgs):
    """Create a private Markdown attachment from UTF-8 text."""

    name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=200_000)


class KnowledgePageArgs(ToolArgs):
    """Read a packaged UI knowledge page by safe slug."""

    page: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class JevSwipeToolArgs(ToolArgs):
    """Bounded Jev swipe input; provider and usage controls are server-owned."""

    vacancy_id: uuid.UUID
    count: int = Field(strict=True, ge=1, le=50)
    comment: str = Field(default="", max_length=4_000)


class BridgeArgs(ToolArgs):
    """Base for bridge arguments; concrete bridge contracts are deliberately explicit."""


class BridgeVacancyArgs(BridgeArgs):
    """Bridge feed input."""

    vacancy_id: uuid.UUID


class BridgeCandidateArgs(BridgeArgs):
    """Bridge candidate input."""

    candidate_id: uuid.UUID


class BridgeProcessArgs(BridgeArgs):
    """Bridge recruitment process input."""

    process_id: uuid.UUID


class BridgeFeedArgs(BridgeVacancyArgs):
    """Read a bounded employer feed."""

    include_reserve: bool = False
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class BridgeFeedContinueArgs(BridgeVacancyArgs):
    """Continue an existing feed session."""

    session_id: uuid.UUID


class BridgeStatsArgs(BridgeArgs):
    """Optional statistics date range."""

    from_: str | None = Field(default=None, alias="from", max_length=10)
    to: str | None = Field(default=None, max_length=10)


class BridgeVacancyStatsArgs(BridgeStatsArgs):
    """Vacancy statistics input."""

    vacancy_id: uuid.UUID


class BridgeRecruitmentListArgs(BridgeArgs):
    """Bounded recruitment list."""

    limit: int = Field(default=50, strict=True, ge=1, le=100)
    offset: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    cursor: str | None = Field(default=None, max_length=1024)
    draft_id: uuid.UUID | None = None


class BridgeRecruitmentGetArgs(BridgeProcessArgs):
    """Read one recruitment process."""


class BridgeSearchArgs(BridgeArgs):
    """Candidate search input."""

    q: str = Field(default="", max_length=500)
    filters: dict[str, Any] | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)


class BridgeFeedDecisionArgs(BridgeVacancyArgs):
    """One guarded feed decision."""

    exposure_id: uuid.UUID
    action: Literal["like", "dislike"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class BridgeFeedImpressionArgs(BridgeVacancyArgs):
    """One feed impression."""

    exposure_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class BridgeChatReadArgs(BridgeProcessArgs):
    """Delegated candidate chat history read."""

    delegation_id: uuid.UUID
    task_id: uuid.UUID | None = None
    after_sequence: int = Field(default=0, strict=True, ge=0)
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class BridgeChatSendArgs(BridgeProcessArgs):
    """Delegated candidate chat message.

    Sending has a deliberately separate schema from history reads: the bridge
    rejects pagination fields on this operation and accepts only the proof and
    message fields needed to perform the guarded write.
    """

    delegation_id: uuid.UUID
    task_id: uuid.UUID | None = None
    lease_token: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=16_000)


class BridgeInterviewProposeArgs(BridgeProcessArgs):
    """Delegated interview proposal."""

    expected_revision: int = Field(strict=True, ge=1)
    kind: Literal["technical", "general"]
    starts_at: str = Field(min_length=1, max_length=64)
    time_zone: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(strict=True, ge=1, le=480)
    format: Literal["remote", "onsite"]
    location: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    # Workflow callers may provide these proofs inside arguments.  The bridge
    # wire contract carries them in the envelope, so dispatch strips them from
    # the nested arguments after validation.
    task_id: uuid.UUID | None = None
    delegation_id: uuid.UUID | None = None
    lease_token: uuid.UUID | None = None


class _SchemaProvider(Protocol):
    def schema(self) -> SchemaSnapshot: ...


def _jsonable(value: Any) -> Any:
    """Convert Pydantic values into stable JSON and psycopg-compatible values."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: Any) -> bytes:
    """Serialize tool payloads canonically for request hashes and receipts."""
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: Any) -> str:
    """Return a SHA-256 digest for idempotency conflict detection."""
    return hashlib.sha256(_canonical(value)).hexdigest()


def _result(value: Any) -> Any:
    """Return a JSON-safe result without leaking model internals."""
    return _jsonable(value)


@dataclass
class _ToolDefinition:
    name: str
    category: str
    description: str
    args: type[ToolArgs]


class ToolRegistry:
    """Progressively discoverable, owner-scoped organization tool registry."""

    def __init__(
        self,
        database_url: str,
        *,
        store: Store | None = None,
        documents: DocumentStore | None = None,
        matching: _SchemaProvider | None = None,
        api_url: str | None = None,
        bridge_url: str | None = None,
        internal_token: str | None = None,
        transport: httpx.Client | None = None,
        knowledge_root: str | Path | None = None,
        jev: Any | None = None,
    ) -> None:
        self.database_url = database_url
        self.store = store or Store(database_url)
        self.documents = documents or DocumentStore(database_url)
        self.matching = matching or MatchingClient()
        self.api_url = (api_url or bridge_url or os.getenv("ORGANIZATION_API_URL") or os.getenv("API_URL", "")).rstrip("/")
        self.internal_token = internal_token or os.getenv("AGENTS_SERVICE_TOKEN", "")
        self.transport = transport or httpx.Client(timeout=15, follow_redirects=False)
        self.knowledge_root = Path(knowledge_root) if knowledge_root else Path(__file__).with_name("knowledge")
        self.jev = jev

    @staticmethod
    def _definitions() -> tuple[_ToolDefinition, ...]:
        """Return the stable catalog, including bridge tools without loading schemas."""
        local = (
            _ToolDefinition("vacancy_list", "vacancy", "List this employer's vacancy drafts.", VacancyListArgs),
            _ToolDefinition("vacancy_get", "vacancy", "Read one owner-scoped vacancy.", VacancyIdArgs),
            _ToolDefinition("vacancy_create", "vacancy", "Create an editable vacancy draft.", VacancyCreateArgs),
            _ToolDefinition("vacancy_edit", "vacancy", "Edit a vacancy with an exact revision.", VacancyEditArgs),
            _ToolDefinition("vacancy_approve", "vacancy", "Approve the exact vacancy revision.", VacancyRevisionArgs),
            _ToolDefinition("vacancy_publish", "vacancy", "Publish an exact approved summary after human confirmation.", VacancyPublishArgs),
            _ToolDefinition("taxonomy", "vacancy", "Read the current matching taxonomy.", EmptyArgs),
            _ToolDefinition("documents_list", "documents", "List private organization attachments.", DocumentListArgs),
            _ToolDefinition("documents_read", "documents", "Read a bounded page from a private attachment.", DocumentReadArgs),
            _ToolDefinition("documents_create_md", "documents", "Create a private Markdown attachment.", DocumentCreateMdArgs),
            _ToolDefinition("knowledge_page_read", "knowledge", "Read one packaged UI knowledge page.", KnowledgePageArgs),
            _ToolDefinition("jev_swipe", "jev", "Classify and apply a bounded candidate swipe.", JevSwipeToolArgs),
        )
        bridge: tuple[_ToolDefinition, ...] = (
            _ToolDefinition("organization_stats", "bridge", "Read owner organization statistics.", BridgeStatsArgs),
            _ToolDefinition("vacancy_stats", "bridge", "Read one vacancy's statistics.", BridgeVacancyStatsArgs),
            _ToolDefinition("recruitment_list", "bridge", "List owner recruitment processes.", BridgeRecruitmentListArgs),
            _ToolDefinition("recruitment_get", "bridge", "Read one recruitment process.", BridgeRecruitmentGetArgs),
            _ToolDefinition("candidate_showcase_search", "bridge", "Search the candidate showcase.", BridgeSearchArgs),
            _ToolDefinition("candidate_context_search", "bridge", "Search candidate context.", BridgeSearchArgs),
            _ToolDefinition("candidate_showcase_detail", "bridge", "Read one candidate showcase record.", BridgeCandidateArgs),
            _ToolDefinition("candidate_context_detail", "bridge", "Read one candidate context record.", BridgeCandidateArgs),
            _ToolDefinition("candidate_feed", "bridge", "Read a vacancy candidate feed.", BridgeFeedArgs),
            _ToolDefinition("candidate_feed_continue", "bridge", "Continue a candidate feed session.", BridgeFeedContinueArgs),
            _ToolDefinition("feed_decision", "bridge", "Record one guarded feed decision.", BridgeFeedDecisionArgs),
            _ToolDefinition("feed_impression", "bridge", "Record one guarded feed impression.", BridgeFeedImpressionArgs),
            _ToolDefinition("candidate_chat_read", "bridge", "Read a delegated candidate chat.", BridgeChatReadArgs),
            _ToolDefinition("candidate_chat_send", "bridge", "Send a delegated candidate chat message.", BridgeChatSendArgs),
            _ToolDefinition("interview_propose", "bridge", "Propose a delegated interview.", BridgeInterviewProposeArgs),
            _ToolDefinition("interview_accept", "bridge", "Candidate acceptance is owned by the candidate session.", EmptyArgs),
        )
        return local + bridge

    @classmethod
    def _definition(cls, name: str) -> _ToolDefinition:
        """Resolve one catalog name without accepting arbitrary callables."""
        for definition in cls._definitions():
            if definition.name == name:
                return definition
        raise OrganizationToolError("tool_not_found", status=404)

    def catalog(self) -> list[dict[str, str]]:
        """Return all names and descriptions without exposing argument schemas."""
        return [{"name": item.name, "category": item.category, "description": item.description}
                for item in self._definitions()]

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """Return OpenAI function definitions only for explicitly requested tools."""
        if not isinstance(names, list) or len(names) > 64:
            raise OrganizationToolError("invalid_tool_names", status=422)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            if not isinstance(name, str) or name in seen:
                if not isinstance(name, str):
                    raise OrganizationToolError("invalid_tool_names", status=422)
                continue
            seen.add(name)
            definition = self._definition(name)
            schema = definition.args.model_json_schema(by_alias=True)
            result.append({"type": "function", "function": {"name": name,
                           "description": definition.description, "parameters": schema}})
        return result

    def _parse(self, definition: _ToolDefinition, arguments: Mapping[str, Any]) -> ToolArgs:
        """Validate one strict argument object before opening a database or HTTP call."""
        try:
            if not isinstance(arguments, Mapping):
                raise TypeError
            return definition.args.model_validate(dict(arguments))
        except (ValidationError, TypeError):
            raise OrganizationToolError("invalid_tool_arguments", status=422) from None

    def _receipt_effect(self, owner_id: str, key: str, operation: str, args: ToolArgs,
                        effect: Callable[[Any], Any]) -> Any:
        """Run a local mutation and its replay receipt under one transaction/lock."""
        request_hash = _hash({"operation": operation, "arguments": args})
        # PostgreSQL advisory locks take a signed bigint.  Eight digest bytes are enough
        # for a per-owner/key serialization fence and avoid interpolating user input.
        lock_value = int.from_bytes(hashlib.sha256(f"{owner_id}:{key}".encode()).digest()[:8], "big", signed=True)
        try:
            with connect(self.database_url) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_value,))
                prior = connection.execute(
                    "SELECT request_hash,result FROM agents_service.organization_tool_receipts "
                    "WHERE owner_id=%s AND idempotency_key=%s FOR UPDATE", (owner_id, key),
                ).fetchone()
                if prior is not None:
                    if prior["request_hash"] != request_hash:
                        raise OrganizationToolError("idempotency_key_conflict", status=409)
                    return prior["result"]
                value = _result(effect(connection))
                connection.execute(
                    "INSERT INTO agents_service.organization_tool_receipts(owner_id,idempotency_key,request_hash,result) "
                    "VALUES(%s,%s,%s,%s)", (owner_id, key, request_hash, Jsonb(value)),
                )
                return value
        except OrganizationToolError:
            raise
        except NotFoundError as error:
            raise OrganizationToolError(str(error), status=404) from error
        except ConflictError as error:
            raise OrganizationToolError(str(error), status=409) from error
        except ValueError as error:
            raise OrganizationToolError(str(error), status=422) from error

    def _bridge(self, owner_id: str, name: str, arguments: ToolArgs, key: str, task_id: str | None,
                delegation_id: str | None = None, lease_token: str | None = None) -> Any:
        """Call the fixed configured API bridge with the owner and replay envelope."""
        if not self.api_url or not self.internal_token:
            raise OrganizationToolError("organization_bridge_unavailable", status=503)
        # The public worker contract accepts human-readable keys; the Fastify
        # bridge deliberately accepts UUIDs only.  Namespacing keeps retries
        # deterministic while avoiding collisions between two bridge operations.
        try:
            wire_key = str(uuid.UUID(key))
        except (ValueError, AttributeError):
            wire_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"comit:organization-bridge:{owner_id}:{name}:{key}"))
        wire_arguments = _jsonable(arguments)
        if isinstance(wire_arguments, dict):
            # Proofs are envelope fields on the Fastify bridge.  Keeping them
            # nested would be rejected by its per-tool allow-list.
            for proof in ("task_id", "delegation_id", "lease_token"):
                wire_arguments.pop(proof, None)
        if isinstance(wire_arguments, dict) and "idempotency_key" in wire_arguments:
            wire_arguments["idempotency_key"] = wire_key
        payload = {"arguments": wire_arguments, "idempotency_key": wire_key}
        if task_id is not None:
            payload["task_id"] = task_id
        if delegation_id is not None:
            payload["delegation_id"] = delegation_id
        if lease_token is not None:
            payload["lease_token"] = lease_token
        try:
            response = self.transport.post(
                urljoin(self.api_url + "/", f"internal/organization-agent/tools/{name}"),
                headers={"X-Internal-Token": self.internal_token, "X-Owner-ID": owner_id,
                         "content-type": "application/json"}, json=payload,
            )
        except httpx.HTTPError as error:
            raise OrganizationToolError("organization_bridge_unavailable", status=503) from error
        try:
            body = response.json()
        except ValueError as error:
            raise OrganizationToolError("organization_bridge_invalid_response", status=503) from error
        if response.status_code >= 400:
            code = body.get("detail", "organization_bridge_rejected") if isinstance(body, dict) else "organization_bridge_rejected"
            raise OrganizationToolError(str(code), status=response.status_code)
        if not isinstance(body, dict) or "result" not in body:
            raise OrganizationToolError("organization_bridge_invalid_response", status=503)
        return body["result"]

    def _create_document_in_connection(self, connection: Any, owner_id: str, args: DocumentCreateMdArgs,
                                       key: str) -> DocumentView:
        """Store Markdown bytes directly on the receipt connection for atomicity."""
        if not args.name.lower().endswith(".md"):
            raise OrganizationToolError("document_type_unsupported", status=422)
        content = args.content.encode("utf-8")
        media, text = extract_document(args.name, content)
        digest = hashlib.sha256(content).hexdigest()
        request_hash = _hash({"name": args.name, "content_hash": digest})
        existing = connection.execute(
            "SELECT * FROM agents_service.organization_documents WHERE owner_id=%s AND idempotency_key=%s FOR UPDATE",
            (owner_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise OrganizationToolError("document_idempotency_conflict", status=409)
            return self.documents._view(existing)
        connection.execute(
            "INSERT INTO agents_service.organization_documents "
            "(document_id,owner_id,name,media_type,content,extracted_text,content_hash,idempotency_key,request_hash) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid.uuid4(), owner_id, args.name, media, content, text, digest, key, request_hash),
        )
        row = connection.execute("SELECT * FROM agents_service.organization_documents WHERE owner_id=%s AND idempotency_key=%s",
                                 (owner_id, key)).fetchone()
        assert row is not None
        return self.documents._view(row)

    def dispatch(self, owner_id: str, name: str, arguments: dict[str, Any], idempotency_key: str,
                 task_id: str | None = None, delegation_id: str | None = None,
                 lease_token: str | None = None) -> dict[str, Any]:
        """Validate and execute one tool, replaying local mutations by owner/key."""
        definition = self._definition(name)
        supplied = dict(arguments)
        if task_id is not None and name in {"candidate_chat_read", "candidate_chat_send"}:
            supplied.setdefault("task_id", task_id)
        if task_id is not None and name == "interview_propose":
            supplied.setdefault("task_id", task_id)
        if delegation_id is not None and name in {"candidate_chat_read", "candidate_chat_send"}:
            supplied.setdefault("delegation_id", delegation_id)
        if delegation_id is not None and name == "interview_propose":
            supplied.setdefault("delegation_id", delegation_id)
        if lease_token is not None and name == "candidate_chat_send":
            supplied.setdefault("lease_token", lease_token)
        if lease_token is not None and name == "interview_propose":
            supplied.setdefault("lease_token", lease_token)
        args = self._parse(definition, supplied)
        try:
            owner = str(uuid.UUID(owner_id))
        except (ValueError, AttributeError, TypeError):
            raise OrganizationToolError("invalid_owner", status=422) from None
        key = str(idempotency_key)
        if not key or len(key) > 200:
            raise OrganizationToolError("invalid_idempotency_key", status=422)
        if definition.category == "bridge":
            # Arguments may carry workflow proofs (the runtime wrapper uses
            # that form); use them as envelope values when explicit dispatch
            # parameters were omitted.
            if name in {"candidate_chat_read", "candidate_chat_send", "interview_propose"}:
                task_id = task_id or (str(args.task_id) if getattr(args, "task_id", None) else None)
                delegation_id = delegation_id or (str(args.delegation_id) if getattr(args, "delegation_id", None) else None)
            if name in {"candidate_chat_send", "interview_propose"}:
                lease_token = lease_token or (str(args.lease_token) if getattr(args, "lease_token", None) else None)
            return _result(self._bridge(owner, name, args, key, task_id, delegation_id, lease_token))
        if name == "jev_swipe":
            jev = self.jev
            if jev is None:
                from .organization_jev import OrganizationJev
                jev = OrganizationJev(self)
            return _result(jev.swipe(owner, _jsonable(args), idempotency_key=key, task_id=task_id))
        if name == "vacancy_list":
            value = self.store.page(owner, limit=args.limit, offset=args.offset, cursor=args.cursor)  # type: ignore[attr-defined]
            return _result(value)
        if name == "vacancy_get":
            return _result(self.store.get(owner, args.vacancy_id))  # type: ignore[attr-defined]
        if name == "taxonomy":
            return _result(self.matching.schema())
        if name == "vacancy_create":
            schema = self.matching.schema()
            value = self._receipt_effect(owner, key, name, args, lambda connection: self.store.create_in_connection(connection, owner, args.fields, schema))
        elif name == "vacancy_edit":
            schema = self.matching.schema()
            value = self._receipt_effect(owner, key, name, args, lambda connection: self.store.revise_in_connection(
                connection, owner, args.vacancy_id, DraftPatch(expected_revision=args.expected_revision, fields=args.fields), schema))
        elif name == "vacancy_approve":
            schema = self.matching.schema()
            value = self._receipt_effect(owner, key, name, args, lambda connection: self.store.approve_in_connection(
                connection, owner, args.vacancy_id, args.expected_revision, schema))
        elif name == "vacancy_publish":
            schema = self.matching.schema()
            value = self._receipt_effect(owner, key, name, args, lambda connection: self.store.submit_in_connection(
                connection, owner, args.vacancy_id, CreateRequest(expected_revision=args.expected_revision,
                summary_hash=args.summary_hash), schema))
        elif name == "documents_list":
            value = self.documents.list(owner, limit=args.limit, offset=args.offset)
        elif name == "documents_read":
            value = self.documents.read(owner, args.document_id, offset=args.offset, limit=args.limit)
        elif name == "documents_create_md":
            value = self._receipt_effect(owner, key, name, args, lambda connection: self._create_document_in_connection(connection, owner, args, key))
        elif name == "knowledge_page_read":
            path = (self.knowledge_root / f"{args.page}.md").resolve()
            root = self.knowledge_root.resolve()
            if root not in path.parents:
                raise OrganizationToolError("knowledge_page_not_found", status=404)
            try:
                value = {"page": args.page, "content": path.read_text(encoding="utf-8"), "untrusted": True}
            except OSError as error:
                raise OrganizationToolError("knowledge_page_not_found", status=404) from error
        else:  # pragma: no cover - catalog and dispatch are kept together intentionally.
            raise OrganizationToolError("tool_not_implemented", status=503)
        return _result(value)
