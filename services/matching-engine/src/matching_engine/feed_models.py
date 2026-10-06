"""Typed contracts for the employer's durable, revision-safe candidate feed."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from matching_engine.combine_models import CandidateCard
from matching_engine.models import Contract


class FeedRequest(Contract):
    """Open one immutable feed session for a vacancy owned by the caller."""

    include_reserve: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class ShowcaseLikeRequest(Contract):
    """Manual like binds the explicitly viewed candidate revision to one vacancy."""

    candidate_id: UUID
    profile_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID


class FeedCard(CandidateCard):
    """A served exposure with immutable source identity and feedback metadata."""

    exposure_id: UUID
    session_id: UUID
    kind: Literal["recommended", "reserve", "showcase"]
    main_position: int
    rerank_position: int | None
    mode: Literal["learned", "main_fallback", "manual"]
    rerank_score: float | None
    rerank_contributions: dict[str, float] = Field(default_factory=dict)
    artifact_hash: str | None
    activation_revision: int = 0
    approval_receipt_hash: str | None = None
    activation_kind: Literal["gated", "manual_override", "none"] = "none"
    served_at: datetime | None = None
    decision: Literal["like", "dislike"] | None = None
    history_badges: list[str] = Field(default_factory=list)
    change_assessment: dict[str, object] | None = None
    preference_model_hash: str | None = None
    preference_revision: int = 0
    preference_position: int | None = None
    preference_score: float | None = None
    preference_adjustment: float = 0.
    preference_contributions: dict[str, float] = Field(default_factory=dict)
    preference_state: Literal["baseline", "personalized"] = "baseline"
    order_revision: int = 0


class FeedPage(Contract):
    """Stable position-cursor page from one 24-hour immutable session."""

    session_id: UUID
    vacancy_id: UUID
    vacancy_revision: int
    policy_hash: str
    expires_at: datetime
    cursor: str | None
    next_cursor: str | None
    has_more: bool
    items: list[FeedCard]
    order_revision: int = 0


class FeedDecisionRequest(Contract):
    """One explicit action bound to a served exposure and caller idempotency key."""

    exposure_id: UUID
    action: Literal["like", "dislike"]
    idempotency_key: UUID


class FeedImpressionRequest(Contract):
    """Client acknowledgement of a visible card; delivery alone does not prove a view."""

    exposure_id: UUID
    idempotency_key: UUID


class FeedImpression(Contract):
    """Owner-scoped first visible-impression receipt, without candidate feature payloads."""

    event_id: UUID
    exposure_id: UUID
    recorded_at: datetime
    replayed: bool = False


class FeedDecision(Contract):
    """Durable action receipt; replay returns the same receipt without reapplying it."""

    receipt_id: UUID
    exposure_id: UUID
    vacancy_id: UUID
    candidate_id: UUID
    action: Literal["like", "dislike"]
    idempotency_key: UUID
    saved: bool
    visited: bool
    replayed: bool = False
    history_badges: list[str] = Field(default_factory=list)


class ShowcaseLikeReceipt(FeedDecision):
    """Like receipt records the exact public revision selected outside the ranked feed."""

    profile_revision: int
    selection_source: Literal["showcase"] = "showcase"


class SavedCard(Contract):
    """An employer-scoped saved candidate projection with its source revision."""

    vacancy_id: UUID
    candidate_id: UUID
    profile_revision: int
    exposure_id: UUID
    saved_at: datetime
    card: FeedCard | None


class VisitedCard(Contract):
    """An employer-scoped visited baseline retaining history across re-impressions."""

    vacancy_id: UUID
    candidate_id: UUID
    profile_revision: int
    evaluated_revision: int
    state: Literal["eligible", "visited", "visited_updated", "pending", "attractive"]
    exposure_id: UUID | None
    baseline_origin: Literal["exposure", "legacy_migration"]
    visited_at: datetime | None
    updated_at: datetime
    history_badges: list[str] = Field(default_factory=list)
    assessment: dict[str, object] = Field(default_factory=dict)


class FeedListPage[T](Contract):
    """Offset page for saved or visited employer records."""

    items: list[T]
    limit: int
    offset: int
    has_more: bool
