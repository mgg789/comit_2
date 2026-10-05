"""Revisioned vacancy input and explicit combiner-readiness contracts."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from matching_engine.models import Contract, Direction

BusinessStatus = Literal["draft", "open", "pending", "closed", "done"]
WorkType = Literal["onsite", "remote", "hybrid"]
PipelineState = Literal["draft", "queued", "processing", "retrying", "failed", "ready"]
BuildMode = Literal["class_only", "requirements_only", "mixed"]
SkillKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")]
Level = Annotated[int, Field(strict=True, ge=0, le=7)]
# JSON is parsed by the TypeScript gateway before forwarding; int64 is not lossless there.
SAFE_JSON_INTEGER = 2**53 - 1


class VacancyInput(Contract):
    """Canonical trusted snapshot; incomplete drafts persist without invented requirements."""

    source_revision: int = Field(strict=True, ge=1, le=SAFE_JSON_INTEGER)
    owner_id: UUID | None = None
    status: BusinessStatus = "draft"
    zp_min: int | None = Field(default=None, strict=True, ge=0, le=SAFE_JSON_INTEGER)
    zp_max: int | None = Field(default=None, strict=True, ge=0, le=SAFE_JSON_INTEGER)
    daily: str | None = Field(default=None, min_length=1, max_length=500)
    city: str | None = Field(default=None, min_length=1, max_length=300)
    type: WorkType | None = None
    bio: str = Field(default="", max_length=10000)
    domens: dict[SkillKey, Level] = Field(default_factory=dict, max_length=1000)
    stacks: dict[SkillKey, Level] = Field(default_factory=dict, max_length=1000)
    soft_skills: dict[SkillKey, Level] = Field(default_factory=dict, max_length=1000)
    class_key: Direction | None = Field(default=None, alias="class")
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)

    @field_validator("bio", mode="before", json_schema_input_type=str | None)
    @classmethod
    def normalize_bio(cls, value: object) -> object:
        """Allow upstream narrative clearing with null; canonical output stays a string."""
        return "" if value is None else value

    @field_validator("type", mode="before",
                     json_schema_input_type=WorkType | Literal["очно", "дистант", "гибрид"] | None)
    @classmethod
    def normalize_type(cls, value: object) -> object:
        """Accept the upstream Russian work-format labels as canonical enums."""
        return {"очно": "onsite", "дистант": "remote", "гибрид": "hybrid"}.get(value, value) \
            if isinstance(value, str) else value

    @field_validator("class_key", mode="before",
                     json_schema_input_type=Direction | Literal["back", "front", "ML"] | None)
    @classmethod
    def normalize_class(cls, value: object) -> object:
        """Map upstream class abbreviations to the shared candidate directions."""
        return {"back": "backend", "front": "frontend", "ML": "ml"}.get(value, value) \
            if isinstance(value, str) else value

    @field_validator("daily", "city")
    @classmethod
    def nonempty_daily(cls, value: str | None) -> str | None:
        """Whitespace-only schedules are invalid rather than silently accepted."""
        if value is not None and not value.strip():
            raise ValueError("schedule and city must not be blank")
        return value

    @model_validator(mode="after")
    def bounded_snapshot(self) -> "VacancyInput":
        """Reject reversed salary bounds and unbounded nested metadata before persistence."""
        if self.zp_min is not None and self.zp_max is not None and self.zp_min > self.zp_max:
            raise ValueError("zp_max must be greater than or equal to zp_min")
        if len(json.dumps(self.metadata, ensure_ascii=False).encode()) > 65536:
            raise ValueError("metadata exceeds 64 KiB")
        return self


class VacancyPipelineStatus(Contract):
    """Business status and artifact state are independent, revision-safe dimensions."""

    vacancy_id: UUID
    revision: int
    source_revision: int
    status: BusinessStatus
    state: PipelineState
    missing_requirements: list[str]
    strict_schema_version: int | None = None
    attempt_count: int = 0
    error_code: str | None = None


class VacancyReady(VacancyInput):
    """Complete selection input; omitted coordinates remain distinct from explicit zero."""

    vacancy_id: UUID
    revision: int
    owner_id: UUID
    status: Literal["open"]
    zp_min: int
    zp_max: int
    daily: str
    city: str
    type: WorkType
    build_mode: BuildMode
    strict_vector: list[float]
    requirement_mask: list[bool]
    strict_schema_version: int
    strict_schema_hash: str
    semantic_vector: list[float] | None
    semantic_model_version: str | None
    semantic_space_id: str | None
    semantic_available: bool
    published_at: datetime


class VacancyReadyPage(Contract):
    """Bounded page containing only current, active-schema, ready vacancies."""

    items: list[VacancyReady]
    limit: int
    offset: int


def missing_requirements(vacancy: VacancyInput) -> list[str]:
    """Require class or domain/stack criteria; soft skills and prose cannot qualify alone."""
    return [key for key, present in (
        ("owner_id", vacancy.owner_id is not None),
        ("zp_min", vacancy.zp_min is not None),
        ("zp_max", vacancy.zp_max is not None),
        ("daily", vacancy.daily is not None),
        ("city", vacancy.city is not None),
        ("type", vacancy.type is not None),
        ("class_or_domens_or_stacks", vacancy.class_key is not None or bool(vacancy.domens or vacancy.stacks)),
        ("status_open", vacancy.status == "open"),
    ) if not present]


def build_mode(vacancy: VacancyInput) -> BuildMode:
    """Describe which explicit selection channels are available, including optional soft skills."""
    requirements = bool(vacancy.domens or vacancy.stacks or vacancy.soft_skills)
    if vacancy.class_key is not None:
        return "mixed" if requirements else "class_only"
    return "requirements_only"
