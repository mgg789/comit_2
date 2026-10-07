"""Verification decisions with provider cache and no account ownership claims."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from verification_engine.connectors import (
    ConnectorUnavailable,
    HhResume,
    ProviderRejected,
    RepositoryProbe,
    fetch_hh_resume,
    fetch_json,
    probe_public_repository,
)
from verification_engine.models import (
    FspVerificationRequest,
    HhVerificationRequest,
    RepositoryVerificationRequest,
    VerificationResult,
)

FspFetcher = Callable[[str], Awaitable[dict[str, Any]]]
HhFetcher = Callable[[str], Awaitable[HhResume]]


class VerificationService:
    """Verify bounded public/provider facts while api-core owns candidate binding."""

    def __init__(self, *, integration_url: str | None = None, hh_token: str | None = None,
                 hh_user_agent: str | None = None, fsp_fetcher: FspFetcher | None = None,
                 repository_fetcher: Callable[[str], RepositoryProbe] | None = None,
                 hh_fetcher: HhFetcher | None = None, cache_ttl_seconds: int = 60,
                 max_cache_entries: int = 256) -> None:
        self.integration_url = integration_url
        self.hh_token = hh_token
        self.hh_user_agent = hh_user_agent
        self.fsp_fetcher = fsp_fetcher
        self.repository_fetcher = repository_fetcher
        self.hh_fetcher = hh_fetcher
        self.cache_ttl_seconds = max(0, min(cache_ttl_seconds, 3600))
        self.max_cache_entries = max(1, min(max_cache_entries, 2048))
        self._cache: dict[str, tuple[float, VerificationResult]] = {}

    def _cached(self, key: str) -> VerificationResult | None:
        hit = self._cache.get(key)
        if hit is None:
            return None
        if hit[0] <= time.monotonic():
            self._cache.pop(key, None)
            return None
        return hit[1]

    def _put(self, key: str, value: VerificationResult) -> VerificationResult:
        if self.cache_ttl_seconds > 0:
            if len(self._cache) >= self.max_cache_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, value)
        return value

    async def fsp(self, request: FspVerificationRequest) -> VerificationResult:
        """Read the synthetic FSP registry; mock facts are explicitly partial."""

        if request.origin == "live_unbound":
            return VerificationResult(kind="fsp", status="pending", error_code="fsp_origin_unbound",
                                      provenance=["fsp:live_unbound"])
        key = "fsp:" + json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if cached := self._cached(key):
            return cached
        if self.fsp_fetcher is not None:
            fetch = self.fsp_fetcher
        elif self.integration_url:
            fetch = lambda fsp_id: fetch_json(f"{self.integration_url.rstrip('/')}/fsp/participants/{fsp_id}")
        else:
            raise ConnectorUnavailable("fsp_connector_unavailable")
        try:
            participant = await fetch(request.fsp_id)
        except ProviderRejected:
            return self._put(key, VerificationResult(kind="fsp", status="rejected", error_code="fsp_not_found",
                                      provenance=["fsp:synthetic_registry"]))
        except ConnectorUnavailable:
            raise
        except (TypeError, ValueError, KeyError) as error:
            raise ConnectorUnavailable("fsp_invalid_response") from error
        if (participant.get("fsp_id") not in {None, request.fsp_id}
                or not isinstance(participant.get("rating"), int)
                or not isinstance(participant.get("rank"), str)
                or not isinstance(participant.get("events", []), list)
                or not isinstance(participant.get("certificates", []), list)):
            raise ConnectorUnavailable("fsp_invalid_response")
        rating = participant.get("rating")
        if not isinstance(rating, int) or (request.claimed_rating is not None and rating != request.claimed_rating):
            return self._put(key, VerificationResult(kind="fsp", status="rejected", error_code="fsp_rating_mismatch",
                                      provenance=["fsp:synthetic_registry"]))
        achievement = None
        if request.claim_id is not None:
            raw_achievements = participant.get("achievements")
            if not isinstance(raw_achievements, list):
                return self._put(key, VerificationResult(kind="fsp", status="rejected", error_code="fsp_claim_not_found",
                                          provenance=["fsp:synthetic_registry"]))
            achievement = next((item for item in raw_achievements if isinstance(item, dict) and item.get("claim_id") == request.claim_id), None)
            if achievement is None:
                return self._put(key, VerificationResult(kind="fsp", status="rejected", error_code="fsp_claim_not_found",
                                          provenance=["fsp:synthetic_registry"]))
        return self._put(key, VerificationResult(kind="fsp", status="partial", verified_value={
            "fsp_id": request.fsp_id, "rank": participant.get("rank"), "rating": rating,
            "events": participant.get("events", []), "certificates": participant.get("certificates", []),
            "claim_id": request.claim_id, "achievement": achievement, "provider": "fsp_hackathon_registry",
            "synthetic": True, "demo_registry": True, "ownership_proven": False,
        }, provenance=["fsp:synthetic_registry"]))

    async def repository(self, request: RepositoryVerificationRequest) -> VerificationResult:
        """Confirm only a real public Git tree and non-empty file content."""

        probe = await probe_public_repository(str(request.url), fetcher=self.repository_fetcher)
        host = (request.url.host or "").lower()
        provenance = [f"repo:{host}:public_git_tree"]
        if probe.status_code == 404:
            return VerificationResult(kind="repository", status="rejected", error_code="repository_not_found", provenance=provenance)
        if probe.status_code < 200 or probe.status_code >= 300:
            return VerificationResult(kind="repository", status="pending", error_code="repository_provider_unavailable", provenance=provenance)
        if not probe.nonempty or probe.tree_entries < 1 or probe.content_bytes < 1:
            return VerificationResult(kind="repository", status="rejected", error_code="repository_empty", provenance=provenance)
        return VerificationResult(kind="repository", status="partial", verified_value={
            "url": probe.canonical_url or str(request.url), "repository_nonempty": True,
            "tree_entries": probe.tree_entries, "content_bytes": probe.content_bytes,
            "contribution_narrative": request.contribution_narrative,
            "authorship_proven": False,
        }, provenance=provenance)

    async def hh(self, request: HhVerificationRequest) -> VerificationResult:
        """Read a real HH resume; provider data is partial and never ownership proof."""

        if not self.hh_token:
            raise ConnectorUnavailable("hh_token_unavailable")
        if not self.hh_user_agent:
            raise ConnectorUnavailable("hh_user_agent_unavailable")
        key = f"hh:{request.resume_id}"
        if cached := self._cached(key):
            return cached
        fetch = self.hh_fetcher or (lambda resume_id: fetch_hh_resume(resume_id, token=self.hh_token or "", user_agent=self.hh_user_agent or ""))
        try:
            resume = await fetch(request.resume_id)
        except ProviderRejected as error:
            return self._put(key, VerificationResult(kind="hh", status="rejected", error_code=str(error), provenance=["hh:api.hh.ru"]))
        except ConnectorUnavailable:
            raise
        return self._put(key, VerificationResult(kind="hh", status="partial", verified_value={
            "resume_id": resume.resume_id, "title": resume.title, "updated_at": resume.updated_at,
            "area": resume.area, "experience": resume.experience or [], "ownership_proven": False,
        }, provenance=["hh:api.hh.ru"]))
