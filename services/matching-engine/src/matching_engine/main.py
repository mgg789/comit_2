"""Protected candidate-pipeline API; schema migration and inference run explicitly."""

import os
import secrets
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from matching_engine.models import (
    CandidateProfile,
    CandidateStatus,
    CoordinateAddition,
    PoolPage,
    StrictSchema,
)
from matching_engine.store import ConflictError, NotFoundError, Store
from matching_engine.vacancy_routes import vacancy_router
from matching_engine.vacancy_store import VacancyStore


def create_app(*, store: Store | None = None, internal_token: str | None = None,
               encoder_ready: Callable[[], bool] | None = None,
               vacancy_store: VacancyStore | None = None) -> FastAPI:
    """Build routes without DB/model side effects; injected dependencies support tests only."""
    database_url = os.environ.get("DATABASE_URL")
    pipeline_store = store if store is not None else (Store(database_url) if database_url else None)
    vacancy_repository = vacancy_store if vacancy_store is not None else (
        VacancyStore(pipeline_store.database_url) if pipeline_store is not None else None
    )
    token = internal_token if internal_token is not None else os.environ.get("CANDIDATE_PIPELINE_TOKEN")
    app = FastAPI(
        title="Comit Matching Engine", version="0.2.0",
        description="Revision-safe candidate intake, dual vectors and contact-free ready pool.",
    )

    def require_internal(x_internal_token: str | None = Header(default=None)) -> Store:
        """Fail closed without a configured token; only the trusted API may use these routes."""
        if not token:
            raise HTTPException(status_code=503, detail="pipeline authentication is not configured")
        if x_internal_token is None or not secrets.compare_digest(x_internal_token.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid internal token")
        if pipeline_store is None:
            raise HTTPException(status_code=503, detail="pipeline database is not configured")
        return pipeline_store

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request: Request, exception: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exception)})

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exception: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exception)})

    @app.exception_handler(psycopg.Error)
    async def database_handler(_request: Request, _exception: psycopg.Error) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "pipeline storage unavailable"})

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Report process liveness, independent of DB, token and model provisioning."""
        return {"status": "ok", "service": "matching-engine"}

    @app.get("/ready", tags=["health"])
    def ready(response: Response) -> dict[str, object]:
        """Check actual dependencies; unavailable model or migrations means HTTP 503."""
        if encoder_ready is not None:
            embedding_ready = encoder_ready()
        else:
            from matching_engine.embeddings import HarrierEncoder
            embedding_ready = HarrierEncoder().is_available
        checks = {
            "authentication": bool(token),
            "database": pipeline_store is not None and pipeline_store.ready(),
            "embedding": embedding_ready,
            "vacancy_database": vacancy_repository is not None and vacancy_repository.ready(),
        }
        available = all(checks.values())
        if not available:
            response.status_code = 503
        return {"status": "ok" if available else "unavailable",
                "service": "matching-engine", "checks": checks}

    @app.put("/internal/candidates/{candidate_id}/profile", response_model=CandidateStatus,
             tags=["candidate-pipeline"])
    def intake(candidate_id: UUID, profile: CandidateProfile,
               repository: Annotated[Store, Depends(require_internal)]) -> CandidateStatus:
        """Persist a monotonic upstream snapshot and queue artifacts when its checklist is complete."""
        try:
            return repository.intake(candidate_id, profile)
        except ValueError as exception:
            raise HTTPException(status_code=422, detail=str(exception)) from exception

    @app.get("/internal/candidates/{candidate_id}", response_model=CandidateStatus,
             tags=["candidate-pipeline"])
    def candidate_status(candidate_id: UUID,
                         repository: Annotated[Store, Depends(require_internal)]) -> CandidateStatus:
        """Read progress or missing required inputs without returning contacts."""
        return repository.status(candidate_id)

    @app.post("/internal/candidates/{candidate_id}/retry", response_model=CandidateStatus,
              tags=["candidate-pipeline"])
    def retry(candidate_id: UUID,
              repository: Annotated[Store, Depends(require_internal)]) -> CandidateStatus:
        """Restart bounded failed inference after fixing dependency availability."""
        return repository.retry(candidate_id)

    @app.get("/internal/strict-schema", response_model=StrictSchema, tags=["candidate-pipeline"])
    def strict_schema(repository: Annotated[Store, Depends(require_internal)]) -> StrictSchema:
        """Return coordinate indices and compatibility identity for combine consumers."""
        return repository.schema()

    @app.post("/internal/strict-schema/coordinates", response_model=StrictSchema,
              tags=["candidate-pipeline"])
    def extend_schema(addition: CoordinateAddition,
                      repository: Annotated[Store, Depends(require_internal)]) -> StrictSchema:
        """Append an authorized coordinate and durably migrate every previous candidate."""
        return repository.extend_schema(addition)

    @app.get("/internal/pool", response_model=PoolPage, tags=["candidate-pipeline"])
    def pool(repository: Annotated[Store, Depends(require_internal)],
             limit: int = Query(default=100, ge=1, le=500),
             offset: int = Query(default=0, ge=0)) -> PoolPage:
        """Return contact-free ready snapshots, never drafts or mixed strict generations."""
        return repository.pool(limit, offset)

    @app.get("/internal/pool/{candidate_id}/resume.pdf", tags=["candidate-pipeline"],
             responses={200: {"content": {"application/pdf": {}}}})
    def public_resume(candidate_id: UUID,
                      repository: Annotated[Store, Depends(require_internal)]) -> Response:
        """Return the redacted Tinder PDF for a currently visible pool member."""
        return Response(content=repository.resume(candidate_id), media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/internal/candidates/{candidate_id}/resume.pdf", tags=["candidate-pipeline"],
             responses={200: {"content": {"application/pdf": {}}}})
    def owner_resume(candidate_id: UUID,
                     repository: Annotated[Store, Depends(require_internal)]) -> Response:
        """Return the private PDF to trusted API, which must enforce candidate ownership."""
        return Response(content=repository.resume(candidate_id, public=False),
                        media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    app.include_router(vacancy_router(vacancy_repository, require_internal))
    return app
