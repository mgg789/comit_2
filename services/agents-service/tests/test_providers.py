"""Provider protocol tests, including explicit absence/zero and safe failures."""

import httpx
import pytest
from agents_service.providers import (
    DeepSeekClient,
    LayaClient,
    MatchingClient,
    ProviderError,
)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_laya_batches_and_keeps_zero():
    calls = []

    def handle(request):
        import json
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["host"] == "localhost:12444"
        answers = {}
        for key in body["questions"]:
            selected = 1 if key == "0" else 0
            answers[key] = {"probabilities": {str(i): int(i == selected) for i in range(9)}, "confidence": 1}
        return httpx.Response(200, json={"answers": answers})

    schema = {"schema_hash": "a", "coordinates": [{"index": i, "group": "stack", "key": f"skill{i}"} for i in range(37)]}
    result = LayaClient(client=client(handle)).derive({"bio": "SQL"}, schema)
    assert [len(c["questions"]) for c in calls] == [24, 13]
    assert result["fields"]["stacks"] == {"skill0": 0}
    assert result["fields"]["domens"] == {}
    assert result["provenance"]["evidence"]["stack:skill1"]["selected"] == 0


def test_laya_rejects_invalid_probabilities():
    provider = LayaClient(client=client(lambda _: httpx.Response(200, json={"answers": {"0": {"probabilities": {}}}})))
    with pytest.raises(ProviderError, match="invalid_laya_probabilities"):
        provider.derive({}, {"schema_hash": "a", "coordinates": [{"index": 0, "group": "stack", "key": "python"}]})


def test_provider_error_does_not_leak_body():
    provider = MatchingClient(token="synthetic", client=client(lambda _: httpx.Response(503, text="secret body")))
    with pytest.raises(ProviderError) as error:
        provider.schema()
    assert str(error.value) == "upstream_http_503"
    assert error.value.retryable


def test_deepseek_tools_real_wire_shape():
    def handle(request):
        import json
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["tools"][0]["type"] == "function"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                                           "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}})
    provider = DeepSeekClient(token="synthetic", client=client(handle))
    assert provider.complete([], [{"type": "function"}])["choices"][0]["message"]["content"] == "ok"


def test_no_unapproved_provider_endpoint():
    with pytest.raises(ValueError):
        DeepSeekClient(base_url="https://elsewhere.invalid")
    with pytest.raises(ValueError):
        LayaClient(base_url="http://elsewhere.invalid")


def test_publication_does_not_accept_empty_ack():
    provider = MatchingClient(token="synthetic", client=client(lambda _: httpx.Response(200, json={})))
    with pytest.raises(ProviderError, match="invalid_matching_ack"):
        provider.publish("00000000-0000-4000-8000-000000000001", {"source_revision": 1})


def test_laya_unconfigured_does_not_block_other_worker_jobs(monkeypatch):
    """An optional empty endpoint disables inference without preventing worker startup."""
    monkeypatch.setenv("JEV_LOCAL_URL", "")
    calls = []
    provider = LayaClient(client=client(lambda request: calls.append(request)))
    with pytest.raises(ProviderError, match="laya_not_configured") as error:
        provider.derive({}, {"schema_hash": "a", "coordinates": []})
    assert error.value.retryable is False
    assert calls == []
