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
