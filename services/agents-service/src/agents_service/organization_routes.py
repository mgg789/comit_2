"""Owner-scoped FastAPI routes for the global organization agent."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import Field

from .organization_delegation import AvailabilitySlot
from .organization_models import (
    OrganizationBudget,
    OrganizationChat,
    OrganizationContext,
    OrganizationContract,
    OrganizationMemory,
    OrganizationMemoryPatch,
    OrganizationMessagePage,
    OrganizationMessageRequest,
    OrganizationTask,
    OrganizationTaskRequest,
    OrganizationTurn,
    PendingAnswer,
    PendingAnswerRequest,
    PresenceUpdate,
)
from .organization_store import (
    OrganizationConflict,
    OrganizationNotFound,
    OrganizationStore,
)


class TaskAvailabilityRequest(OrganizationContract):
    """Explicit employer slots for a paused scheduling task."""

    availability: list[AvailabilitySlot] = Field(min_length=1, max_length=100)


def _database_url(database: object) -> str:
    """Resolve the existing API Store dependency without importing its implementation."""
    value = getattr(database, "database_url", None)
    if not value:
        raise HTTPException(503, "agents_database_not_configured")
    return str(value)


def _store(database: object) -> OrganizationStore:
    """Construct the organization persistence adapter from the API dependency."""
    return OrganizationStore(_database_url(database))


def register_organization_routes(app: FastAPI, owner_dependency, database_dependency, *, capability_check=None) -> None:
    """Register the durable global chat, task, context, memory and budget API."""
    Owner = Annotated[UUID, Depends(owner_dependency)]
    Database = Annotated[object, Depends(database_dependency)]
    from .availability import require_capability
    require_optional = capability_check or require_capability

    @app.get("/internal/organization-agent/chat", response_model=OrganizationChat)
    def chat(owner_id: Owner, database: Database) -> OrganizationChat:
        """Get or idempotently create the one global chat for this employer."""
        return _store(database).get_or_create_chat(owner_id)

    @app.get("/internal/organization-agent/messages", response_model=OrganizationMessagePage)
    def messages(owner_id: Owner, database: Database, after_sequence: int | None = Query(None, ge=-1),
                 limit: int = Query(50, ge=1, le=200)) -> OrganizationMessagePage:
        """Read a stable keyset page of owner messages."""
        return _store(database).list_messages(owner_id, after_sequence=after_sequence, limit=limit)

    @app.post("/internal/organization-agent/messages", response_model=OrganizationTurn, status_code=202)
    def message(request: OrganizationMessageRequest, owner_id: Owner, database: Database) -> OrganizationTurn:
        """Queue one idempotent user message for the background worker."""
        try:
            require_optional("agent")
            return _store(database).create_turn(owner_id, request.message, request.idempotency_key,
                                                reasoning_effort=request.reasoning_effort)
        except OrganizationConflict as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/internal/organization-agent/turns/{turn_id}", response_model=OrganizationTurn)
    def turn(turn_id: UUID, owner_id: Owner, database: Database) -> OrganizationTurn:
        """Read a turn only when it belongs to the authenticated employer."""
        value = _store(database).get_turn(owner_id, turn_id)
        if value is None:
            raise HTTPException(404, "organization_turn_not_found")
        return value

    @app.get("/internal/organization-agent/pending-answers", response_model=list[PendingAnswer])
    def pending_answers(owner_id: Owner, database: Database) -> list[PendingAnswer]:
        """Return durable unanswered questions awaiting employer input."""
        return _store(database).list_pending_answers(owner_id)

    @app.post("/internal/organization-agent/pending-answers/{answer_id}", response_model=PendingAnswer)
    def answer(answer_id: UUID, request: PendingAnswerRequest, owner_id: Owner, database: Database) -> PendingAnswer:
        """Persist one owner answer without copying it into editable memory."""
        try:
            require_optional("agent")
            return _store(database).answer_pending(owner_id, answer_id, request.answer)
        except OrganizationNotFound as error:
            raise HTTPException(404, str(error)) from error
        except OrganizationConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, "pending_answer_invalid") from error

    @app.get("/internal/organization-agent/tasks", response_model=list[OrganizationTask])
    def tasks(owner_id: Owner, database: Database, limit: int = Query(50, ge=1, le=200)) -> list[OrganizationTask]:
        """List delegated tasks scoped to the authenticated employer."""
        return _store(database).list_tasks(owner_id, limit=limit)

    @app.post("/internal/organization-agent/tasks", response_model=OrganizationTask, status_code=202)
    def task(request: OrganizationTaskRequest, owner_id: Owner, database: Database) -> OrganizationTask:
        """Persist explicit employer authorization for a delegated task."""
        store = _store(database)
        try:
            require_optional("agent")
            return store.create_task(owner_id, request)
        except OrganizationConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, "invalid_delegation_scope") from error

    @app.post("/internal/organization-agent/tasks/{task_id}/availability", response_model=OrganizationTask)
    def availability(task_id: UUID, request: TaskAvailabilityRequest, owner_id: Owner, database: Database) -> OrganizationTask:
        """Resume interview negotiation after the employer confirms available intervals."""
        try:
            require_optional("agent")
            return _store(database).resume_task(owner_id, task_id, [slot.model_dump(mode="json") for slot in request.availability])
        except OrganizationConflict as error:
            raise HTTPException(409, str(error)) from error

    @app.delete("/internal/organization-agent/tasks/{task_id}", response_model=OrganizationTask)
    def stop_task(task_id: UUID, owner_id: Owner, database: Database) -> OrganizationTask:
        """Stop a queued or active task under owner scope."""
        try:
            return _store(database).stop_task(owner_id, task_id)
        except OrganizationNotFound as error:
            raise HTTPException(404, str(error)) from error

    @app.put("/internal/organization-agent/presence", response_model=OrganizationChat)
    @app.post("/internal/organization-agent/presence", response_model=OrganizationChat)
    def presence(request: PresenceUpdate, owner_id: Owner, database: Database) -> OrganizationChat:
        """Record an explicit employer heartbeat; no absence is inferred from messages."""
        return _store(database).set_presence(owner_id, request.present)

    @app.get("/internal/organization-agent/context", response_model=OrganizationContext)
    def context(owner_id: Owner, database: Database) -> OrganizationContext:
        """Read current compact runtime context, separate from owner memory."""
        return _store(database).get_context(owner_id)

    @app.get("/internal/organization-agent/memory", response_model=OrganizationMemory)
    def memory(owner_id: Owner, database: Database) -> OrganizationMemory:
        """Read editable owner memory under owner scope."""
        return _store(database).get_memory(owner_id)

    @app.patch("/internal/organization-agent/memory", response_model=OrganizationMemory)
    def patch_memory(request: OrganizationMemoryPatch, owner_id: Owner, database: Database) -> OrganizationMemory:
        """CAS update editable memory; stale revisions are rejected."""
        try:
            return _store(database).patch_memory(owner_id, request.expected_revision, request.value)
        except OrganizationConflict as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/internal/organization-agent/budget", response_model=OrganizationBudget)
    def budget(owner_id: Owner, database: Database) -> OrganizationBudget:
        """Return exact rolling seven-day spend and outstanding reservations."""
        from .organization_runtime import Pricing
        try:
            Pricing.from_env()
            configured = True
        except ValueError:
            configured = False
        return _store(database).get_budget(owner_id, pricing_configured=configured)
