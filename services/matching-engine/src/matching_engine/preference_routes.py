"""Owner-scoped preference status and reversible operator controls."""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field

from matching_engine.models import Contract
from matching_engine.preference_store import PreferenceStore


class PreferenceStatus(Contract):
    """Operational state without coefficients, personal feature payloads or participant identifiers."""

    vacancy_id: UUID
    revision: int
    enabled: bool
    state: str
    active_model_hash: str | None
    eligible_reactions: int
    fresh_reactions: int
    job_id: UUID | None
    job_state: str | None
    error_code: str | None
    policy_hash: str
    selection_bias: Literal["feedback_unknown_global_propensity", "deterministic_feedback_unknown_propensity"]


class PreferenceCommand(Contract):
    """CAS mutation with an owner-scoped replay key; retries never bypass the quality gate."""

    idempotency_key: UUID
    expected_revision: int = Field(ge=0)


def preference_router(repository: PreferenceStore | None, require_internal: Callable) -> APIRouter:
    """Build authenticated vacancy controls; source identity is always derived from trusted owner context."""
    router = APIRouter(tags=["vacancy-preferences"])

    def owner_store(_authenticated: Annotated[object, Depends(require_internal)],
                    x_owner_id: Annotated[UUID, Header(alias="X-Owner-ID")]) -> tuple[PreferenceStore, UUID]:
        if repository is None:
            raise HTTPException(status_code=503, detail="preference database is not configured")
        return repository, x_owner_id

    @router.get("/internal/vacancies/{vacancy_id}/preferences", response_model=PreferenceStatus)
    def status(vacancy_id: UUID, services: Annotated[tuple[PreferenceStore, UUID], Depends(owner_store)]) -> dict:
        """Read collection/fit/activation progress for one owned vacancy."""
        store, owner_id = services
        return store.status(owner_id, vacancy_id)

    @router.post("/internal/vacancies/{vacancy_id}/preferences/reset", response_model=PreferenceStatus)
    def reset(vacancy_id: UUID, payload: PreferenceCommand,
              services: Annotated[tuple[PreferenceStore, UUID], Depends(owner_store)]) -> dict:
        """Return to baseline and restart collection strictly after the reset timestamp."""
        store, owner_id = services
        return store.command(owner_id, vacancy_id, "reset", payload.idempotency_key, payload.expected_revision)

    @router.post("/internal/vacancies/{vacancy_id}/preferences/rollback", response_model=PreferenceStatus)
    def rollback(vacancy_id: UUID, payload: PreferenceCommand,
                 services: Annotated[tuple[PreferenceStore, UUID], Depends(owner_store)]) -> dict:
        """Disable preference learning/application until an explicit reset re-enables it."""
        store, owner_id = services
        return store.command(owner_id, vacancy_id, "rollback", payload.idempotency_key, payload.expected_revision)

    @router.post("/internal/vacancies/{vacancy_id}/preferences/retry", response_model=PreferenceStatus)
    def retry(vacancy_id: UUID, payload: PreferenceCommand,
              services: Annotated[tuple[PreferenceStore, UUID], Depends(owner_store)]) -> dict:
        """Retry a bounded failed sealed fit; rejected models require fresh reactions."""
        store, owner_id = services
        return store.command(owner_id, vacancy_id, "retry", payload.idempotency_key, payload.expected_revision)

    return router
