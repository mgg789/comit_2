"""Strict wire contracts for external verification; identity is deliberately absent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

VerificationOrigin = Literal["mock", "live_unbound"]
ClaimKind = Literal["projects", "experience", "achievements"]


class StrictModel(BaseModel):
    """Reject provider-boundary fields that were not explicitly reviewed."""

    model_config = ConfigDict(extra="forbid")


class FspVerificationRequest(StrictModel):
    """FSP participant id and candidate supplied value; no account identity."""

    fsp_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    claimed_rating: int | None = Field(default=None, ge=0, le=10_000_000)
    origin: VerificationOrigin
    claim_id: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")


class RepositoryVerificationRequest(StrictModel):
    """Public repository URL and narrative; claim binding stays in api-core."""

    url: HttpUrl
    contribution_narrative: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def public_repository_url(self) -> "RepositoryVerificationRequest":
        """Reject credentials, fragments, query strings and non-public hosts."""

        from verification_engine.connectors import (
            ProviderRejected,
            canonical_repository_url,
        )

        try:
            canonical_repository_url(str(self.url))
        except ProviderRejected as error:
            raise ValueError(str(error)) from error
        return self


class HhVerificationRequest(StrictModel):
    """Read-only HH resume lookup; the token is provider configuration only."""

    resume_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class VerificationResult(StrictModel):
    """Provider result safe to bind to an authenticated profile claim."""

    kind: Literal["fsp", "repository", "hh"]
    status: Literal["confirmed", "partial", "pending", "rejected"]
    verified_value: dict[str, object] | None = None
    provenance: list[str] = Field(default_factory=list, max_length=10)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
