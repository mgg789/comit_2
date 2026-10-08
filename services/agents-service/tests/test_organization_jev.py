"""Unit tests for fail-closed Jev swipe orchestration."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from agents_service.organization_jev import JevUsageUnavailable, OrganizationJev
from agents_service.organization_tools import OrganizationToolError, ToolRegistry

OWNER = "00000000-0000-4000-8000-000000000001"
VACANCY = "00000000-0000-4000-8000-000000000002"
EXPOSURE = "00000000-0000-4000-8000-000000000003"


class FakeRegistry:
    """In-memory owner bridge recording read and guarded write calls."""

    database_url = "postgresql://unused"

    def __init__(self):
        self.calls = []

    def dispatch(self, owner, name, arguments, idempotency_key, task_id=None):
        self.calls.append((owner, name, arguments, idempotency_key, task_id))
        if name == "vacancy_get":
            return {"vacancy_id": VACANCY, "fields": {"title": "Backend"}}
        if name == "candidate_feed":
            return {"cards": [{"exposure_id": EXPOSURE, "candidate_id": "00000000-0000-4000-8000-000000000004", "grade": "middle"}]}
        return {"accepted": True}


class FakeUsage:
    """Synthetic cost backend; tests never call a live budget or provider."""

    def __init__(self):
        self.reserved = []
        self.settled = []

    def reserve(self, owner_id, request_hash, upper_bound_micro_usd):
        self.reserved.append((owner_id, request_hash, upper_bound_micro_usd))
        return "reservation-1"

    def settle(self, owner_id, reservation_id, usage):
        self.settled.append((owner_id, reservation_id, usage))


class FakeJev:
    """Deterministic decision provider with explicit usage accounting."""

    def decide(self, vacancy, cards, comment):
        assert vacancy["fields"]["title"] == "Backend"
        assert cards[0]["grade"] == "middle"
        assert comment == "focus on API work"
        return {"answers": {"0": {"classprobs": {"0": 0.1, "1": 0.9}}},
                "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                           "input_micro_usd": "2", "output_micro_usd": "1",
                           "rate_config": {"source": "test"}}}


def _service(registry=None, client=None, usage=None):
    service = OrganizationJev(registry or FakeRegistry(), client=client or FakeJev(), usage_backend=usage or FakeUsage(),
                              upper_bound_micro_usd=Decimal(100))
    service._cached = lambda owner, request_hash: None
    service._cache = lambda owner, request_hash, response, usage, reservation=None: None
    return service


def test_swipe_requires_only_bounded_input_and_applies_guarded_decision():
    """Provider usage is settled before the single idempotent feed decision."""
    registry = FakeRegistry()
    usage = FakeUsage()
    result = _service(registry, usage=usage).swipe(OWNER, {
        "vacancy_id": VACANCY, "count": 1, "comment": "focus on API work",
    }, idempotency_key="swipe-1")
    assert result["decisions"] == [{"exposure_id": EXPOSURE, "action": "like"}]
    assert len(usage.reserved) == len(usage.settled) == 1
    assert [call[1] for call in registry.calls] == ["vacancy_get", "candidate_feed", "feed_decision"]


def test_swipe_fails_closed_when_provider_usage_is_missing():
    """A model response without exact usage cannot reach exposure mutation."""
    registry = FakeRegistry()

    class NoUsage:
        def decide(self, vacancy, cards, comment):
            return {"answers": {"0": {"classprobs": {"0": 0.1, "1": 0.9}}}}

    usage = FakeUsage()
    with pytest.raises(JevUsageUnavailable):
        _service(registry, client=NoUsage(), usage=usage).swipe(OWNER, {"vacancy_id": VACANCY, "count": 1}, idempotency_key="swipe-2")
    assert [call[1] for call in registry.calls] == ["vacancy_get", "candidate_feed"]
    assert usage.settled == []


def test_swipe_argument_contract_rejects_extra_provider_controls():
    """Callers cannot inject endpoint, model, or usage values through swipe input."""
    with pytest.raises(OrganizationToolError, match="invalid_jev_arguments"):
        _service().swipe(OWNER, {"vacancy_id": VACANCY, "count": 1, "model": "fake"}, idempotency_key="swipe-3")


def test_new_idempotency_key_gets_a_new_feed_identity():
    """A distinct user key cannot accidentally replay the first feed snapshot."""
    registry = FakeRegistry()
    service = _service(registry)
    arguments = {"vacancy_id": VACANCY, "count": 1, "comment": "focus on API work"}
    first = service.swipe(OWNER, arguments, idempotency_key="swipe-4")
    second = service.swipe(OWNER, arguments, idempotency_key="swipe-5")
    assert first["request_hash"] != second["request_hash"]
    feed_keys = [call[3] for call in registry.calls if call[1] == "candidate_feed"]
    assert len(feed_keys) == 2
    assert feed_keys[0] != feed_keys[1]


def test_billing_replay_reuses_the_original_card_snapshot():
    """A retry after filtering never fetches a new deck from matching."""
    registry = FakeRegistry()

    class Billing:
        def __init__(self):
            self.response = None

        def begin(self, owner, turn, request_hash, *, input_bound, output_bound):
            if self.response is not None:
                return SimpleNamespace(cached=True, cached_response=self.response)
            return SimpleNamespace(cached=False, cached_response=None)

        def settle(self, owner, reservation, response, *, input_tokens, output_tokens, pricing):
            self.response = response

    from agents_service.organization_jev import OrganizationJev
    billing = Billing()
    class ReplayJev(FakeJev):
        def decide(self, vacancy, cards, comment):
            return {"answers": {"0": {"classprobs": {"0": 0.1, "1": 0.9}}},
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                               "input_micro_usd": "2", "output_micro_usd": "1",
                               "rate_config": {"source": "test"}}}

    service = OrganizationJev(ToolRegistry("postgresql://unused"), client=ReplayJev(), billing=billing)
    # Inject the bridge test double after construction; no database is touched.
    service.registry = registry
    cached = {}
    service._cached = lambda owner, request_hash: cached.get(request_hash)
    # The provider cache key is computed by the service; retain the response by
    # calculating the same stable key for the replay probe.
    first = service.swipe(OWNER, {"vacancy_id": VACANCY, "count": 1}, "replay-1")
    key = first["request_hash"]
    cached[key] = billing.response
    before = len(registry.calls)
    service.swipe(OWNER, {"vacancy_id": VACANCY, "count": 1}, "replay-1")
    assert [call[1] for call in registry.calls[before:]] == ["feed_decision"]


def test_real_registry_result_envelope_is_unwrapped():
    """The runtime registry wraps every result; Jev receives actual vacancy fields and cards."""
    class WrappedRegistry(FakeRegistry):
        def dispatch(self, *args, **kwargs):
            return {"result": super().dispatch(*args, **kwargs)}
    registry = WrappedRegistry()
    result = _service(registry=registry).swipe(OWNER, {
        "vacancy_id": VACANCY, "count": 1, "comment": "focus on API work",
    }, "wrapped-1")
    assert len(result["decisions"]) == 1
    assert registry.calls[0][3] != registry.calls[1][3]


def test_invalid_decisions_still_capture_real_paid_usage():
    """Unusable classifications consume tokens and must settle before validation fails."""
    class InvalidJev:
        def decide(self, *_args):
            return {"answers": {}, "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
    usage = FakeUsage()
    with pytest.raises(JevUsageUnavailable):
        _service(client=InvalidJev(), usage=usage).swipe(OWNER, {
            "vacancy_id": VACANCY, "count": 1,
        }, "invalid-decisions-1")
    assert len(usage.settled) == 1
    assert usage.settled[0][2].input_tokens == 10


def test_systemone_selects_labelled_key_and_sends_string_state(tmp_path):
    """Operator files may hold several keys; only the Sverk-labelled credential is eligible."""
    from agents_service.organization_jev import SystemOneJevClient
    file = tmp_path / "credentials.txt"
    file.write_text("DeepSeek\nsk-synthetic-deepseek\nSverk\nsk-synthetic-sverk\nOther\nsk-synthetic-other\n")
    def transport(request):
        assert request.headers["authorization"] == "Bearer sk-synthetic-sverk"
        payload = json.loads(request.content)
        assert isinstance(payload["state"], str)
        assert json.loads(payload["state"])["cards"] == [{"synthetic": True}]
        return httpx.Response(200, json={"model": "kev-latest", "answers": {},
                                         "usage": {"input_tokens": 93, "output_tokens": 73}})
    client = SystemOneJevClient(token_file=str(file), client=httpx.Client(transport=httpx.MockTransport(transport)))
    assert client.decide({}, [{"synthetic": True}], "test")["usage"]["input_tokens"] == 93
    with pytest.raises(ValueError, match="ambiguous"):
        client.credential_from_text("sk-synthetic-one\nsk-synthetic-two")
