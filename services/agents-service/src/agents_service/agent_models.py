"""Contracts for the reusable, durable agent runtime.

The module intentionally contains no FastAPI or provider implementation.  API routes
and workers can therefore use the same validation rules for synchronous tests and
durable turns.  Values coming from a model are treated as untrusted input.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

AgentStatus = Literal["queued", "processing", "retrying", "failed", "succeeded"]
TurnStatus = Literal["queued", "processing", "retrying", "failed", "succeeded"]
MessageRole = Literal["system", "user", "assistant", "tool"]
InteractionKind = Literal["buttons", "single_select", "multi_select", "slider"]
JournalStatus = Literal["started", "completed", "failed"]

MAX_ROUNDS = 8
MAX_TOOL_CALLS = 16
MAX_OUTPUT_TOKENS = 1536
MAX_TOTAL_TOKENS = 16_000
MAX_CONTEXT_CHARS = 24_000
LATEST_MESSAGES = 8
MAX_TOOL_ARGUMENT_BYTES = 16_384


class AgentContract(BaseModel):
    """Base model for agent payloads; unknown fields are rejected at the boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentLimits(AgentContract):
    """Resource and conversation bounds applied independently of provider settings."""

    max_rounds: int = Field(default=MAX_ROUNDS, strict=True, ge=1, le=MAX_ROUNDS)
    max_tool_calls: int = Field(default=MAX_TOOL_CALLS, strict=True, ge=1, le=MAX_TOOL_CALLS)
    max_output_tokens: int = Field(default=MAX_OUTPUT_TOKENS, strict=True, ge=1, le=MAX_OUTPUT_TOKENS)
    max_total_tokens: int = Field(default=MAX_TOTAL_TOKENS, strict=True, ge=1, le=MAX_TOTAL_TOKENS)
    context_chars: int = Field(default=MAX_CONTEXT_CHARS, strict=True, ge=1_000, le=MAX_CONTEXT_CHARS)
    latest_messages: int = Field(default=LATEST_MESSAGES, strict=True, ge=1, le=LATEST_MESSAGES)


class AgentDefinition(AgentContract):
    """Packaged agent metadata pinned into each session and turn."""

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(min_length=1, max_length=80)
    knowledge_version: str = Field(min_length=1, max_length=80)
    knowledge_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction: str = Field(min_length=1, max_length=12_000)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    tools: list[str] = Field(min_length=1, max_length=16)


class AgentSession(AgentContract):
    """Owner-scoped durable conversation bound to one draft revision stream."""

    session_id: UUID
    owner_id: UUID
    draft_id: UUID
    agent_key: str
    agent_version: str
    knowledge_version: str
    knowledge_hash: str
    definition_hash: str
    status: Literal["active", "approved", "closed", "failed"] = "active"
    draft_revision: int = Field(strict=True, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentMessage(AgentContract):
    """Persisted chat message; tool calls and raw provider data remain structured JSON."""

    message_id: UUID
    session_id: UUID
    turn_id: UUID | None = None
    role: MessageRole
    content: str = Field(default="", max_length=32_000)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=16)
    name: str | None = Field(default=None, max_length=80)
    tool_call_id: str | None = Field(default=None, max_length=120)
    sequence: int = Field(strict=True, ge=0)
    created_at: datetime | None = None


class AgentTurn(AgentContract):
    """One idempotent user request and its durable provider/tool execution state."""

    turn_id: UUID
    session_id: UUID
    owner_id: UUID
    draft_id: UUID
    sequence: int = Field(strict=True, ge=1)
    user_message: str = Field(min_length=1, max_length=8_000)
    expected_revision: int = Field(strict=True, ge=1)
    draft_revision: int = Field(strict=True, ge=1)
    status: TurnStatus = "queued"
    idempotency_key: str = Field(min_length=1, max_length=200)
    provider_result: dict[str, JsonValue] | None = None
    input_tokens: int = Field(default=0, strict=True, ge=0)
    output_tokens: int = Field(default=0, strict=True, ge=0)
    rounds: int = Field(default=0, strict=True, ge=0)
    tool_calls: int = Field(default=0, strict=True, ge=0)
    error_code: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=500)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InteractionOption(AgentContract):
    """One selectable value exposed by an assistant interaction."""

    value: JsonValue
    label: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def bounded_json_value(self) -> InteractionOption:
        """Keep model-supplied choice payloads small enough for durable UI state."""
        encoded = json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > 2_048:
            raise ValueError("interaction option value exceeds 2 KiB")
        return self


class AgentInteraction(AgentContract):
    """Typed, server-validated prompt for a user response."""

    interaction_id: UUID
    session_id: UUID
    turn_id: UUID
    draft_revision: int = Field(strict=True, ge=1)
    kind: InteractionKind
    prompt: str = Field(min_length=1, max_length=1_000)
    field_target: str | None = Field(default=None, max_length=120)
    options: list[InteractionOption] = Field(default_factory=list, max_length=32)
    min_value: int | None = Field(default=None, strict=True, ge=0, le=7)
    max_value: int | None = Field(default=None, strict=True, ge=0, le=7)
    value: JsonValue | None = None
    responded: bool = False
    created_at: datetime | None = None
    responded_at: datetime | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> AgentInteraction:
        """Keep interaction values bounded and meaningful for their widget type."""
        if self.kind in {"buttons", "single_select", "multi_select"} and not self.options:
            raise ValueError("select interactions require options")
        if self.kind == "slider" and (self.min_value is None or self.max_value is None):
            raise ValueError("slider interactions require bounds")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("interaction bounds are reversed")
        fingerprints = [json.dumps(item.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in self.options]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("interaction options must have unique values")
        return self


class ToolJournalEntry(AgentContract):
    """Idempotency record for one model tool call, keyed by turn and provider call id."""

    turn_id: UUID
    call_id: str = Field(min_length=1, max_length=120)
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: JournalStatus
    result: dict[str, JsonValue] | None = None
    draft_revision: int = Field(strict=True, ge=1)
    error_code: str | None = Field(default=None, max_length=80)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentStatusView(AgentContract):
    """Safe status projection returned to an owner while a turn is running."""

    session: AgentSession
    turn: AgentTurn | None = None
    draft: dict[str, JsonValue] | None = None
    messages: list[AgentMessage] = Field(default_factory=list, max_length=8)
    interactions: list[AgentInteraction] = Field(default_factory=list, max_length=32)


class UserTurnRequest(AgentContract):
    """Request for appending a user turn with both optimistic and idempotency guards."""

    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=8_000)


class InteractionResponse(AgentContract):
    """Validated value submitted to one pending interaction."""

    expected_revision: int = Field(strict=True, ge=1)
    value: JsonValue


class ProviderUsage(AgentContract):
    """Provider usage counters accepted only when they fit runtime budgets."""

    prompt_tokens: int = Field(default=0, strict=True, ge=0)
    completion_tokens: int = Field(default=0, strict=True, ge=0)
    total_tokens: int = Field(default=0, strict=True, ge=0)


class ProviderCall(AgentContract):
    """Normalized provider response used by the runtime loop."""

    role: Literal["assistant"] = "assistant"
    content: str = Field(default="", max_length=32_000)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=16)
    usage: ProviderUsage = Field(default_factory=ProviderUsage)


class ToolResult(AgentContract):
    """Structured tool outcome included in a tool message and provider context."""

    ok: bool
    value: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=500)
    draft_revision: int = Field(strict=True, ge=1)
