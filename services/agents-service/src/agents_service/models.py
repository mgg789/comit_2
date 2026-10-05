"""Typed contracts for employer vacancy drafts and their publication jobs.

The agents service deliberately owns a small copy of the vacancy intake contract.  It
does not import the matching service: the registry is passed in as ``SchemaSnapshot``
and is checked before every write that changes coordinate fields.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

Direction = Literal["backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml"]
WorkType = Literal["onsite", "remote", "hybrid"]
DraftState = Literal["editing", "summary", "creating", "created", "failed"]
JobKind = Literal["publication", "enrichment", "agent_turn"]
JobState = Literal["queued", "processing", "retrying", "failed", "succeeded", "obsolete"]
SourceName = Literal["manual", "agent", "laya", "system", "fsp"]
CoordinateGroup = Literal["domain", "stack", "soft_skill"]

SAFE_JSON_INTEGER = 2**53 - 1
SkillKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")]
SkillLevel = Annotated[int, Field(strict=True, ge=0, le=7)]


class Contract(BaseModel):
    """Base contract that rejects misspelled fields at the API boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SchemaCoordinate(Contract):
    """One immutable coordinate from the matching registry."""

    index: int = Field(strict=True, ge=0)
    group: CoordinateGroup
    key: SkillKey


class SchemaSnapshot(Contract):
    """Matching registry identity and coordinate ordering supplied by the API parent."""

    version: int = Field(strict=True, ge=1)
    schema_hash: str = Field(min_length=1, max_length=128)
    coordinates: list[SchemaCoordinate] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_coordinates(self) -> SchemaSnapshot:
        """Reject duplicate indexes or keys before vectors can be interpreted differently."""
        if len({item.index for item in self.coordinates}) != len(self.coordinates):
            raise ValueError("schema coordinates must have unique indexes")
        if len({(item.group, item.key) for item in self.coordinates}) != len(self.coordinates):
            raise ValueError("schema coordinates must have unique group/key pairs")
        return self


class DraftFields(Contract):
    """Partial or canonical vacancy fields; omitted values remain distinguishable in patches."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    zp_min: int | None = Field(default=None, strict=True, ge=0, le=SAFE_JSON_INTEGER)
    zp_max: int | None = Field(default=None, strict=True, ge=0, le=SAFE_JSON_INTEGER)
    daily: str | None = Field(default=None, min_length=1, max_length=500)
    city: str | None = Field(default=None, min_length=1, max_length=300)
    type: WorkType | None = None
    bio: str | None = Field(default=None, max_length=10000)
    domens: dict[SkillKey, SkillLevel] = Field(default_factory=dict, max_length=1000)
    stacks: dict[SkillKey, SkillLevel] = Field(default_factory=dict, max_length=1000)
    soft_skills: dict[SkillKey, SkillLevel] = Field(default_factory=dict, max_length=1000)
    class_key: Direction | None = Field(default=None, alias="class")
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, value: object) -> object:
        """Accept legacy Russian work-format labels while storing canonical values."""
        return {"очно": "onsite", "дистант": "remote", "гибрид": "hybrid"}.get(value, value)

    @field_validator("class_key", mode="before")
    @classmethod
    def normalize_class(cls, value: object) -> object:
        """Map the three established upstream aliases to the eight canonical directions."""
        return {"back": "backend", "front": "frontend", "ML": "ml"}.get(value, value)

    @field_validator("daily", "city")
    @classmethod
    def nonblank_strings(cls, value: str | None) -> str | None:
        """Whitespace-only required fields are not accepted as meaningful input."""
        if value is not None and not value.strip():
            raise ValueError("daily and city must not be blank")
        return value

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str | None) -> str | None:
        """Require a visible title rather than accepting whitespace-only approval input."""
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("bio", mode="before")
    @classmethod
    def normalize_bio(cls, value: object) -> object:
        """Allow an explicit null from a form while retaining the matching empty-string contract."""
        return "" if value is None else value

    @model_validator(mode="after")
    def ordered_salary(self) -> DraftFields:
        """Reject reversed salary bounds before a revision can be persisted."""
        if self.zp_min is not None and self.zp_max is not None and self.zp_min > self.zp_max:
            raise ValueError("zp_max must be greater than or equal to zp_min")
        if len(json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")).encode()) > 65536:
            raise ValueError("metadata exceeds 64 KiB")
        return self


class DraftCreate(Contract):
    """Create request; ownership is injected from trusted internal authentication."""

    fields: DraftFields = Field(default_factory=DraftFields)


class DraftPatch(Contract):
    """Compare-and-swap patch request; ``exclude_unset`` preserves omitted fields."""

    expected_revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)
    fields: DraftFields = Field(default_factory=DraftFields)


class RevisionRequest(Contract):
    """Revision guard shared by approval and other mutating operations."""

    expected_revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)


class CreateRequest(Contract):
    """Human publication confirmation for one exact approved summary."""

    expected_revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FieldSource(Contract):
    """Provenance attached to one field or requirement group."""

    source: SourceName
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=30)


class SummarySnapshot(Contract):
    """Immutable, typed approval snapshot; no generated narrative is required."""

    revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)
    summary_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: DraftFields
    strict_schema_version: int = Field(strict=True, ge=1)
    strict_schema_hash: str = Field(min_length=1, max_length=128)


class PublicationView(Contract):
    """Publication handoff status; accepted means matching intake accepted the payload."""

    status: Literal["pending", "accepted", "failed"]
    source_revision: int = Field(strict=True, ge=1)
    matching_status: str | None = Field(default=None, max_length=100)
    job_id: UUID | None = None
    summary_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = Field(default=None, max_length=500)


class DraftView(Contract):
    """Owner-scoped draft response with revision, provenance and publication state."""

    draft_id: UUID
    vacancy_id: UUID
    owner_id: UUID
    revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)
    state: DraftState
    fields: DraftFields
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)
    missing_requirements: list[str] = Field(default_factory=list)
    summary: SummarySnapshot | None = None
    publication: PublicationView | None = None


class JobView(Contract):
    """Durable generic worker job with bounded leases and retry metadata."""

    job_id: UUID
    kind: JobKind
    owner_id: UUID
    draft_id: UUID | None = None
    session_id: UUID | None = None
    payload: dict[str, JsonValue]
    state: JobState
    attempts: int = Field(strict=True, ge=0)
    max_attempts: int = Field(strict=True, ge=1, le=20)
    available_at: str | None = None
    lease_token: UUID | None = None
    lease_until: str | None = None
    result: dict[str, JsonValue] | None = None
    error: str | None = None
