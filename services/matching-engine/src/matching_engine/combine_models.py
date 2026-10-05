"""Versioned main-selection inputs and contact-free results before reranking."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from matching_engine.models import (
    Category,
    ClaimStatus,
    Contract,
    PoolEntry,
    SalaryRange,
    WorkType,
)


class ScoringPolicy(Contract):
    """Manual initial weights; policy snapshots belong to each durable run, not an LLM."""

    version: str = Field(default="main-v1", min_length=1, max_length=100)
    rating_weight: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    strict_weight: float = Field(default=0.35, ge=0, le=1, allow_inf_nan=False)
    semantic_correction: float = Field(default=0.10, ge=0, le=0.20, allow_inf_nan=False)
    confidence: dict[ClaimStatus, float] = Field(default_factory=lambda: {
        "confirmed": 1.0, "partial": 0.8, "self_reported": 0.6,
        "pending": 0.5, "rejected": 0.0,
    })

    @model_validator(mode="after")
    def validate_weights(self) -> "ScoringPolicy":
        """Require complete bounded evidence weights and a dominant, nonzero rank channel."""
        if self.rating_weight <= self.strict_weight or self.strict_weight < 0:
            raise ValueError("rating weight must exceed strict weight")
        if set(self.confidence) != {"confirmed", "partial", "self_reported", "pending", "rejected"}:
            raise ValueError("all evidence statuses require an explicit confidence")
        if any(not 0 <= value <= 1 for value in self.confidence.values()):
            raise ValueError("evidence confidence must be finite and between zero and one")
        if self.confidence["rejected"] != 0 or self.confidence["confirmed"] != 1:
            raise ValueError("confirmed and rejected confidence must remain one and zero")
        return self


class MatchCandidate(PoolEntry):
    """Internal matching projection, excluding contact/private narrative fields."""

    class_size: int = Field(ge=1)
    rating_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    work_types: list[WorkType] | None = None
    relocation_ready: bool | None = None
    relocation_cities: list[str] = Field(default_factory=list)


class FilterDecision(Contract):
    """Machine-readable exclusions; missing preferences never imply consent."""

    passed: bool
    reasons: list[str]


class MatchedFeature(Contract):
    """One explicit target and candidate evidence, with its computed strict contribution."""

    index: int
    group: str
    key: str
    target_level: float
    candidate_level: float
    status: ClaimStatus | None
    agreement: float
    confidence: float
    contribution: float


class ScoreBreakdown(Contract):
    """Available-channel weights, bounded semantic delta and reproducible main score."""

    rating_score: float
    strict_score: float | None
    semantic_cosine: float | None
    semantic_unavailable_reason: str | None
    rating_contribution: float
    strict_contribution: float
    semantic_contribution: float
    score: float
    top_matched_features: list[MatchedFeature]


class CandidateCard(Contract):
    """Anonymous main shortlist card; position is provisional until the future reranker."""

    candidate_id: UUID
    profile_revision: int
    position: int
    display_name: str
    city: str
    salary: SalaryRange
    category: Category
    rank: int
    confirmed_strength: int
    resume_url: str
    explanation: ScoreBreakdown


class MainSelectionResult(Contract):
    """All-pool filter counts and top-k main cards, without discarded-tail reranking."""

    scanned_count: int
    passed_count: int
    filter_counts: dict[str, int]
    items: list[CandidateCard]


class CombineRequest(Contract):
    """Bounded main shortlist size; optional caller key prevents duplicate manual submissions."""

    top_k: int = Field(default=50, strict=True, ge=1, le=500)
    idempotency_key: UUID | None = None


class CombineStatus(Contract):
    """Exact vacancy, registry, pool and scoring-policy generations of one durable run."""

    run_id: UUID
    vacancy_id: UUID
    vacancy_revision: int
    strict_schema_version: int
    strict_schema_hash: str
    pool_generation: int
    policy_version: str
    policy_hash: str
    stage: Literal["main_selection"] = "main_selection"
    state: Literal["queued", "processing", "retrying", "failed", "ready", "obsolete"]
    top_k: int
    attempt_count: int = 0
    error_code: str | None = None
    scanned_count: int = 0
    passed_count: int = 0
    selected_count: int = 0
    filter_counts: dict[str, int] = Field(default_factory=dict)
    published_at: datetime | None = None


class SelectionPage(Contract):
    """Only current, live main cards; stale pool/vacancy/schema generations cannot be served."""

    run: CombineStatus
    items: list[CandidateCard]
    limit: int
    offset: int


class DeliveryStatus(Contract):
    """Persisted acknowledgement counts for the two outbox streams and combine queue."""

    candidate_pending: int
    vacancy_pending: int
    acknowledged_count: int
    queued_count: int
    quarantined_count: int = 0
    retrying_count: int = 0


class DeliveryRetryRequest(Contract):
    """Explicitly retry a quarantined/backing-off local outbox event after repairing its cause."""

    source: Literal["candidate", "vacancy"]
    event_id: int = Field(strict=True, ge=1, le=2**53 - 1)


class SelectionStateInput(Contract):
    """Server-side handoff for exclusion state; visited is permanently terminal for this vacancy."""

    state: Literal["eligible", "visited", "pending", "attractive"]


class SelectionState(SelectionStateInput):
    """Persisted exclusion state, independent of disposable shortlist generations."""

    vacancy_id: UUID
    candidate_id: UUID
