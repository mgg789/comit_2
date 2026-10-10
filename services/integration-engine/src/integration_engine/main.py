"""App factory and routes of the integration engine."""

import hmac
import os
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from integration_engine.fsp_mock import MOCK_PARTICIPANTS, get_participant

DEMO_CODES: dict[str, tuple[str, str, datetime]] = {}


class VerifyRequest(BaseModel):
    fsp_id: str
    claimed_rating: int


class VerifyResponse(BaseModel):
    fsp_id: str
    verified: bool
    actual_rating: int | None


class FspAchievement(BaseModel):
    claim_id: str
    title: str
    year: int
    place: int


class FspParticipant(BaseModel):
    fsp_id: str
    display_name: str
    rank: str
    rating: int
    events: list[dict[str, Any]]
    certificates: list[str]
    provider: str
    provider_version: str
    demo: bool
    account_ref: str
    achievements: list[FspAchievement]


class DemoAuthorizeRequest(BaseModel):
    state: str
    redirect_uri: str
    fixture_id: str


class DemoTokenRequest(BaseModel):
    code: str
    state: str


def create_app(demo_internal_token: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Comit Integration Engine",
        version="0.1.0",
        description="Verification of externally sourced profile data; hosts demo mock registries.",
    )
    def require_development_registry() -> None:
        """Synthetic registry data is available only in an explicitly selected dev environment."""
        if os.environ.get("APP_ENV", "production") != "development" or os.environ.get("FSP_DEMO_ENABLED") != "true":
            raise HTTPException(status_code=503, detail="fsp_registry_not_configured")

    configured_demo_token = demo_internal_token if demo_internal_token is not None else os.environ.get("FSP_DEMO_INTERNAL_TOKEN")
    callback_allowlist = {os.environ.get("FSP_DEMO_CALLBACK_URL", "http://localhost:8080/candidate/profile/fsp/demo/callback")}

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "integration-engine"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "integration-engine"}

    @app.get("/fsp/participants/{fsp_id}", tags=["fsp"], dependencies=[Depends(require_development_registry)])
    async def fsp_participant(fsp_id: str) -> FspParticipant:
        participant = get_participant(fsp_id)
        # Absence of FSP history is a normal, must-handle state (TZ §2), not an error
        # for the platform — the registry-side 404 tells callers to render a profile
        # without the FSP block instead of failing the request.
        if participant is None:
            raise HTTPException(status_code=404, detail="fsp history not found")
        return FspParticipant.model_validate(participant)

    @app.get("/fsp/participants", tags=["fsp"], dependencies=[Depends(require_development_registry)])
    async def fsp_participants() -> list[FspParticipant]:
        """List read-only synthetic registry entries for the demo picker."""
        return [FspParticipant.model_validate(item) for item in MOCK_PARTICIPANTS.values()]

    @app.post("/fsp/demo/authorize", tags=["fsp-demo"], dependencies=[Depends(require_development_registry)])
    async def fsp_demo_authorize(request: DemoAuthorizeRequest) -> dict[str, str]:
        """Issue a short lived one-time demo code; no FSP credentials are collected."""
        if len(request.state) > 256 or request.redirect_uri not in callback_allowlist:
            raise HTTPException(status_code=400, detail="invalid_demo_authorization_request")
        if get_participant(request.fixture_id) is None:
            raise HTTPException(status_code=404, detail="demo_fixture_not_found")
        code = token_urlsafe(32)
        DEMO_CODES[code] = (request.state, request.fixture_id, datetime.now(timezone.utc) + timedelta(seconds=120))
        return {"code": code, "state": request.state, "provider": "fsp_hackathon_registry", "demo": "true"}

    @app.get("/fsp/demo/authorize", tags=["fsp-demo"], dependencies=[Depends(require_development_registry)])
    async def fsp_demo_authorize_get(state: str, redirect_uri: str, fixture_id: str) -> dict[str, str]:
        """Curl/browser friendly equivalent of the demo authorize form."""
        return await fsp_demo_authorize(DemoAuthorizeRequest(state=state, redirect_uri=redirect_uri, fixture_id=fixture_id))

    @app.post("/fsp/demo/token", tags=["fsp-demo"], dependencies=[Depends(require_development_registry)])
    async def fsp_demo_token(request: DemoTokenRequest, x_internal_token: str | None = Header(default=None, alias="X-Internal-Token")) -> FspParticipant:
        """Exchange one demo code exactly once and return only fixture registry data."""
        if not configured_demo_token or not x_internal_token or not hmac.compare_digest(x_internal_token, configured_demo_token):
            raise HTTPException(status_code=503 if not configured_demo_token else 401, detail="demo_exchange_auth_unavailable" if not configured_demo_token else "invalid_internal_token")
        record = DEMO_CODES.pop(request.code, None)
        if record is None or record[0] != request.state or record[2] <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="demo_code_invalid_or_replayed")
        participant = get_participant(record[1])
        if participant is None:
            raise HTTPException(status_code=404, detail="demo_fixture_not_found")
        return FspParticipant.model_validate(participant)

    @app.get("/fsp/participants/{fsp_id}/achievements/{claim_id}", tags=["fsp"], dependencies=[Depends(require_development_registry)])
    async def fsp_achievement(fsp_id: str, claim_id: str) -> FspAchievement:
        participant = get_participant(fsp_id)
        achievement = next((item for item in (participant or {}).get("achievements", []) if item.get("claim_id") == claim_id), None)
        if achievement is None:
            raise HTTPException(status_code=404, detail="fsp achievement not found")
        return FspAchievement.model_validate(achievement)

    @app.post("/fsp/verify", tags=["fsp"], response_model=VerifyResponse, dependencies=[Depends(require_development_registry)])
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
