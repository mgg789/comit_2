"""Typed task scope and employer availability; candidate text never creates a grant."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from agents_service.organization_models import (
    OrganizationContract,
    OrganizationTaskKind,
)


class AvailabilitySlot(OrganizationContract):
    """A confirmed employer interval with explicit timezone and meeting location."""

    starts_at: datetime
    ends_at: datetime
    time_zone: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(default=60, strict=True, ge=5, le=240)
    format: str = Field(default="remote", pattern="^(remote|onsite)$")
    location: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="technical", pattern="^(technical|general)$")

    @model_validator(mode="after")
    def valid_interval(self):
        """Reject naive times and intervals too short for the proposed interview."""
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("availability_timezone_required")
        try:
            ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("availability_timezone_invalid") from error
        if not timedelta(minutes=self.duration_minutes) <= self.ends_at - self.starts_at <= timedelta(days=7):
            raise ValueError("availability_interval_invalid")
        return self


class TaskScope(OrganizationContract):
    """Bounded candidate processes, availability and expiry supplied by the employer."""

    process_ids: list[UUID] = Field(min_length=1, max_length=20)
    availability: list[AvailabilitySlot] = Field(default_factory=list, max_length=100)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def unique_processes(self):
        """Reject duplicated process scope and ambiguous expiry timestamps."""
        if len(self.process_ids) != len(set(self.process_ids)):
            raise ValueError("delegation_processes_duplicate")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("delegation_expiry_timezone_required")
        return self


def normalize_task_scope(kind: OrganizationTaskKind, raw: dict) -> dict:
    """Build server-owned grant fields from validated employer input; caller cannot choose authority."""
    raw = dict(raw)
    if "process_id" in raw and "process_ids" not in raw:
        raw["process_ids"] = [raw.pop("process_id")]
    scope = TaskScope.model_validate(raw)
    now = datetime.now(UTC)
    expiry = scope.expires_at or now + timedelta(days=7)
    if not now < expiry <= now + timedelta(days=30):
        raise ValueError("delegation_expiry_invalid")
    value = scope.model_dump(mode="json")
    value.update({"delegation_id": str(uuid4()), "expires_at": expiry.isoformat(),
                  "allowed_operations": ["candidate_chat_read", "candidate_chat_send"]
                  + (["interview_propose"] if kind == "schedule_interview" else [])})
    return value
