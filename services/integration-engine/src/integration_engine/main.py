"""App factory and routes of the integration engine."""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from integration_engine.fsp_mock import get_participant


class VerifyRequest(BaseModel):
    fsp_id: str
    claimed_rating: int


class VerifyResponse(BaseModel):
    fsp_id: str
    verified: bool
    actual_rating: int | None


def create_app() -> FastAPI:
    app = FastAPI(
        title="Comit Integration Engine",
        version="0.1.0",
        description="Verification of externally sourced profile data; hosts demo mock registries.",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "integration-engine"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "integration-engine"}

    @app.get("/fsp/participants/{fsp_id}", tags=["fsp"])
    async def fsp_participant(fsp_id: str) -> dict[str, Any]:
        participant = get_participant(fsp_id)
        # Absence of FSP history is a normal, must-handle state (TZ §2), not an error
        # for the platform — the registry-side 404 tells callers to render a profile
        # without the FSP block instead of failing the request.
        if participant is None:
            raise HTTPException(status_code=404, detail="fsp history not found")
        return participant

    @app.post("/fsp/verify", tags=["fsp"], response_model=VerifyResponse)
    async def fsp_verify(request: VerifyRequest) -> VerifyResponse:
        participant = get_participant(request.fsp_id)
        if participant is None:
            return VerifyResponse(
                fsp_id=request.fsp_id, verified=False, actual_rating=None
            )
        actual = int(participant["rating"])
        return VerifyResponse(
            fsp_id=request.fsp_id,
            verified=actual == request.claimed_rating,
            actual_rating=actual,
        )

    return app
