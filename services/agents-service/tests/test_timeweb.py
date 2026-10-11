"""Offline Timeweb wire, billing and availability regression coverage."""

import json

import httpx
import pytest
from agents_service import availability
from agents_service.organization_billing import BillingError, Pricing
from agents_service.providers import DeepSeekClient, llm_config


@pytest.mark.parametrize(("effort", "expected"), [("off", "low"), ("medium", "medium"), ("max", "high"), (None, "medium")])
def test_timeweb_wire_mapping(monkeypatch, effort, expected):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv("TIMEWEB_REASONING_EFFORT", "medium")
    def handle(request):
        assert str(request.url) == "https://api.timeweb.ai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "timeweb/gpt-oss-120b"
        assert body["reasoning_effort"] == expected
        assert "thinking" not in body
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    provider = DeepSeekClient(token="synthetic", client=httpx.Client(transport=httpx.MockTransport(handle)))
    provider.complete([], [], reasoning_effort=effort)
    assert provider.provider == "timeweb"


@pytest.mark.parametrize(("key", "value"), [("LLM_PROVIDER", "other"), ("TIMEWEB_REASONING_EFFORT", "max"), ("TIMEWEB_BASE_URL", "https://other.example/v1")])
def test_invalid_configuration_fails_closed(monkeypatch, key, value):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        llm_config()


def test_timeweb_pricing_requires_explicit_rates(monkeypatch):
    monkeypatch.delenv("AGENT_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENT_OUTPUT_USD_PER_MILLION", raising=False)
    with pytest.raises(BillingError, match="usage_pricing_not_configured"):
        Pricing.from_env(provider="timeweb", model="timeweb/gpt-oss-120b")
    monkeypatch.setenv("AGENT_INPUT_USD_PER_MILLION", "0.1")
    monkeypatch.setenv("AGENT_OUTPUT_USD_PER_MILLION", "0.4")
    pricing = Pricing.from_env(provider="timeweb", model="timeweb/gpt-oss-120b")
    assert pricing.provider == "timeweb"
    assert pricing.rate_hash != Pricing.from_env().rate_hash


def test_timeweb_capability_probes_configured_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "timeweb")
    monkeypatch.setenv("TIMEWEB_API_KEY", "synthetic")
    availability._cache.clear()
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200)
    monkeypatch.setattr(availability.httpx, "get", get)
    assert availability._reachable("agent")
    assert calls == ["https://api.timeweb.ai/v1/models"]


@pytest.mark.parametrize("confidence", [1, 0.4, None])
def test_jev_mirror_enrichment_protocol(monkeypatch, confidence):
    from agents_service.providers import LayaClient, ProviderError
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    def handle(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://openrouter.droidje.com/api/alpha/decisions"
        assert request.headers["X-Proxy-Token"] == "synthetic"
        assert "authorization" not in request.headers
        assert body["model"] == "typesafe/jev-1.13"
        assert isinstance(body["state"]["text"], str)
        assert body["questions"]["0"]["type"] == "choice"
        assert set(body["questions"]["0"]["criteria"]) == {str(i) for i in range(9)}
        return httpx.Response(200, json={"model": "typesafe/jev-1.13-20260917", "answers": {
            "0": {"type": "choice", "choice": "1", "probabilities": {str(i): int(i == 1) for i in range(9)}, "confidence": confidence}}})
    provider = LayaClient(client=httpx.Client(transport=httpx.MockTransport(handle)))
    schema = {"schema_hash": "schema", "coordinates": [{"index": 0, "group": "stack", "key": "python"}]}
    if confidence != 1:
        with pytest.raises(ProviderError):
            provider.derive({"bio": "Python"}, schema)
    else:
        result = provider.derive({"bio": "Python"}, schema)
        assert result["fields"]["stacks"] == {"python": 0}
        assert result["provenance"]["model"] == "typesafe/jev-1.13-20260917"


def test_jev_mirror_enrichment_capability(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    availability._cache.clear()
    def get(url, **kwargs):
        assert url == "https://openrouter.droidje.com/api/v1/models"
        assert kwargs["headers"] == {"X-Proxy-Token": "synthetic"}
        return httpx.Response(200)
    monkeypatch.setattr(availability.httpx, "get", get)
    assert availability._reachable("enrichment")


@pytest.mark.parametrize("model", ["typesafe/jev-1.13", "typesafe/jev-1.13-20260917", "wrong/model", "typesafe/jev-1.13-other", "typesafe/jev-1.13-202609170", None])
def test_mirror_enrichment_rejects_wrong_model_before_applying(monkeypatch, model):
    from agents_service.providers import LayaClient, ProviderError
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    response = {"model": model, "answers": {"0": {"type": "choice", "choice": "1", "confidence": 1,
        "probabilities": {str(i): int(i == 1) for i in range(9)}}}}
    provider = LayaClient(client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))))
    fields = {"bio": "Python"}
    schema = {"schema_hash": "schema", "coordinates": [{"index": 0, "group": "stack", "key": "python"}]}
    if model in {"typesafe/jev-1.13", "typesafe/jev-1.13-20260917"}:
        assert provider.derive(fields, schema)["provenance"]["model"] == model
    else:
        with pytest.raises(ProviderError, match="jev_model_mismatch"):
            provider.derive(fields, schema)
    assert fields == {"bio": "Python"}


@pytest.mark.parametrize("cached_rate", [None, ""])
def test_timeweb_cached_pricing_defaults_to_input_for_compose(monkeypatch, cached_rate):
    from decimal import Decimal
    monkeypatch.setenv("AGENT_INPUT_USD_PER_MILLION", "0.2")
    monkeypatch.setenv("AGENT_OUTPUT_USD_PER_MILLION", "0.83")
    monkeypatch.setenv("AGENT_PRICING_VERSION", "user-timeweb-2026-10-11")
    if cached_rate is None:
        monkeypatch.delenv("AGENT_CACHED_INPUT_USD_PER_MILLION", raising=False)
    else:
        monkeypatch.setenv("AGENT_CACHED_INPUT_USD_PER_MILLION", cached_rate)
    pricing = Pricing.from_env(provider="timeweb", model="timeweb/gpt-oss-120b")
    assert pricing.input_usd_per_million == Decimal("0.2")
    assert pricing.output_usd_per_million == Decimal("0.83")
    assert pricing.cached_input_usd_per_million == Decimal("0.2")
    assert pricing.provider == "timeweb"
    assert pricing.model == "timeweb/gpt-oss-120b"
    assert pricing.version == "user-timeweb-2026-10-11"
