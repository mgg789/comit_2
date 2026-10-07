"""Internal-token-protected vacancy preparation routes, without executing the combiner."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from matching_engine.vacancy_models import (
    VacancyInput,
    VacancyPipelineStatus,
    VacancyReady,
    VacancyReadyPage,
)
from matching_engine.vacancy_store import VacancyStore


def vacancy_router(repository: VacancyStore | None, require_internal: Callable) -> APIRouter:
    """Reuse the candidate authentication boundary while keeping vacancy storage independent."""
    router = APIRouter(prefix="/internal/vacancies", tags=["vacancy-pipeline"])

    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> VacancyStore:
        if repository is None:
            raise HTTPException(status_code=503, detail="vacancy pipeline database is not configured")
        return repository

    @router.get("/ready", response_model=VacancyReadyPage)
    def page(store: Annotated[VacancyStore, Depends(storage)],
             limit: int = Query(default=100, ge=1, le=500),
             offset: int = Query(default=0, ge=0),
             cursor: str | None = Query(default=None, max_length=1024)) -> VacancyReadyPage:
        """Page only current eligible artifacts in the active strict generation."""
        return store.page(limit, offset, cursor)

    @router.put("/{vacancy_id}", response_model=VacancyPipelineStatus)
    async def intake(vacancy_id: UUID, vacancy: VacancyInput, request: Request,
                     store: Annotated[VacancyStore, Depends(storage)]) -> VacancyPipelineStatus:
        """Persist the canonical snapshot and original wire values, then queue eligible vacancies."""
        raw = await request.json()
        try:
            return await run_in_threadpool(store.intake, vacancy_id, vacancy, raw_input=raw)
        except ValueError as exception:
            raise HTTPException(status_code=422, detail=str(exception)) from exception

    @router.get("/{vacancy_id}", response_model=VacancyPipelineStatus)
    def status(vacancy_id: UUID, store: Annotated[VacancyStore, Depends(storage)]) -> VacancyPipelineStatus:
        """Read pipeline progress separately from business lifecycle status."""
        return store.status(vacancy_id)

    @router.get("/{vacancy_id}/ready", response_model=VacancyReady)
    def ready(vacancy_id: UUID, store: Annotated[VacancyStore, Depends(storage)]) -> VacancyReady:
        """Read one completed preparation snapshot; absent readiness returns 404."""
        return store.ready_entry(vacancy_id)

    @router.post("/{vacancy_id}/retry", response_model=VacancyPipelineStatus)
    def retry(vacancy_id: UUID, store: Annotated[VacancyStore, Depends(storage)]) -> VacancyPipelineStatus:
        """Explicitly restart a failed or backing-off current preparation job."""
        return store.retry(vacancy_id)

    return router
