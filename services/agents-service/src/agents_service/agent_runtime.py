"""Reusable durable tool-calling runtime for vacancy agents.

The runtime is deliberately provider- and draft-store-neutral.  A worker claims a
turn with :meth:`run_turn`; all provider messages and tool calls are written before
the next step, so a process restart can resume without inventing an answer or
repeating a journaled mutation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from importlib.resources import files
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from .agent_models import (
    MAX_TOOL_ARGUMENT_BYTES,
    AgentDefinition,
    AgentInteraction,
    AgentLimits,
    AgentStatusView,
    AgentTurn,
    InteractionOption,
    InteractionResponse,
    ProviderCall,
    ProviderUsage,
    ToolResult,
    UserTurnRequest,
)
from .models import DraftFields, SchemaSnapshot
from .providers import ProviderError, fingerprint
from .store import (
    ConflictError,
    LeaseError,
    NotFoundError,
    StaleRevisionError,
    StoreError,
)


class DraftGateway(Protocol):
    """CAS draft operations supplied by the parent API's draft store."""

    def get_draft(self, owner_id: UUID, draft_id: UUID) -> Any:
        """Return the current owner-scoped draft view."""

    def patch_fields(self, owner_id: UUID, draft_id: UUID, expected_revision: int, fields: DraftFields) -> Any:
        """Apply one validated field patch with optimistic concurrency."""

    def approve(self, owner_id: UUID, draft_id: UUID, expected_revision: int) -> Any:
        """Freeze the current draft summary and return its view."""


class Provider(Protocol):
    """Minimal DeepSeek-compatible non-thinking tool-calling provider."""

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int = 1536) -> dict[str, Any]:
        """Return one raw provider response; transport errors must be raised."""


class MatchingSchema(Protocol):
    """Direct matching taxonomy access; no memory/jev lookup is permitted."""

    def schema(self) -> Any:
        """Return the current matching schema snapshot."""


class ToolArguments(BaseModel):
    """Strict base for model-generated tool arguments."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SetFieldsArguments(ToolArguments):
    """Allowed shared-form field patch; metadata and unknown keys are excluded."""

    fields: dict[str, JsonValue] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def allowed_fields(self) -> SetFieldsArguments:
        allowed = {"title", "class", "bio", "city", "daily", "type", "zp_min", "zp_max", "domens", "stacks", "soft_skills"}
        unknown = set(self.fields) - allowed
        if unknown:
            raise ValueError(f"unsupported draft fields: {sorted(unknown)}")
        return self


class ApproveArguments(ToolArguments):
    """Approval arguments; publication/create has intentionally no agent tool."""

    expected_revision: int = Field(strict=True, ge=1)


class InteractionArguments(ToolArguments):
    """Arguments for a typed interaction shown in the employer UI."""

    kind: str = Field(pattern=r"^(buttons|single_select|multi_select|slider)$")
    prompt: str = Field(min_length=1, max_length=1_000)
    field_target: str | None = Field(default=None, max_length=120)
    options: list[InteractionOption] = Field(default_factory=list, max_length=32)
    min_value: int | None = Field(default=None, strict=True, ge=0, le=7)
    max_value: int | None = Field(default=None, strict=True, ge=0, le=7)

    @model_validator(mode="after")
    def allowed_target(self) -> InteractionArguments:
        """Keep interaction controls bound to the same employer form fields."""
        allowed = {"title", "class", "bio", "city", "daily", "type", "zp_min", "zp_max", "domens", "stacks", "soft_skills"}
        coordinate = bool(self.field_target and re.fullmatch(r"(domens|stacks|soft_skills)\.[a-z][a-z0-9_-]{0,99}", self.field_target))
        if self.field_target is not None and self.field_target not in allowed and not coordinate:
            raise ValueError("unsupported interaction field target")
        if self.kind == "slider" and self.field_target is not None and not coordinate:
            raise ValueError("skill slider requires a coordinate field target")
        if self.kind == "multi_select" and self.field_target is not None and self.field_target not in {"domens", "stacks", "soft_skills"}:
            raise ValueError("multi select field target must be a skill group")
        return self


class AgentRuntimeError(RuntimeError):
    """Safe structured runtime failure that can be exposed as a durable error code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def build_tool_schemas() -> list[dict[str, Any]]:
    """Derive provider argument schemas from the same validated editorial models."""
    patch = DraftFields.model_json_schema(mode="validation")
    allowed = {"title", "class", "bio", "city", "daily", "type", "zp_min", "zp_max", "domens", "stacks", "soft_skills"}
    fields = {"type": "object", "properties": {key: value for key, value in patch["properties"].items() if key in allowed},
              "additionalProperties": False, "minProperties": 1}
    set_fields = {"type": "object", "required": ["fields"], "properties": {"fields": fields},
                  "additionalProperties": False, "$defs": patch.get("$defs", {})}
    contracts = [
        ("get_draft", "Read the current shared vacancy draft.", ToolArguments.model_json_schema()),
        ("get_taxonomy", "Read all current domain, stack and soft-skill coordinates.", ToolArguments.model_json_schema()),
        ("set_fields", "Fill or edit exactly the same vacancy form fields.", set_fields),
        ("approve", "Validate and show the common summary. Human Create publishes later.", ApproveArguments.model_json_schema()),
        ("show_interaction", "Return a typed question or field control for the future client.", InteractionArguments.model_json_schema()),
    ]
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}
            for name, description, parameters in contracts]


