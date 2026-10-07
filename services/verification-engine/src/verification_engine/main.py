"""FastAPI entrypoint for provider-only verification decisions."""

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException

from verification_engine.connectors import ConnectorUnavailable
from verification_engine.models import (
    FspVerificationRequest,
    HhVerificationRequest,
    RepositoryVerificationRequest,
    VerificationResult,
)
from verification_engine.service import VerificationService


def create_app(service: VerificationService | None = None, internal_token: str | None = None) -> FastAPI:
    """Build an app with an injectable service for deterministic contract tests."""

    verifier = service or VerificationService(
        integration_url=os.environ.get("INTEGRATION_ENGINE_URL"),
        hh_token=os.environ.get("HH_API_TOKEN"),
        hh_user_agent=os.environ.get("HH_USER_AGENT"),
    )
    configured_token = internal_token if internal_token is not None else os.environ.get("VERIFICATION_ENGINE_TOKEN")
    app = FastAPI(title="Comit Verification Engine", version="0.1.0")

    async def require_internal(x_internal_token: str | None = Header(default=None, alias="X-Internal-Token")) -> None:
        """Fail closed when api-core has no configured service credential."""

        if not configured_token or not x_internal_token or not hmac.compare_digest(x_internal_token, configured_token):
            raise HTTPException(status_code=401 if configured_token else 503, detail="verification_auth_unavailable" if not configured_token else "invalid_internal_token")

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "verification-engine"}

    @app.post("/verify/fsp", response_model=VerificationResult, tags=["verification"], dependencies=[Depends(require_internal)])
    async def fsp(request: FspVerificationRequest) -> VerificationResult:
        try:
            return await verifier.fsp(request)
        except ConnectorUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/verify/repository", response_model=VerificationResult, tags=["verification"], dependencies=[Depends(require_internal)])
    async def repository(request: RepositoryVerificationRequest) -> VerificationResult:
        try:
            return await verifier.repository(request)
        except ConnectorUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/verify/hh", response_model=VerificationResult, tags=["verification"], dependencies=[Depends(require_internal)])
    async def hh(request: HhVerificationRequest) -> VerificationResult:
        try:
            return await verifier.hh(request)
        except ConnectorUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return app
