"""Provider failure disables optional work without inventing a generated result."""
import pytest
from agents_service import availability
from agents_service.providers import ProviderError


def test_manual_mode_never_probes_network(monkeypatch):
    monkeypatch.setenv("AGENTS_MODE", "manual")
    monkeypatch.setattr(availability, "_reachable", lambda _: pytest.fail("network called"))
    assert availability.capability_view()["manual_editing"]
    assert not availability.capability_view()["agent"]
    with pytest.raises(ProviderError) as error:
        availability.require_capability("enrichment")
    assert error.value.code == "agent_disabled_manual_available" and not error.value.retryable


def test_missing_token_and_network_failure_keep_manual_available(monkeypatch):
    monkeypatch.delenv("AGENTS_MODE", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(availability, "_reachable", lambda _: False)
    view = availability.capability_view()
    assert view["manual_editing"] and not view["agent"] and not view["enrichment"]


def test_missing_token_skips_remote_probe(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(availability.httpx, "get", lambda *a, **k: pytest.fail("network called"))
    assert not availability._reachable("agent")


def test_transient_probe_failure_is_retryable_not_missing_configuration(monkeypatch):
    monkeypatch.setenv("AGENTS_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-token")
    monkeypatch.setattr(availability, "_reachable", lambda _: False)
    with pytest.raises(ProviderError) as error:
        availability.require_capability("agent")
    assert error.value.code == "agent_temporarily_unavailable"
    assert error.value.retryable


def test_negative_probe_cache_expires_before_positive_cache(monkeypatch):
    availability._cache.clear()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-token")
    clock = [100.0]
    calls = []
    monkeypatch.setattr(availability.time, "monotonic", lambda: clock[0])

    def probe(*args, **kwargs):
        calls.append(1)
        return availability.httpx.Response(503 if len(calls) == 1 else 200)

    monkeypatch.setattr(availability.httpx, "get", probe)
    try:
        assert not availability._reachable("agent")
        clock[0] += 4
        assert availability._reachable("agent")
        clock[0] += 4
        assert availability._reachable("agent")
        assert len(calls) == 2
    finally:
        availability._cache.clear()


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_credentials_are_not_reported_as_retryable_outage(monkeypatch, status):
    availability._cache.clear()
    monkeypatch.setenv("AGENTS_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-token")
    monkeypatch.setattr(availability.httpx, "get", lambda *a, **k: availability.httpx.Response(status))
    try:
        with pytest.raises(ProviderError) as error:
            availability.require_capability("agent")
        assert error.value.code == "agent_provider_rejected"
        assert not error.value.retryable
    finally:
        availability._cache.clear()
