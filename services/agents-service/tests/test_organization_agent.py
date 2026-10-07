"""Unit checks for global-agent contracts and fail-closed pricing."""

from decimal import Decimal

import pytest
from agents_service.organization_models import (
    OrganizationMemoryPatch,
    OrganizationTaskRequest,
)
from agents_service.organization_runtime import Pricing


def test_organization_task_contract_is_strict() -> None:
    """Delegated work requires an explicit kind, instruction and idempotency key."""
    value = OrganizationTaskRequest(kind="inquiry", instruction="Ask for availability", idempotency_key="task-1")
    assert value.kind == "inquiry"
    with pytest.raises(ValueError):
        OrganizationMemoryPatch.model_validate({"expected_revision": 1, "value": {}, "unknown": True})


def test_pricing_uses_user_supplied_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The current user tariff is explicit and cached input inherits the input rate."""
    monkeypatch.delenv("AGENT_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENT_OUTPUT_USD_PER_MILLION", raising=False)
    pricing = Pricing.from_env()
    assert pricing.input_usd_per_million == Decimal("0.2")
    assert pricing.output_usd_per_million == Decimal("0.7")


def test_pricing_uses_decimal_micro_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usage receipts retain exact decimal rates instead of binary float rounding."""
    monkeypatch.setenv("AGENT_INPUT_USD_PER_MILLION", "0.125")
    monkeypatch.setenv("AGENT_OUTPUT_USD_PER_MILLION", "0.250")
    pricing = Pricing.from_env()
    assert pricing.cost(8, 4) == (Decimal("1.000000"), Decimal("1.000000"), Decimal("2.000000"))
