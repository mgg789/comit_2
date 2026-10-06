"""Owner-scoped experiment controls and safe summaries without participant facts."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from matching_engine.hypothesis_store import HypothesisStore
from matching_engine.models import Contract
from matching_engine.preference_routes import PreferenceCommand

HypothesisId = Literal["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]


class HypothesisSummary(Contract):
    """A fixed twenty-decisive-pair batch; diagnostics remain local and uncalibrated."""

    hypothesis_id: HypothesisId
    status: Literal["unclear", "supported", "not_supported", "not_testable"]
    supporting: int
    opposing: int
    decisive: int
    total: int
    observed_decisive: int
    post_batch_decisive: int
    batch_status: Literal["collecting", "frozen", "invalidated"]
    confidence_interval: tuple[float, float]
    minimum_decisive_pairs: int
    familywise_alpha: float
    multiple_testing_families: int
    policy_hash: str
    calibration_status: Literal["uncalibrated_baseline"]


class HypothesisStatus(Contract):
    """Collection and bounded search state, excluding IDs, facts and feature payloads."""

    vacancy_id: UUID
    revision: int
    enabled: bool
    context_hash: str | None
    policy_hash: str
    hypotheses: list[HypothesisSummary]
    search_report: dict[str, object]
    pairs: int


def hypothesis_router(repository: HypothesisStore | None, require_internal: Callable) -> APIRouter:
    """Build internal routes; employer ownership is derived by the authenticated gateway."""
    router = APIRouter(tags=["vacancy-hypotheses"])

    def services(_authenticated: Annotated[object, Depends(require_internal)],
                 owner_id: Annotated[UUID, Header(alias="X-Owner-ID")]) -> tuple[HypothesisStore, UUID]:
        if repository is None:
            raise HTTPException(status_code=503, detail="hypothesis database is not configured")
        return repository, owner_id

    @router.get("/internal/vacancies/{vacancy_id}/hypotheses", response_model=HypothesisStatus)
    def status(vacancy_id: UUID, service: Annotated[tuple[HypothesisStore, UUID], Depends(services)]) -> dict:
        """Read hypotheses and incomplete comparisons for one owned vacancy."""
        store, owner_id = service
        return store.status(owner_id, vacancy_id)

    @router.post("/internal/vacancies/{vacancy_id}/hypotheses/{action}", response_model=HypothesisStatus)
    def command(vacancy_id: UUID, action: Literal["pause", "resume", "reset"], payload: PreferenceCommand,
                service: Annotated[tuple[HypothesisStore, UUID], Depends(services)]) -> dict:
        """Pause new exploration, resume or discard old evidence with CAS and UUID replay."""
        store, owner_id = service
        return store.command(owner_id, vacancy_id, action, payload.idempotency_key, payload.expected_revision)

    return router
