"""Versioned main-selection inputs and contact-free results before reranking."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from matching_engine.models import (
    Category,
    ClaimStatus,
    Contract,
    DurationMonths,
    GradeStatus,
    NarrativeClaim,
    NarrativeRole,
    PoolEntry,
    SalaryRange,
    CandidateSalary,
    StrictBoolean,
    WorkType,
)


class SafeNarrativeReference(Contract):
    """Registry reference and evidence only; no arbitrary private strings."""

    key: str
    status: ClaimStatus


class SafeNarrativeValue[SafeFactValue](Contract):
    """Bounded typed value and evidence suitable for immutable matching snapshots."""

    value: SafeFactValue
    status: ClaimStatus


class PublicNarrativeFacts(Contract):
    """Safe typed facts projection, excluding titles, text, details and provenance."""

    domains: list[SafeNarrativeReference] = Field(default_factory=list)
    stacks: list[SafeNarrativeReference] = Field(default_factory=list)
    duration_months: SafeNarrativeValue[DurationMonths] | None = None
    role: SafeNarrativeValue[NarrativeRole] | None = None
    production_support: SafeNarrativeValue[StrictBoolean] | None = None


class SafeNarrativeFacts(Contract):
    """One typed narrative snapshot; rejected status is retained but earns no credit."""

    kind: Literal["project", "experience", "achievement"]
    status: ClaimStatus
    facts: PublicNarrativeFacts


def project_narrative_facts(claims: dict[str, list[NarrativeClaim]]) -> list[SafeNarrativeFacts]:
    """Strip private strings from parsed narrative groups; never infer missing facts.

    Keys are projects/experience/achievements. Rejected inputs remain auditable;
    the feature builder excludes rejected parent claims and individual facts.
    """
    output: list[SafeNarrativeFacts] = []
    for key, kind in (("projects", "project"), ("experience", "experience"), ("achievements", "achievement")):
        for claim in claims.get(key, []):
            if claim.facts is None:
                continue
            raw = claim.facts.model_dump()
            for group in ("domains", "stacks"):
                raw[group] = [{"key": ref["key"], "status": ref["status"]} for ref in raw[group]]
            for name in ("duration_months", "role", "production_support"):
                if raw[name] is not None:
                    raw[name] = {"value": raw[name]["value"], "status": raw[name]["status"]}
            output.append(SafeNarrativeFacts(kind=kind, status=claim.status,
                                             facts=PublicNarrativeFacts.model_validate(raw)))
    return output


class ScoringPolicy(Contract):
    """Manual initial weights; policy snapshots belong to each durable run, not an LLM."""

    version: str = Field(default="main-v2", min_length=1, max_length=100)
    rating_weight: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    strict_weight: float = Field(default=0.35, ge=0, le=1, allow_inf_nan=False)
    semantic_correction: float = Field(default=0.10, ge=0, le=0.20, allow_inf_nan=False)
    declared_grade_confidence: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
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
        if self.declared_grade_confidence >= 1:
            raise ValueError("self-reported grade confidence must be below approved confidence")
        return self


class MatchCandidate(PoolEntry):
    """Internal matching projection, excluding contact/private narrative fields."""

    class_size: int = Field(ge=1)
    rating_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    work_types: list[WorkType] | None = None
    relocation_ready: bool | None = None
    relocation_cities: list[str] = Field(default_factory=list)
    narrative_facts: list[SafeNarrativeFacts] = Field(default_factory=list)


class FilterDecision(Contract):
    """Machine-readable exclusions; missing preferences never imply consent."""

    passed: bool
    reasons: list[str]
    factors: list[str] = Field(default_factory=list)


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
    """Available-channel weights and reproducible score with raw rating provenance.

    ``rating_score`` is the raw class percentile. ``rating_contribution`` applies
    the grade-status confidence from the immutable policy snapshot, so a declared
    grade cannot look like a tested grade while remaining eligible for the pool.
    """

    rating_score: float
    strict_score: float | None
    semantic_cosine: float | None
    semantic_unavailable_reason: str | None
    rating_contribution: float
    strict_contribution: float
    semantic_contribution: float
    score: float
    top_matched_features: list[MatchedFeature]
    grade_status: GradeStatus = "approved"
    grade_confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)


class CandidateCard(Contract):
    """Anonymous main shortlist card; position is provisional until the future reranker."""

    candidate_id: UUID
    profile_revision: int
    position: int
    display_name: str
    city: str
    salary: CandidateSalary
    category: Category
    rank: int
    confirmed_strength: int
    resume_url: str
    filter_factors: list[str] = Field(default_factory=list)
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
    """Trusted handoff cannot manually release visited; feed policy owns structural release."""

    state: Literal["eligible", "visited", "pending", "attractive"]


class SelectionState(SelectionStateInput):
    """Persisted exclusion state, independent of disposable shortlist generations."""

    state: Literal["eligible", "visited", "pending", "attractive", "visited_updated"]

    vacancy_id: UUID
    candidate_id: UUID
