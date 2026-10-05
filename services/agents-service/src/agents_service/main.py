"""Protected editorial API; no account authentication or browser UI in this cycle."""

import hmac
import os
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from agents_service.models import (
    CreateRequest,
    DraftCreate,
    DraftPatch,
    DraftView,
    JobView,
    MatchingStatus,
    RevisionRequest,
    SchemaSnapshot,
)
from agents_service.providers import MatchingClient, ProviderError, fingerprint
from agents_service.store import ConflictError, NotFoundError, Store


def create_app(store: Store | None = None, matching: MatchingClient | None = None,
               token: str | None = None) -> FastAPI:
    """Build an injectable app; startup never applies migrations or loads LLMs."""
    app = FastAPI(title="Comit Agents Service", version="0.1.0")
    secret = token if token is not None else os.getenv("AGENTS_SERVICE_TOKEN", "")
    engine = matching or MatchingClient()

    def database() -> Store:
        if store is not None:
            return store
        url = os.getenv("DATABASE_URL")
        if not url:
            raise HTTPException(503, "agents_database_not_configured")
        return Store(url)

    def owner(x_internal_token: Annotated[str | None, Header()] = None,
              x_owner_id: Annotated[str | None, Header()] = None) -> UUID:
        if not secret:
            raise HTTPException(503, "agents_authentication_not_configured")
        if not x_internal_token or not hmac.compare_digest(x_internal_token.encode(), secret.encode()):
            raise HTTPException(401, "invalid_internal_token")
        try:
            return UUID(x_owner_id or "")
        except ValueError:
            raise HTTPException(400, "owner_id_header_required") from None

    Owner = Annotated[UUID, Depends(owner)]
    Database = Annotated[Store, Depends(database)]

    @app.exception_handler(ProviderError)
    async def provider_error(_request, error: ProviderError):
        return JSONResponse(status_code=503, content={"detail": error.code})

    @app.exception_handler(psycopg.Error)
    async def database_error(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "agents_database_unavailable"})

    @app.exception_handler(NotFoundError)
    async def not_found(_request, _error):
        return JSONResponse(status_code=404, content={"detail": "owner_record_not_found"})

    @app.exception_handler(ConflictError)
    async def conflict(_request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        """Report liveness without claiming model or database readiness."""
        return {"service": "agents-service", "status": "ok"}

    @app.get("/ready")
    def ready(db: Database) -> dict[str, str]:
        """Check the actual migration tables; inference remains optional."""
        from agents_service.database import connect
        try:
            with connect(db.database_url) as connection:
                connection.execute("SELECT singleton FROM agents_service.settings").fetchone()
        except psycopg.Error:
            raise HTTPException(503, "agents_database_unavailable") from None
        return {"service": "agents-service", "status": "ready"}

    @app.get("/internal/agents/taxonomy", response_model=SchemaSnapshot)
    def taxonomy(_owner: Owner):
        """Expose all three matching-owned groups directly to clients and agent tools."""
        return engine.schema()

    @app.post("/internal/vacancy-drafts", response_model=DraftView)
    def create(request: DraftCreate, owner_id: Owner, db: Database):
        """Create an editable owner-scoped draft; no matching intake occurs."""
        return db.create(owner_id, request.fields, engine.schema())

    @app.get("/internal/vacancy-drafts", response_model=list[DraftView])
    def drafts(owner_id: Owner, db: Database, limit: int = Query(50, ge=1, le=100),
               offset: int = Query(0, ge=0, le=1000000)):
        """Page only drafts belonging to the asserted accounting owner."""
        return db.list(owner_id, limit=limit, offset=offset)

    @app.get("/internal/vacancy-drafts/{draft_id}", response_model=DraftView)
    def draft(draft_id: UUID, owner_id: Owner, db: Database):
        """Read shared form/chat state and current summary/publication status."""
        return db.get(owner_id, draft_id)

    @app.patch("/internal/vacancy-drafts/{draft_id}", response_model=DraftView)
    def patch(draft_id: UUID, request: DraftPatch, owner_id: Owner, db: Database):
        """Apply a partial form edit using CAS; editing invalidates the old summary."""
        db.get(owner_id, draft_id)
        return db.patch(owner_id, draft_id, request, engine.schema())

    @app.post("/internal/vacancy-drafts/{draft_id}/approve", response_model=DraftView)
    def approve(draft_id: UUID, request: RevisionRequest, owner_id: Owner, db: Database):
        """Validate fields and move to summary directly, without publishing."""
        db.get(owner_id, draft_id)
        return db.approve(owner_id, draft_id, request, engine.schema())

    @app.post("/internal/vacancy-drafts/{draft_id}/create", response_model=DraftView)
    def submit(draft_id: UUID, request: CreateRequest, owner_id: Owner, db: Database):
        """Human Create confirms exactly the summary hash and enqueues durable intake."""
        db.get(owner_id, draft_id)
        return db.submit(owner_id, draft_id, request, engine.schema())

    @app.get("/internal/vacancy-drafts/{draft_id}/pipeline", response_model=MatchingStatus)
    def pipeline(draft_id: UUID, owner_id: Owner, db: Database):
        """Read actual matching state separately from intake acceptance."""
        value = db.get(owner_id, draft_id)
        if value.state != "created":
            raise HTTPException(409, "matching_intake_not_accepted")
        return engine.status(str(value.vacancy_id))

    @app.post("/internal/vacancy-drafts/{draft_id}/enrich", response_model=JobView)
    def enrich(draft_id: UUID, request: RevisionRequest, owner_id: Owner, db: Database):
        """Queue form-only Laya inference on one immutable revision; manual edits win."""
        value = db.get(owner_id, draft_id)
        if value.revision != request.expected_revision or value.state in ("creating", "created", "failed"):
            raise HTTPException(409, "draft_revision_or_state_conflict")
        schema = engine.schema()
        payload = {"expected_revision": value.revision, "fields": value.fields.model_dump(by_alias=True),
                   "schema": schema.model_dump()}
        return db.enqueue_job(owner_id, "enrichment", payload, draft_id=draft_id,
                              idempotency_key=f"enrich:{draft_id}:{fingerprint(payload)}")

    @app.get("/internal/agents/jobs/{job_id}", response_model=JobView)
    def job(job_id: UUID, owner_id: Owner, db: Database):
        """Read visible durable progress and safe error codes."""
        return db.get_job(owner_id, job_id)

    @app.post("/internal/agents/jobs/{job_id}/retry", response_model=JobView)
    def retry(job_id: UUID, owner_id: Owner, db: Database):
        """Explicitly retry a failed job without duplicating its effects."""
        value = db.get_job(owner_id, job_id)
        if value.kind == "agent_turn":
            from agents_service.session_routes import retry_agent_job
            return retry_agent_job(db, owner_id, job_id)
        return db.retry_job(owner_id, job_id)

    # Separate session module shares this app's owner dependency and database store.
    from agents_service.session_routes import register_session_routes
    register_session_routes(app, owner, database, engine)
    return app
