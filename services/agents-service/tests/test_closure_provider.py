"""Closure ACKs remain revision-checked without requiring open-vacancy readiness."""
from uuid import uuid4

import httpx
import pytest
from agents_service.providers import MatchingClient, ProviderError


def test_matching_accepts_closed_ack_and_rejects_wrong_status_or_source_revision() -> None:
    """Closed snapshots intentionally fail open readiness but still acknowledge their intake."""
    vacancy = uuid4()
    ack = {"vacancy_id": str(vacancy), "revision": 2, "source_revision": 3, "status": "closed",
        "state": "draft", "missing_requirements": ["status_open"], "strict_schema_version": None,
        "attempt_count": 0, "error_code": None}
    provider = MatchingClient(token="synthetic", client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=ack))))
    assert provider.publish(str(vacancy), {"source_revision": 3, "status": "closed"})["status"] == "closed"
    with pytest.raises(ProviderError, match="invalid_matching_ack"):
        provider.publish(str(vacancy), {"source_revision": 2, "status": "closed"})
    with pytest.raises(ProviderError, match="invalid_matching_ack"):
        provider.publish(str(vacancy), {"source_revision": 3, "status": "open"})
