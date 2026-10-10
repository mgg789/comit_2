"""Pure billing invariants; PostgreSQL receipt tests run in the integration suite."""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from agents_service.database import connect
from agents_service.organization_billing import (
    BillingError,
    OrganizationBilling,
    Pricing,
)
from agents_service.organization_store import OrganizationStore


def _integration_url() -> str:
    value = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not value:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    return value


def _delete_owner(database_url: str, owner_id: object) -> None:
    with connect(database_url) as db:
        db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner_id,))


def test_default_deepseek_pricing_and_rate_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured user tariff is positive, finite and versioned."""
    monkeypatch.delenv("AGENT_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENT_OUTPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENT_CACHED_INPUT_USD_PER_MILLION", raising=False)
    pricing = Pricing.from_env()
    assert pricing.input_usd_per_million == Decimal("0.2")
    assert pricing.output_usd_per_million == Decimal("0.7")
    assert pricing.cached_input_usd_per_million == Decimal("0.2")
    assert len(pricing.rate_hash) == 64


def test_invalid_nonfinite_rate_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """NaN and infinity cannot enter a reservation or ledger receipt."""
    monkeypatch.setenv("AGENT_INPUT_USD_PER_MILLION", "NaN")
    with pytest.raises(BillingError, match="usage_pricing_invalid"):
        Pricing.from_env()


def test_input_bound_counts_utf8_json_and_framing() -> None:
    """The preflight bound grows with Unicode bytes and tool framing."""
    small = OrganizationBilling.input_token_bound([{"role": "user", "content": "x"}], [])
    large = OrganizationBilling.input_token_bound([{"role": "user", "content": "привет" * 20}], [{"type": "function"}])
    assert large > small


def test_postgres_concurrent_reservations_are_owner_budget_serialized() -> None:
    """Two simultaneous upper bounds cannot exceed the one-million micro-USD cap."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    try:
        store.get_or_create_chat(owner_id)
        turn = store.create_turn(owner_id, "budget test", "billing-concurrency")

        def reserve(suffix: str):
            try:
                return ("ok", OrganizationBilling(database_url).begin(
                    owner_id, turn.turn_id, "concurrent-" + suffix,
                    input_bound=Decimal(600000), output_bound=Decimal(0),
                ))
            except BillingError as error:
                return (error.code, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ("a", "b")))
        assert sum(status == "ok" for status, _ in results) == 1
        assert any(status == "organization_usage_limit_exceeded" for status, _ in results)
        winner = next(item for status, item in results if status == "ok")
        OrganizationBilling(database_url).settle(
            owner_id, winner, {"choices": []}, input_tokens=1, output_tokens=0,
            pricing=Pricing.from_env(),
        )
    finally:
        _delete_owner(database_url, owner_id)


def test_postgres_completed_provider_response_is_replayed_without_reservation() -> None:
    """A completed receipt is returned before budget accounting and cannot be double charged."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    try:
        store.get_or_create_chat(owner_id)
        turn = store.create_turn(owner_id, "cache test", "billing-cache")
        billing = OrganizationBilling(database_url)
        first = billing.begin(owner_id, turn.turn_id, "cache-request", input_bound=Decimal(100), output_bound=Decimal(100))
        response = {"choices": [{"message": {"content": "cached"}}]}
        billing.settle(owner_id, first, response, input_tokens=1, output_tokens=1, pricing=Pricing.from_env())
        second = billing.begin(owner_id, turn.turn_id, "cache-request", input_bound=Decimal(900000), output_bound=Decimal(900000))
        assert second.cached and second.cached_response == response and second.reservation_id is None
        with connect(database_url) as db:
            count = db.execute("SELECT count(*) AS count FROM agents_service.organization_usage_reservations WHERE owner_id=%s", (owner_id,)).fetchone()["count"]
        assert count == 1
    finally:
        _delete_owner(database_url, owner_id)
