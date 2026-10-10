"""Trusted internal employer showcase routes, independent of vacancies."""
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from matching_engine.showcase_models import (
    PublicCandidateDetail,
    SearchPage,
    SearchRequest,
)
from matching_engine.showcase_store import ShowcaseStore


def showcase_router(repository: ShowcaseStore | None, require_internal: Callable) -> APIRouter:
    """Require service authentication before resolving trusted employer identity."""
    router = APIRouter(tags=["employer-showcase"])

    def owner_store(_authenticated: Annotated[object, Depends(require_internal)],
                    x_owner_id: Annotated[UUID, Header(alias="X-Owner-ID")]) -> tuple[ShowcaseStore, UUID]:
        """Fail closed when the showcase database is not configured."""
        if repository is None:
            raise HTTPException(503, "showcase database is not configured")
        return repository, x_owner_id

    @router.post("/internal/employer/candidates/search", response_model=SearchPage)
    def search(payload: SearchRequest, services: Annotated[tuple[ShowcaseStore, UUID], Depends(owner_store)]) -> SearchPage:
        """Search the consented public pool with explicit bounded filters."""
        store, owner_id = services
        return store.search(owner_id, payload)

    @router.get("/internal/employer/candidates/{candidate_id}", response_model=PublicCandidateDetail)
    def profile(candidate_id: UUID, services: Annotated[tuple[ShowcaseStore, UUID], Depends(owner_store)]) -> PublicCandidateDetail:
        """Read one current public profile without candidate contact access."""
        store, owner_id = services
        return store.profile(owner_id, candidate_id)

    return router
