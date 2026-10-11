"""Token-protected employer lifecycle cleanup for the authenticated API gateway."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from matching_engine.vacancy_store import VacancyStore


def employer_router(repository: VacancyStore | None, require_internal: Callable) -> APIRouter:
    """Expose idempotent owner deletion using the existing internal authentication boundary."""
    router = APIRouter(prefix="/internal/employers", tags=["employer-lifecycle"])

    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> VacancyStore:
        if repository is None:
            raise HTTPException(status_code=503, detail="vacancy pipeline database is not configured")
        return repository

    @router.delete("/{owner_id}")
    def delete(owner_id: UUID, store: Annotated[VacancyStore, Depends(storage)]) -> dict[str, object]:
        """Erase the owner matching projection and return its durable replay receipt."""
        return store.delete_employer(owner_id)

    return router
