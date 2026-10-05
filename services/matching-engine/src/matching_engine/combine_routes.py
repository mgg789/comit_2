"""Protected main-selection API; exclusion handoff requires the internal pipeline token."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from matching_engine.combine_models import (
    CombineRequest,
    CombineStatus,
    DeliveryRetryRequest,
    DeliveryStatus,
    SelectionPage,
    SelectionState,
    SelectionStateInput,
)
from matching_engine.combine_store import CombineStore


def combine_router(repository: CombineStore | None, require_internal: Callable) -> APIRouter:
    """Reuse existing authentication and publish typed routes in a separate static namespace."""
    router = APIRouter(tags=["main-selection"])

    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> CombineStore:
        if repository is None:
            raise HTTPException(status_code=503, detail="matching database is not configured")
        return repository

    @router.post("/internal/vacancies/{vacancy_id}/combine", response_model=CombineStatus)
    def request(vacancy_id: UUID, payload: CombineRequest,
                store: Annotated[CombineStore, Depends(storage)]) -> CombineStatus:
        """Immediately queue one exact snapshot; repeated caller keys preserve the original run."""
        return store.request(vacancy_id, payload)

    @router.get("/internal/vacancies/{vacancy_id}/combine", response_model=CombineStatus)
    def status(vacancy_id: UUID, store: Annotated[CombineStore, Depends(storage)]) -> CombineStatus:
        """Read durable run progress and report obsolete input generations."""
        return store.status(vacancy_id)

    @router.post("/internal/vacancies/{vacancy_id}/combine/retry", response_model=CombineStatus)
    def retry(vacancy_id: UUID, store: Annotated[CombineStore, Depends(storage)]) -> CombineStatus:
        """Explicitly restart terminal failure with bounded attempts or refresh obsolete inputs."""
        return store.retry(vacancy_id)

    @router.get("/internal/vacancies/{vacancy_id}/selections", response_model=SelectionPage)
    def selections(vacancy_id: UUID, store: Annotated[CombineStore, Depends(storage)],
                   limit: int = Query(default=100, ge=1, le=500),
                   offset: int = Query(default=0, ge=0)) -> SelectionPage:
        """Return a page from exactly one valid ready run, with no contact information."""
        return store.selections(vacancy_id, limit, offset)

    @router.put("/internal/vacancies/{vacancy_id}/selection-state/{candidate_id}", response_model=SelectionState)
    def selection_state(vacancy_id: UUID, candidate_id: UUID, payload: SelectionStateInput,
                        store: Annotated[CombineStore, Depends(storage)]) -> SelectionState:
        """Persist an internal Tinder exclusion handoff and invalidate the former batch."""
        return store.set_selection_state(vacancy_id, candidate_id, payload)

    @router.get("/internal/matching/events", response_model=DeliveryStatus)
    def events(store: Annotated[CombineStore, Depends(storage)]) -> DeliveryStatus:
        """Read pending, acknowledged, retrying and quarantined durable delivery totals."""
        return store.delivery_status()

    @router.post("/internal/matching/events/retry", response_model=DeliveryStatus)
    def retry_event(payload: DeliveryRetryRequest,
                    store: Annotated[CombineStore, Depends(storage)]) -> DeliveryStatus:
        """Release one quarantined/retrying event for bounded durable delivery."""
        return store.retry_event(payload.source, payload.event_id)

    return router