TOOL_SCHEMAS = build_tool_schemas()


def _json_safe(value: Any) -> Any:
    """Convert a pydantic/UUID value to JSON-safe data for provider and DB payloads."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _hash_request(messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    """Hash all provider inputs to make DeepSeek calls owner-cacheable and replayable."""
    body = json.dumps({"messages": messages, "tools": tools, "max_tokens": max_tokens}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def load_vacancy_definition() -> AgentDefinition:
    """Load packaged Russian vacancy instructions and pin both content hashes."""
    knowledge_path = files("agents_service").joinpath("knowledge/vacancy.md")
    definition_path = files("agents_service").joinpath("definitions/vacancy.json")
    knowledge = knowledge_path.read_text(encoding="utf-8")
    raw = json.loads(definition_path.read_text(encoding="utf-8"))
    raw["knowledge_hash"] = hashlib.sha256(knowledge.encode()).hexdigest()
    raw["definition_hash"] = hashlib.sha256(definition_path.read_bytes() +
        json.dumps(TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    raw["instruction"] = knowledge
    return AgentDefinition.model_validate(raw)


class AgentRuntime:
    """Bounded durable agent loop with CAS draft edits and replay-safe tool calls."""

    def __init__(self, store: Any, drafts: DraftGateway, provider: Provider, matching: MatchingSchema, *, definition: AgentDefinition | None = None) -> None:
        self.store = store
        self.drafts = drafts
        self.provider = provider
        self.matching = matching
        self.definition = definition or load_vacancy_definition()

    def create_session(self, owner_id: UUID, draft_id: UUID, draft_revision: int) -> Any:
        """Create a session pinned to the current packaged agent artifacts."""
        return self.store.create_session(owner_id=owner_id, draft_id=draft_id, draft_revision=draft_revision, definition=self.definition)

    def append_user_turn(self, owner_id: UUID, session_id: UUID, draft_id: UUID, request: UserTurnRequest) -> AgentTurn:
        """Append one idempotent turn after checking owner, draft and revision guards."""
        session = self.store.get_session(session_id, owner_id)
        if session is None or session.draft_id != draft_id:
            raise AgentRuntimeError("not_found", "agent session not found")
        existing = self.store.get_turn_by_idempotency(session_id, owner_id, request.idempotency_key) if hasattr(self.store, "get_turn_by_idempotency") else None
        if existing is not None:
            if existing.user_message != request.message or existing.expected_revision != request.expected_revision or existing.draft_id != draft_id:
                raise AgentRuntimeError("idempotency_conflict", "idempotency key was already used for another turn")
            return existing
        draft = self.drafts.get_draft(owner_id, draft_id)
        if getattr(draft, "state", None) in {"creating", "created", "failed"}:
            raise AgentRuntimeError("draft_is_frozen", "publication snapshot is frozen")
        current = self._revision(draft)
        if current != request.expected_revision:
            raise AgentRuntimeError("revision_conflict", "draft revision changed")
        try:
            return self.store.append_turn(session_id=session_id, owner_id=owner_id, draft_id=draft_id, message=request.message, expected_revision=request.expected_revision, idempotency_key=request.idempotency_key)
        except NotFoundError as exc:
            raise AgentRuntimeError("not_found", "draft is unavailable") from exc
        except StaleRevisionError as exc:
            raise AgentRuntimeError("revision_conflict", "draft revision changed") from exc
        except ConflictError as exc:
            raise AgentRuntimeError("draft_is_frozen", "publication snapshot is frozen") from exc
        except RuntimeError as exc:
            raise AgentRuntimeError("turn_active", "another turn is already active") from exc

    def run_turn(self, owner_id: UUID, turn_id: UUID, *, job: Any | None = None) -> AgentTurn:
        """Claim and execute one durable turn; retries are safe through CAS and journal rows."""
        turn = self.store.claim_turn(turn_id, owner_id)
        if turn is None:
            existing = self.store.get_turn(turn_id, owner_id)
            if existing is None:
                raise AgentRuntimeError("not_found", "agent turn not found")
            return existing
        try:
            return self._run_claimed(owner_id, turn, job=job)
        except AgentRuntimeError as exc:
            status = "retrying" if exc.retryable else "failed"
            with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                return self.store.update_turn(turn.turn_id, owner_id, status=status, error_code=exc.code, error_message=exc.code)
        except LeaseError:
            raise
        except ProviderError as error:
            with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                return self.store.update_turn(turn.turn_id, owner_id, status="retrying" if error.retryable else "failed",
                                              error_code=error.code, error_message=error.code)
        except (psycopg.Error, RuntimeError, ValueError, TypeError, KeyError):
            with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                return self.store.update_turn(turn.turn_id, owner_id, status="retrying", error_code="runtime_error", error_message="agent runtime failed")

    def respond_interaction(self, owner_id: UUID, session_id: UUID, interaction_id: UUID, request: InteractionResponse, *, idempotency_key: str) -> AgentTurn:
        """Validate an interaction under owner/session/revision scope and continue via the same patch path."""
        session = self.store.get_session(session_id, owner_id)
        if session is None:
            raise AgentRuntimeError("not_found", "agent session not found")
        interaction = self.store.get_interaction(interaction_id, session_id)
        if interaction is None:
            raise AgentRuntimeError("not_found", "interaction not found")
        if request.expected_revision != interaction.draft_revision:
            raise AgentRuntimeError("revision_conflict", "interaction belongs to another revision")
        if interaction.responded:
            existing = self.store.get_turn_by_idempotency(session_id, owner_id, idempotency_key)
            if fingerprint(interaction.value) == fingerprint(request.value) and existing is not None:
                return existing
            raise AgentRuntimeError("already_responded", "interaction already answered")
        draft = self.drafts.get_draft(owner_id, session.draft_id)
        current = self._revision(draft)
        if current != request.expected_revision:
            raise AgentRuntimeError("revision_conflict", "draft revision changed")
        self._validate_interaction_value(interaction, request.value)
        if interaction.field_target is not None:
            target = interaction.field_target
            value = request.value
            if "." in target:
                group, key = target.split(".", 1)
                values = _json_safe(draft)["fields"][group]
                values[key] = value
                patch_data = {group: values}
            elif interaction.kind == "multi_select" and isinstance(value, list):
                merged = {}
                for item in value:
                    if not isinstance(item, dict) or set(merged).intersection(item):
                        raise AgentRuntimeError("invalid_interaction_value", "group selections must contain distinct skill objects")
                    merged.update(item)
                patch_data = {target: merged}
            else:
                patch_data = {target: value}
            try:
                patch = DraftFields.model_validate(patch_data)
            except ValidationError as error:
                raise AgentRuntimeError("invalid_interaction_value", "value does not match the field") from error
            draft = self.drafts.patch_fields(owner_id, session.draft_id, current, patch)
            current = self._revision(draft)
        self.store.respond_interaction(interaction_id, session_id, request.value,
                                      expected_draft_revision=request.expected_revision)
        return self.append_user_turn(owner_id, session_id, session.draft_id, UserTurnRequest(expected_revision=current, idempotency_key=idempotency_key, message=f"Ответ на вопрос: {_json_safe(request.value)}"))

    def get_status(self, owner_id: UUID, session_id: UUID) -> AgentStatusView:
        """Return only owner-scoped session, latest turn, bounded messages and interactions."""
        session = self.store.get_session(session_id, owner_id)
        if session is None:
            raise AgentRuntimeError("not_found", "agent session not found")
        turn = self.store.latest_turn(session_id, owner_id) if hasattr(self.store, "latest_turn") else None
        draft = self.drafts.get_draft(owner_id, session.draft_id)
        interactions = self.store.list_interactions(session_id, limit=32) if hasattr(self.store, "list_interactions") else []
        return AgentStatusView(session=session, turn=turn, draft=_json_safe(draft), messages=self.store.list_messages(session_id, limit=self.definition.limits.latest_messages), interactions=interactions)

    def process_job(self, job: Any) -> AgentTurn:
        """Process a claimed ``agent_turn`` job supplied by the generic worker.

        The worker owns leasing and retries.  If it exposes ``assert_lease`` or
        ``lease_valid`` the check is made before entering provider/tool work and
        therefore a lost lease cannot continue mutating a draft.
        """
        if hasattr(self.store, "assert_job_lease"):
            self.store.assert_job_lease(job)
        owner_id = getattr(job, "owner_id", None) or (job.get("owner_id") if isinstance(job, Mapping) else None)
        turn_id = getattr(job, "turn_id", None) or (job.get("turn_id") if isinstance(job, Mapping) else None)
        payload = getattr(job, "payload", None) or (job.get("payload") if isinstance(job, Mapping) else {}) or {}
        if turn_id is None:
            turn_id = payload.get("turn_id")
        if owner_id is None or turn_id is None:
            raise AgentRuntimeError("invalid_job", "agent job lacks owner or turn")
        return self.run_turn(UUID(str(owner_id)), UUID(str(turn_id)), job=job)

    def retry_turn(self, owner_id: UUID, turn_id: UUID) -> AgentTurn:
        """Reset a failed/retryable turn for the parent worker's durable retry job."""
        try:
            return self.store.retry_turn(turn_id, owner_id)
        except RuntimeError as exc:
            raise AgentRuntimeError("turn_active", "turn cannot be retried while another turn is active") from exc

    def _run_claimed(self, owner_id: UUID, turn: AgentTurn, *, job: Any | None = None) -> AgentTurn:
        session = self.store.get_session(turn.session_id, owner_id)
        if session is None:
            raise AgentRuntimeError("not_found", "agent session not found")
        if (session.definition_hash != self.definition.definition_hash
                or session.knowledge_hash != self.definition.knowledge_hash
                or session.agent_version != self.definition.version):
            raise AgentRuntimeError("knowledge_version_mismatch", "session artifacts are unavailable")
        draft = self.drafts.get_draft(owner_id, turn.draft_id)
        if getattr(draft, "state", None) in {"creating", "created", "failed"}:
            raise AgentRuntimeError("draft_is_frozen", "publication snapshot is frozen")
        if self._revision(draft) != turn.draft_revision:
            raise AgentRuntimeError("revision_conflict", "draft revision changed before execution")
        limits = self.definition.limits
        persisted = self.store.list_messages(turn.session_id, limit=64)
        own = [item for item in persisted if item.turn_id == turn.turn_id]
        if own and own[-1].role == "assistant" and not own[-1].tool_calls:
            return self.store.update_turn(turn.turn_id, owner_id, status="succeeded")
        messages = self._build_context(owner_id, turn, limits)
        turn, messages = self._resume_pending_calls(owner_id, turn, messages, job=job)
        total_tokens = turn.input_tokens + turn.output_tokens
        calls = turn.tool_calls
        remaining_rounds = limits.max_rounds - turn.rounds
        if remaining_rounds <= 0:
            raise AgentRuntimeError("round_budget_exceeded", "agent round budget exceeded")
        for _round in range(remaining_rounds):
            self._lease_guard(job)
            messages = self._bound_messages(messages, limits.context_chars)
            remaining = limits.max_total_tokens - total_tokens
            if remaining <= 0:
                raise AgentRuntimeError("token_budget_exceeded", "agent token budget exceeded")
            raw = self._complete_cached(owner_id, messages, TOOL_SCHEMAS, min(limits.max_output_tokens, remaining))
            parsed = self._parse_provider(raw)
            total_tokens += parsed.usage.total_tokens
            with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                calls += len(parsed.tool_calls)
                turn = self.store.update_turn(turn.turn_id, owner_id, provider_result=_json_safe(raw), input_tokens=turn.input_tokens + parsed.usage.prompt_tokens, output_tokens=turn.output_tokens + parsed.usage.completion_tokens, rounds=turn.rounds + 1, tool_calls=calls)
                assistant = self.store.append_message(session_id=turn.session_id, turn_id=turn.turn_id, role="assistant", content=parsed.content, tool_calls=parsed.tool_calls)
            if total_tokens > limits.max_total_tokens:
                raise AgentRuntimeError("token_budget_exceeded", "agent token budget exceeded")
            messages.append(self._message_payload(assistant))
            if not parsed.tool_calls:
                with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                    return self.store.update_turn(turn.turn_id, owner_id, status="succeeded", draft_revision=self._revision(self.drafts.get_draft(owner_id, turn.draft_id)))
            if calls > limits.max_tool_calls:
                raise AgentRuntimeError("tool_budget_exceeded", "agent tool call budget exceeded")
            for call in parsed.tool_calls:
                self._lease_guard(job)
                result = self._execute_tool(owner_id, turn, call)
                refreshed = self.store.get_turn(turn.turn_id, owner_id)
                if refreshed is not None:
                    turn = refreshed
                tool_call_id = str(call.get("id", ""))
                tool_message = self.store.append_message(session_id=turn.session_id, turn_id=turn.turn_id, role="tool", content=json.dumps(_json_safe(result), ensure_ascii=False, separators=(",", ":")), tool_call_id=tool_call_id, name=str(call.get("name", "")))
                messages.append(self._message_payload(tool_message))
        raise AgentRuntimeError("round_budget_exceeded", "agent round budget exceeded")

    @staticmethod
    def _bound_messages(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Bound each provider request while preserving the authoritative system message."""
        result = list(messages)
        while len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > limit and len(result) > 2:
            oldest = result[1]
            if oldest.get("role") == "assistant" and oldest.get("tool_calls"):
                ids = {str(item.get("id")) for item in oldest["tool_calls"]}
                result = [result[0], *[item for item in result[2:] if str(item.get("tool_call_id")) not in ids]]
            else:
                del result[1]
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > limit:
            raise AgentRuntimeError("context_budget_exceeded", "authoritative draft exceeds context budget")
        return result

    def _build_context(self, owner_id: UUID, turn: AgentTurn, limits: AgentLimits) -> list[dict[str, Any]]:
        """Build authoritative current-draft context and deterministically bounded history."""
        draft = self.drafts.get_draft(owner_id, turn.draft_id)
        schema = self.matching.schema()
        schema_value = _json_safe(schema)
        value = _json_safe(draft)
        fields = {key: item for key, item in value["fields"].items() if key != "metadata"}
        current = {"draft": {"draft_id": value["draft_id"], "state": value["state"], "fields": fields,
                            "missing_requirements": value["missing_requirements"]},
                   "current_request": turn.user_message,
                   "draft_revision": self._revision(draft), "session_draft_revision": turn.draft_revision,
                   "schema": {"version": schema_value.get("version"), "schema_hash": schema_value.get("schema_hash")} if isinstance(schema_value, Mapping) else {}}
        system = f"{self.definition.instruction}\nТекущее состояние черновика (источник истины): {json.dumps(current, ensure_ascii=False, separators=(',', ':'))}"
        messages = [{"role": "system", "content": system}]
        messages.extend(self._message_payload(item) for item in self._context_messages(turn.session_id, limits.latest_messages))
        return self._bound_messages(messages, limits.context_chars)

    def _resume_pending_calls(self, owner_id: UUID, turn: AgentTurn, messages: list[dict[str, Any]], *, job: Any | None) -> tuple[AgentTurn, list[dict[str, Any]]]:
        """Complete assistant tool calls persisted before a crash before asking the provider again."""
        persisted = self.store.list_messages(turn.session_id, limit=32)
        assistant = next((item for item in reversed(persisted) if item.turn_id == turn.turn_id and item.role == "assistant" and item.tool_calls), None)
        if assistant is None:
            return turn, messages
        after = [item for item in persisted if item.sequence > assistant.sequence and item.role == "tool"]
        done = {item.tool_call_id for item in after if item.tool_call_id}
        for call in assistant.tool_calls:
            call_id = str(call.get("id", ""))
            if call_id in done:
                continue
            self._lease_guard(job)
            result = self._execute_tool(owner_id, turn, call)
            refreshed = self.store.get_turn(turn.turn_id, owner_id)
            if refreshed is not None:
                turn = refreshed
            tool_message = self.store.append_message(session_id=turn.session_id, turn_id=turn.turn_id, role="tool", content=json.dumps(_json_safe(result), ensure_ascii=False, separators=(",", ":")), tool_call_id=call_id, name=str(call.get("name", "")))
            messages.append(self._message_payload(tool_message))
        return turn, messages

    def _lease_guard(self, job: Any | None) -> None:
        """Stop work before an external call or mutation when the worker lease is lost."""
        if job is None:
            return
        if hasattr(self.store, "assert_job_lease"):
            self.store.assert_job_lease(job)
        valid = getattr(job, "lease_valid", None)
        if callable(valid) and not valid():
            raise AgentRuntimeError("lease_lost", "agent job lease is no longer valid", retryable=True)

    def _context_messages(self, session_id: UUID, limit: int) -> list[Any]:
        """Compact deterministically while retaining an assistant/tool call chain together."""
        all_messages = self.store.list_messages(session_id, limit=64)
        start = max(0, len(all_messages) - limit)
        while start > 0 and all_messages[start].role == "tool":
            start -= 1
        selected = all_messages[start:]
        if selected and selected[0].role == "tool":
            raise AgentRuntimeError("history_chain_invalid", "assistant call is missing from history")
        return selected

    def _execute_tool(self, owner_id: UUID, turn: AgentTurn, call: Mapping[str, Any]) -> ToolResult:
        call_id = str(call.get("id", ""))
        name = str(call.get("name", ""))
        if not call_id or len(call_id) > 120:
            raise AgentRuntimeError("invalid_tool_call", "tool call id is invalid")
        arguments = call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise AgentRuntimeError("invalid_tool_arguments", "tool arguments must be JSON") from exc
        if not isinstance(arguments, dict) or len(json.dumps(arguments, ensure_ascii=False).encode()) > MAX_TOOL_ARGUMENT_BYTES:
            raise AgentRuntimeError("invalid_tool_arguments", "tool arguments are too large")
        previous = self.store.journal_get(turn.turn_id, call_id)
        current_draft = self.drafts.get_draft(owner_id, turn.draft_id)
        current_revision = self._revision(current_draft)
        if current_revision != turn.draft_revision and name not in {"get_draft", "get_taxonomy"}:
            raise AgentRuntimeError("revision_conflict", "draft changed during the turn")
        if previous is not None and previous.status in {"completed", "failed"}:
            replay = ToolResult(ok=previous.status == "completed", value=previous.result or {}, error_code=previous.error_code, draft_revision=previous.draft_revision)
            if replay.draft_revision != turn.draft_revision:
                with self.store.checkpoint_transaction() if hasattr(self.store, "checkpoint_transaction") else nullcontext():
                    self.store.update_turn(turn.turn_id, owner_id, draft_revision=replay.draft_revision)
                    if hasattr(self.store, "update_session_revision"):
                        self.store.update_session_revision(turn.session_id, owner_id, replay.draft_revision)
            return replay
        if previous is not None and previous.status == "started":
            self.store.journal_finish(turn_id=turn.turn_id, call_id=call_id, status="failed", result={}, draft_revision=current_revision, error_code="interrupted_tool")
            raise AgentRuntimeError("interrupted_tool", "previous tool effect was not durably completed")
        with self.store.tool_transaction() if hasattr(self.store, "tool_transaction") else nullcontext():
            self.store.journal_start(turn_id=turn.turn_id, call_id=call_id, tool_name=name, arguments=arguments, draft_revision=current_revision)
            result = self._call_tool(owner_id, turn, name, arguments, current_draft)
            self.store.journal_finish(turn_id=turn.turn_id, call_id=call_id, status="completed" if result.ok else "failed", result=result.value, draft_revision=result.draft_revision, error_code=result.error_code)
            if result.draft_revision != turn.draft_revision:
                self.store.update_turn(turn.turn_id, owner_id, draft_revision=result.draft_revision)
                if hasattr(self.store, "update_session_revision"):
                    self.store.update_session_revision(turn.session_id, owner_id, result.draft_revision)
        return result

    def _call_tool(self, owner_id: UUID, turn: AgentTurn, name: str, arguments: Mapping[str, Any], draft: Any) -> ToolResult:
        revision = self._revision(draft)
        try:
            if name == "get_draft":
                return ToolResult(ok=True, value={"draft": self._draft_tool_view(draft)}, draft_revision=revision)
            if name == "get_taxonomy":
                schema = SchemaSnapshot.model_validate(_json_safe(self.matching.schema()))
                return ToolResult(ok=True, value={"schema": schema.model_dump(mode="json")}, draft_revision=revision)
            if name == "set_fields":
                parsed = SetFieldsArguments.model_validate(arguments)
                fields = DraftFields.model_validate(parsed.fields)
                updated = self._patch_fields(owner_id, turn.draft_id, revision, fields)
                return ToolResult(ok=True, value={"draft": self._draft_tool_view(updated)}, draft_revision=self._revision(updated))
            if name == "approve":
                parsed = ApproveArguments.model_validate(arguments)
                if parsed.expected_revision != revision:
                    raise AgentRuntimeError("revision_conflict", "approval revision differs")
                updated = self._approve(owner_id, turn.draft_id, revision)
                self.store.set_session_status(turn.session_id, owner_id, "approved")
                return ToolResult(ok=True, value={"draft": self._draft_tool_view(updated), "approved": True}, draft_revision=self._revision(updated))
            if name == "show_interaction":
                parsed = InteractionArguments.model_validate(arguments)
                interaction = AgentInteraction(interaction_id=uuid4(), session_id=turn.session_id, turn_id=turn.turn_id, draft_revision=revision, kind=parsed.kind, prompt=parsed.prompt, field_target=parsed.field_target, options=parsed.options, min_value=parsed.min_value, max_value=parsed.max_value)
                saved = self.store.save_interaction(interaction)
                return ToolResult(ok=True, value={"interaction": saved.model_dump(mode="json")}, draft_revision=revision)
            raise AgentRuntimeError("unknown_tool", "tool is not available")
        except ValidationError:
            return ToolResult(ok=False, error_code="invalid_tool_arguments", error_message="tool arguments failed validation", draft_revision=revision)
        except AgentRuntimeError as exc:
            if exc.code in {"revision_conflict", "interrupted_tool", "lease_lost"}:
                raise
            return ToolResult(ok=False, error_code=exc.code, error_message="tool request was rejected", draft_revision=revision)
        except (LeaseError, psycopg.Error):
            raise
        except StaleRevisionError as error:
            raise AgentRuntimeError("revision_conflict", "draft revision changed") from error
        except (StoreError, ValueError, TypeError, KeyError):
            return ToolResult(ok=False, error_code="tool_failed", error_message="tool execution failed", draft_revision=revision)

    @staticmethod
    def _draft_tool_view(draft: Any) -> dict[str, Any]:
        """Expose editable state without duplicating provenance or publication audit in prompts."""
        value = _json_safe(draft)
        result = {key: value[key] for key in ("draft_id", "revision", "state", "missing_requirements") if key in value}
        result["fields"] = {key: item for key, item in value["fields"].items() if key != "metadata"}
        if value.get("summary"):
            result["summary"] = {key: item for key, item in value["summary"].items() if key != "fields"}
        return result

    def _complete_cached(self, owner_id: UUID, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
        request_hash = hashlib.sha256((_hash_request(messages, tools, max_tokens) +
            self.definition.definition_hash + str(getattr(self.provider, "model", "unknown"))).encode()).hexdigest()
        if getattr(self.provider, "provider", "deepseek") != "deepseek":
            request_hash = hashlib.sha256((request_hash + str(getattr(self.provider, "provider", "")) +
                str(getattr(self.provider, "base_url", "")) + str(getattr(self.provider, "reasoning_effort", ""))).encode()).hexdigest()
        cached = self.store.cache_get(owner_id, request_hash)
        if cached is not None:
            return cached
        raw = self.provider.complete(messages, tools, max_tokens=max_tokens)
        self._parse_provider(raw)  # cache only structurally valid provider responses
        self.store.cache_put(owner_id, request_hash, raw)
        return raw

    def _patch_fields(self, owner_id: UUID, draft_id: UUID, revision: int, fields: DraftFields) -> Any:
        """Use the parent store's same-connection CAS adapter when available."""
        method = getattr(self.drafts, "patch_fields_in_connection", None)
        if method is not None:
            return method(owner_id, draft_id, revision, fields)
        return self.drafts.patch_fields(owner_id, draft_id, revision, fields)

    def _approve(self, owner_id: UUID, draft_id: UUID, revision: int) -> Any:
        """Use the parent store's same-connection approval adapter when available."""
        method = getattr(self.drafts, "approve_in_connection", None)
        if method is not None:
            return method(owner_id, draft_id, revision)
        return self.drafts.approve(owner_id, draft_id, revision)

    @staticmethod
    def _parse_provider(raw: Mapping[str, Any]) -> ProviderCall:
        try:
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ValueError("provider choices are missing")
            message = choices[0].get("message")
            if not isinstance(message, Mapping):
                raise TypeError("provider message is missing")
            calls: list[dict[str, Any]] = []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, Mapping) or not call.get("id"):
                    raise ValueError("provider tool call id is missing")
                function = call.get("function")
                if not isinstance(function, Mapping) or not function.get("name"):
                    raise ValueError("provider tool function is missing")
                arguments = function.get("arguments", "{}")
                if not isinstance(arguments, (str, Mapping)):
                    raise TypeError("provider tool arguments are invalid")
                calls.append({"id": str(call["id"]), "name": str(function["name"]), "arguments": arguments})
            if len({str(item["id"]) for item in calls}) != len(calls):
                raise ValueError("provider tool call ids are duplicated")
            raw_usage = message.get("usage") or raw.get("usage") or {}
            if not isinstance(raw_usage, Mapping):
                raise TypeError("provider usage is invalid")
            prompt_tokens = int(raw_usage.get("prompt_tokens", 0))
            completion_tokens = int(raw_usage.get("completion_tokens", 0))
            total_tokens = max(int(raw_usage.get("total_tokens", 0)), prompt_tokens + completion_tokens)
            usage = ProviderUsage.model_validate({"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens})
            return ProviderCall.model_validate({"content": message.get("content") or "", "tool_calls": calls, "usage": usage.model_dump()})
        except (ValidationError, ValueError, TypeError) as exc:
            raise AgentRuntimeError("provider_invalid_output", "provider returned invalid structured output") from exc

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        payload = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = [{"id": item.get("id"), "type": "function", "function": {"name": item.get("name"), "arguments": item.get("arguments", "{}")}} for item in message.tool_calls]
        if message.role == "tool":
            payload["tool_call_id"] = message.tool_call_id
            payload["name"] = message.name
        return payload

    @staticmethod
    def _revision(draft: Any) -> int:
        revision = getattr(draft, "revision", None)
        if revision is None and isinstance(draft, Mapping):
            revision = draft.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise AgentRuntimeError("draft_invalid", "draft revision is unavailable")
        return revision

    @staticmethod
    def _validate_interaction_value(interaction: AgentInteraction, value: Any) -> None:
        """Validate button/select/slider values without accepting arbitrary model data."""
        allowed = {fingerprint(item.value) for item in interaction.options}
        if interaction.kind in {"buttons", "single_select"} and fingerprint(value) not in allowed:
            raise AgentRuntimeError("invalid_interaction_value", "selected value is not an option")
        if interaction.kind == "multi_select" and (not isinstance(value, list) or not all(fingerprint(item) in allowed for item in value)
                or len({fingerprint(item) for item in value}) != len(value)):
            raise AgentRuntimeError("invalid_interaction_value", "selected values are not options")
        if interaction.kind == "slider" and (type(value) is not int or interaction.min_value is None or interaction.max_value is None or not interaction.min_value <= value <= interaction.max_value):
            raise AgentRuntimeError("invalid_interaction_value", "slider value is outside bounds")


class AgentService:
    """Small application facade used by HTTP routes and tests around ``AgentRuntime``."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    def create_session(self, owner_id: UUID, draft_id: UUID, draft_revision: int) -> Any:
        """Create one vacancy agent session."""
        return self.runtime.create_session(owner_id, draft_id, draft_revision)

    def get_status(self, owner_id: UUID, session_id: UUID) -> AgentStatusView:
        """Return the owner-scoped status projection."""
        return self.runtime.get_status(owner_id, session_id)

    def append_turn(self, owner_id: UUID, session_id: UUID, draft_id: UUID, request: UserTurnRequest) -> AgentTurn:
        """Append one user turn for durable worker processing."""
        return self.runtime.append_user_turn(owner_id, session_id, draft_id, request)

    def respond_interaction(self, owner_id: UUID, session_id: UUID, interaction_id: UUID, request: InteractionResponse, *, idempotency_key: str) -> AgentTurn:
        """Validate and append an interaction response through the same turn path."""
        return self.runtime.respond_interaction(owner_id, session_id, interaction_id, request, idempotency_key=idempotency_key)

    def process_job(self, job: Any) -> AgentTurn:
        """Process one claimed generic worker job."""
        return self.runtime.process_job(job)
