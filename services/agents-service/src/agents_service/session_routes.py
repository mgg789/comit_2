"""Backend chat endpoints; interactions are DTOs for the future client, without UI."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from agents_service.agent_bridge import AtomicAgentStore, DraftGateway
from agents_service.agent_models import (
    AgentSession,
    AgentStatusView,
    InteractionResponse,
    UserTurnRequest,
)
from agents_service.agent_runtime import AgentRuntime, AgentRuntimeError
from agents_service.database import connect
from agents_service.models import JobView
from agents_service.providers import DeepSeekClient, MatchingClient
from agents_service.store import ConflictError, Store


class SessionCreate(BaseModel):
    """Bind a new conversation to an existing accounting-owner draft."""

    model_config = ConfigDict(extra="forbid")
    draft_id: UUID


def _runtime(connection, db, schema=None, matching=None) -> AgentRuntime:
    return AgentRuntime(AtomicAgentStore(connection), DraftGateway(db, connection, schema),
                        DeepSeekClient(), matching or MatchingClient())


def _enqueue(connection, db: Store, owner_id: UUID, turn) -> JobView:
    return db.enqueue_in_connection(connection, owner_id=owner_id, kind="agent_turn", payload={"turn_id": str(turn.turn_id)},
                         idempotency_key=f"turn:{turn.turn_id}", draft_id=turn.draft_id,
                         session_id=turn.session_id, max_attempts=5)


def retry_agent_job(db: Store, owner_id: UUID, job_id: UUID) -> JobView:
    """Reset a failed generic job and its turn atomically, preserving tool checkpoints."""
    with connect(db.database_url) as connection:
        row = connection.execute("SELECT * FROM agents_service.jobs WHERE owner_id=%s AND job_id=%s FOR UPDATE",
                                 (owner_id, job_id)).fetchone()
        if row is None or row["kind"] != "agent_turn":
            raise HTTPException(404, "agent_job_not_found")
        if row["state"] not in ("failed", "retrying"):
            raise ConflictError("only failed or retrying jobs can be retried")
        _runtime(connection, db).retry_turn(owner_id, UUID(row["payload"]["turn_id"]))
        updated = connection.execute("UPDATE agents_service.jobs SET state='queued',attempts=0,error=NULL,"
            "available_at=now(),lease_token=NULL,lease_until=NULL,updated_at=now() WHERE job_id=%s RETURNING *", (job_id,)).fetchone()
        return db._job(updated)


def register_session_routes(app: FastAPI, owner_dependency, database_dependency, matching=None) -> None:
    """Register owner-scoped sessions, turns and checked interaction responses."""
    Owner = Annotated[UUID, Depends(owner_dependency)]
    Database = Annotated[Store, Depends(database_dependency)]
    engine = matching or MatchingClient()

    @app.exception_handler(AgentRuntimeError)
    async def runtime_error(_request, error: AgentRuntimeError):
        status = 404 if error.code == "not_found" else 503 if error.retryable else 409
        return JSONResponse(status_code=status, content={"detail": error.code})

    @app.post("/internal/agents/sessions", response_model=AgentSession)
    def create(request: SessionCreate, owner_id: Owner, db: Database):
        """Create a persisted session pinned to the agent definition and knowledge."""
        with connect(db.database_url) as connection:
            runtime = _runtime(connection, db)
            draft = runtime.drafts.get_draft(owner_id, request.draft_id)
            if draft.state in ("creating", "created", "failed"):
                raise HTTPException(409, "draft_is_frozen")
            return runtime.create_session(owner_id, draft.draft_id, draft.revision)

    @app.get("/internal/agents/sessions/{session_id}", response_model=AgentStatusView)
    def status(session_id: UUID, owner_id: Owner, db: Database):
        """Read real durable progress plus current shared draft and interaction DTOs."""
        with connect(db.database_url) as connection:
            return _runtime(connection, db).get_status(owner_id, session_id)

    @app.post("/internal/agents/sessions/{session_id}/turns", response_model=JobView)
    def turn(session_id: UUID, request: UserTurnRequest, owner_id: Owner, db: Database):
        """Persist one user turn and its worker job in the same transaction."""
        with connect(db.database_url) as connection:
            runtime = _runtime(connection, db)
            session = runtime.store.get_session(session_id, owner_id, for_update=True)
            if session is None:
                raise HTTPException(404, "agent_session_not_found")
            value = runtime.append_user_turn(owner_id, session_id, session.draft_id, request)
            return _enqueue(connection, db, owner_id, value)

    @app.post("/internal/agents/sessions/{session_id}/interactions/{interaction_id}", response_model=JobView)
    def interaction(session_id: UUID, interaction_id: UUID, request: InteractionResponse,
                    owner_id: Owner, db: Database):
        """Apply a saved allowed interaction response and enqueue its continuation atomically."""
        # Schema network call is outside the transaction holding session/draft rows.
        schema = engine.schema()
        with connect(db.database_url) as connection:
            runtime = _runtime(connection, db, schema)
            session = runtime.store.get_session(session_id, owner_id, for_update=True)
            if session is None:
                raise HTTPException(404, "agent_session_not_found")
            value = runtime.respond_interaction(owner_id, session_id, interaction_id, request,
                                                idempotency_key=f"interaction:{interaction_id}")
            return _enqueue(connection, db, owner_id, value)
