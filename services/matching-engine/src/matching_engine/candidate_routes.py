"""Internal candidate lifecycle routes used by the authenticated API gateway."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from matching_engine.store import ConflictError, Store


def candidate_router(repository: Store | None, require_internal: Callable) -> APIRouter:
    """Expose only token-protected candidate deletion; reads remain on existing routes."""
    router = APIRouter(prefix="/internal/candidates", tags=["candidate-pipeline"])

    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> Store:
        if repository is None:
            raise HTTPException(status_code=503, detail="pipeline database is not configured")
        return repository

    @router.delete("/{candidate_id}")
    def delete(candidate_id: UUID, store: Annotated[Store, Depends(storage)]) -> dict[str, object]:
        """Delete one candidate projection idempotently and leave a replay tombstone."""
        try:
            return store.delete(candidate_id)
        except ConflictError as exception:
            raise HTTPException(status_code=409, detail=str(exception)) from exception

    return router
