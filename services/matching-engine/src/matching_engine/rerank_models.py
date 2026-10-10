"""Protected baseline reranking contracts and contact-free ordered result pages."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from matching_engine.combine_models import CandidateCard
from matching_engine.models import Contract


class RerankRequest(Contract):
    """Optional caller identity persists across retries without duplicating a run."""

    idempotency_key: UUID | None = None


class RerankStatus(Contract):
    """Exact immutable main/model identities; obsolete results are never served."""

    run_id: UUID
    main_run_id: UUID
    vacancy_id: UUID
    artifact_hash: str | None
    activation_revision: int = Field(default=0, ge=0)
    approval_receipt_hash: str | None = None
    activation_kind: Literal["gated", "manual_override", "none"] = "none"
    feature_schema_hash: str
    tail_policy_hash: str
    stage: Literal["baseline_rerank"] = "baseline_rerank"
    state: Literal["queued", "processing", "retrying", "failed", "ready", "obsolete"]
    mode: Literal["learned", "main_fallback"] | None = None
    fallback_reason: str | None = None
    attempt_count: int = 0
    error_code: str | None = None
    selected_count: int
    recommended_count: int = 0
    reserve_count: int = 0
    unranked_by_reranker_count: int
    published_at: datetime | None = None


class RerankCard(CandidateCard):
    """Original main factors remain visible alongside versioned R0 contributions."""

    main_position: int
    rerank_position: int | None
    mode: Literal["learned", "main_fallback"]
    kind: Literal["recommended", "reserve"]
    rerank_score: float | None
    rerank_contributions: dict[str, float] = Field(default_factory=dict)
    artifact_hash: str | None
    activation_revision: int = Field(default=0, ge=0)
    approval_receipt_hash: str | None = None
    activation_kind: Literal["gated", "manual_override", "none"] = "none"


class RerankPage(Contract):
    """One separate recommended/reserve page; candidates outside main top-k are absent."""

    run: RerankStatus
    kind: Literal["recommended", "reserve"]
    items: list[RerankCard]
    limit: int
    offset: int
