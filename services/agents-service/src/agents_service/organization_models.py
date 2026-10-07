"""Pydantic contracts for the global employer organization agent.

The organization chat is deliberately independent from vacancy draft sessions:
one employer owns one durable conversation, while turns and delegated tasks are
separate queues that can be resumed after a worker restart.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer


class OrganizationContract(BaseModel):
    """Reject unknown fields at every HTTP and persistence boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


OrganizationMessageRole = Literal["system", "user", "assistant", "tool"]
OrganizationTurnStatus = Literal["queued", "processing", "retrying", "succeeded", "failed"]
OrganizationReasoningEffort = Literal["off", "medium", "max"]
OrganizationTaskKind = Literal["schedule_interview", "inquiry", "stand_in"]
OrganizationTaskStatus = Literal["queued", "processing", "retrying", "waiting", "waiting_employer", "succeeded", "failed", "stopped"]


class OrganizationAttachment(OrganizationContract):
    """Safe attachment metadata persisted alongside a chat message."""

    document_id: UUID
    name: str = Field(min_length=1, max_length=200)
    media_type: str = Field(min_length=1, max_length=120)
    content_hash: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(strict=True, ge=0)


class OrganizationChat(OrganizationContract):
    """Owner-scoped global employer conversation and its presence projection."""

    chat_id: UUID
    owner_id: UUID
    status: Literal["active", "closed"] = "active"
    presence: bool = False
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrganizationMessage(OrganizationContract):
    """Durable message in the global conversation."""

    message_id: UUID
    chat_id: UUID
    turn_id: UUID | None = None
    role: OrganizationMessageRole
    content: str = Field(default="", max_length=32_000)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=32)
    tool_call_id: str | None = Field(default=None, max_length=160)
    name: str | None = Field(default=None, max_length=120)
    attachments: list[OrganizationAttachment] = Field(default_factory=list, max_length=32)
    sequence: int = Field(strict=True, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=200)
    created_at: datetime | None = None


class OrganizationTurn(OrganizationContract):
    """One idempotent queued model turn with exact provider usage counters."""

    turn_id: UUID
    chat_id: UUID
    owner_id: UUID
    task_id: UUID | None = None
    source: Literal["employer", "workflow"] = "employer"
    task_lease_token: UUID | None = None
    sequence: int = Field(strict=True, ge=1)
    user_message: str = Field(min_length=1, max_length=8_000)
    reasoning_effort: OrganizationReasoningEffort = "off"
    reasoning_history: dict[str, str] = Field(default_factory=dict, exclude=True)
    status: OrganizationTurnStatus = "queued"
    idempotency_key: str = Field(min_length=1, max_length=200)
    provider_result: dict[str, JsonValue] | None = None
    input_tokens: int = Field(default=0, strict=True, ge=0)
    output_tokens: int = Field(default=0, strict=True, ge=0)
    rounds: int = Field(default=0, strict=True, ge=0)
    tool_calls: int = Field(default=0, strict=True, ge=0)
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=500)
    lease_token: UUID | None = None
    lease_until: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("provider_result")
    def public_provider_result(self, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        """Keep the receipt shape compatible while hiding private provider reasoning."""
        def redact(item: JsonValue) -> JsonValue:
            if isinstance(item, dict):
                return {key: redact(child) for key, child in item.items() if key != "reasoning_content"}
            if isinstance(item, list):
                return [redact(child) for child in item]
            return item
        return {key: redact(child) for key, child in value.items() if key != "reasoning_content"} if value else value


class OrganizationTask(OrganizationContract):
    """Explicit employer-authorized delegated task and its candidate references."""

    task_id: UUID
    chat_id: UUID
    owner_id: UUID
    kind: OrganizationTaskKind
    instruction: str = Field(min_length=1, max_length=8_000)
    conversation_refs: dict[str, JsonValue] = Field(default_factory=dict)
    status: OrganizationTaskStatus = "queued"
    stop_reason: str | None = Field(default=None, max_length=500)
    result: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=200)
    lease_token: UUID | None = None
    lease_until: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    next_run_at: datetime | None = None


class PendingAnswer(OrganizationContract):
    """Question awaiting an owner answer; answers never enter long-term memory automatically."""

    answer_id: UUID
    chat_id: UUID
    turn_id: UUID | None = None
    task_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=2_000)
    answer_schema: dict[str, JsonValue] = Field(default_factory=dict)
    answer: JsonValue | None = None
    answered: bool = False
    created_at: datetime | None = None
    answered_at: datetime | None = None


class PendingAnswerRequest(OrganizationContract):
    """Owner response to one durable pending question."""

    answer: JsonValue


class OrganizationContext(OrganizationContract):
    """Current compact context summary, kept separate from editable memory."""

    owner_id: UUID
    current_summary: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = Field(strict=True, ge=1)
    updated_at: datetime | None = None


class OrganizationMemory(OrganizationContract):
    """Owner memory document guarded by an explicit compare-and-swap revision."""

    owner_id: UUID
    value: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = Field(strict=True, ge=1)
    updated_at: datetime | None = None


class OrganizationToolJournal(OrganizationContract):
    """Replay fence for one model tool call."""

    turn_id: UUID
    call_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal["started", "completed", "failed"]
    result: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, max_length=120)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PresenceUpdate(OrganizationContract):
    """Presence heartbeat payload."""

    present: bool


class OrganizationMessageRequest(OrganizationContract):
    """Request to queue a user message in the sole owner chat."""

    message: str = Field(min_length=1, max_length=8_000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    reasoning_effort: OrganizationReasoningEffort = "off"


class OrganizationTaskRequest(OrganizationContract):
    """Explicit employer authorization for a background delegated task."""

    kind: OrganizationTaskKind
    instruction: str = Field(min_length=1, max_length=8_000)
    conversation_refs: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class OrganizationMemoryPatch(OrganizationContract):
    """CAS patch for owner memory; callers must identify the revision they saw."""

    expected_revision: int = Field(strict=True, ge=1)
    value: dict[str, JsonValue]


class OrganizationBudget(OrganizationContract):
    """Rolling seven-day budget projection in exact decimal micro-USD."""

    limit_micro_usd: Decimal
    spent_micro_usd: Decimal
    reserved_micro_usd: Decimal
    available_micro_usd: Decimal
    window_days: int = 7
    pricing_configured: bool


class OrganizationMessagePage(OrganizationContract):
    """Stable keyset page for chat messages."""

    items: list[OrganizationMessage]
    next_sequence: int | None = None


class OrganizationTurnRequest(OrganizationContract):
    """Internal worker DTO; HTTP clients submit messages instead."""

    owner_id: UUID
    chat_id: UUID
    turn_id: UUID
