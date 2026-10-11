"""Mirror transport and accounting regression tests use synthetic provider receipts."""
import json

import httpx
import pytest
from agents_service.organization_jev import (
    JevUsageUnavailable,
    OpenRouterMirrorJevClient,
)


def configure(monkeypatch):
    """Pin deployment identity and verified tariff without live credentials."""
    monkeypatch.setenv("JEV_PROVIDER", "openrouter-mirror")
    monkeypatch.setenv("JEV_API_URL", "https://openrouter.droidje.com/api/alpha/decisions")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-1.13")
    monkeypatch.setenv("JEV_INPUT_USD_PER_MILLION", "0.042")
    monkeypatch.setenv("JEV_OUTPUT_USD_PER_MILLION", "0")


def test_mirror_protocol_and_exact_receipt(monkeypatch):
    """No upstream credential or legacy SystemOne payload is sent to the mirror."""
    configure(monkeypatch)
    def handler(request):
        assert request.headers["X-Proxy-Token"] == "synthetic-proxy"
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        assert body["state"]["text"]
        assert body["questions"]["0"]["criteria"] == {"0": "dislike", "1": "like"}
        assert body["questions"]["0"]["type"] == "choice"
        return httpx.Response(200, json={"model":"typesafe/jev-1.13-20260917",
            "answers":{"0":{"choice":"1","probabilities":{"0":0.1,"1":0.9},"confidence":0.9}},
            "usage":{"input_tokens":324,"output_tokens":31,"cost":0.000013608}})
    result = OpenRouterMirrorJevClient(token="synthetic-proxy",client=httpx.Client(transport=httpx.MockTransport(handler))).decide({},[{}],"")
    assert result["usage"]["total_micro_usd"] == "13.608000000"
    assert result["answers"]["0"]["classprobs"]["1"] == 0.9


@pytest.mark.parametrize("mutation", ["missing_usage", "wrong_cost", "wrong_model", "wrong_choice"])
def test_invalid_receipt_is_never_applied(monkeypatch, mutation):
    """Missing counters, changed rates, and response identity mismatches fail closed."""
    configure(monkeypatch)
    body={"model":"typesafe/jev-1.13","answers":{"0":{"choice":"1","probabilities":{"0":0,"1":1}}},
          "usage":{"input_tokens":100,"output_tokens":4,"cost":0.0000042}}
    if mutation == "missing_usage": body.pop("usage")
    if mutation == "wrong_cost": body["usage"]["cost"] = 1
    if mutation == "wrong_model": body["model"] = "other"
    if mutation == "wrong_choice": body["answers"]["0"]["choice"] = "outside"
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
    with pytest.raises(JevUsageUnavailable):
        OpenRouterMirrorJevClient(token="synthetic",client=client).decide({},[{}],"")


def test_mirror_url_is_pinned(monkeypatch):
    """Private credentials cannot be forwarded to a caller-selected host."""
    configure(monkeypatch)
    monkeypatch.setenv("JEV_API_URL", "https://example.org/api/alpha/decisions")
    with pytest.raises(ValueError): OpenRouterMirrorJevClient(token="synthetic")
