"""Internal-token API for durable baseline reranking and separate reserve retrieval."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from matching_engine.rerank_models import RerankPage, RerankRequest, RerankStatus
from matching_engine.rerank_store import RerankStore


def rerank_router(repository: RerankStore | None, require_internal: Callable) -> APIRouter:
    """Reuse trusted-service authentication; model training/promotion is never exposed over HTTP."""
    router = APIRouter(tags=["baseline-rerank"])

    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> RerankStore:
        if repository is None:
            raise HTTPException(status_code=503, detail="rerank database is not configured")
        return repository

    @router.post("/internal/vacancies/{vacancy_id}/rerank", response_model=RerankStatus)
    def request(vacancy_id: UUID, payload: RerankRequest,
                store: Annotated[RerankStore, Depends(storage)]) -> RerankStatus:
        """Queue one versioned top-k snapshot; optional caller keys persist across restart."""
        return store.request(vacancy_id, payload)

    @router.get("/internal/vacancies/{vacancy_id}/rerank", response_model=RerankStatus)
    def status(vacancy_id: UUID, store: Annotated[RerankStore, Depends(storage)]) -> RerankStatus:
        """Read progress including explicit fallback and stale snapshot state."""
        return store.status(vacancy_id)

    @router.post("/internal/vacancies/{vacancy_id}/rerank/retry", response_model=RerankStatus)
    def retry(vacancy_id: UUID, store: Annotated[RerankStore, Depends(storage)]) -> RerankStatus:
        """Refresh current inputs or reset bounded terminal failures."""
        return store.retry(vacancy_id)

    @router.get("/internal/vacancies/{vacancy_id}/rerank/selections", response_model=RerankPage)
    def selections(vacancy_id: UUID, store: Annotated[RerankStore, Depends(storage)],
                   kind: Literal["recommended", "reserve"] = "recommended",
                   limit: int = Query(default=100, ge=1, le=500),
                   offset: int = Query(default=0, ge=0)) -> RerankPage:
        """Return a page from a single valid run and disposition, preserving main explanations."""
        return store.selections(vacancy_id, kind, limit, offset)

    return router
