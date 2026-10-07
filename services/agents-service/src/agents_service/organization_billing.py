"""Atomic provider-call receipts, reservations and exact micro-USD ledger rows."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .database import connect


class BillingError(RuntimeError):
    """Fail-closed billing error; callers must not retry an ambiguous paid call."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Pricing:
    """Immutable decimal tariff snapshot included in every ledger receipt."""

    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    version: str = "user-2026-10-07"

    @classmethod
    def from_env(cls, *, provider: str = "deepseek", model: str = "deepseek-chat") -> Pricing:
        """Load the user-supplied DeepSeek tariffs with finite positive decimals."""
        raw_input = os.getenv("AGENT_INPUT_USD_PER_MILLION", "0.2")
        raw_output = os.getenv("AGENT_OUTPUT_USD_PER_MILLION", "0.7")
        raw_cached = os.getenv("AGENT_CACHED_INPUT_USD_PER_MILLION", raw_input)
        try:
            values = [Decimal(raw_input), Decimal(raw_output), Decimal(raw_cached)]
        except (InvalidOperation, ValueError) as error:
            raise BillingError("usage_pricing_invalid") from error
        if any(not value.is_finite() or value <= 0 for value in values):
            raise BillingError("usage_pricing_invalid")
        return cls(values[0], values[1], values[2], provider=provider, model=model)

    @property
    def rate_hash(self) -> str:
        """Stable hash of provider, model, version and all applicable rates."""
        value = {"provider": self.provider, "model": self.model, "version": self.version,
                 "input": str(self.input_usd_per_million), "output": str(self.output_usd_per_million),
                 "cached_input": str(self.cached_input_usd_per_million)}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def costs(self, input_tokens: int, output_tokens: int, *, cached: bool = False) -> tuple[Decimal, Decimal, Decimal]:
        """Compute decimal micro-USD charges without binary floating point."""
        if type(input_tokens) is not int or type(output_tokens) is not int or input_tokens < 0 or output_tokens < 0:
            raise BillingError("provider_usage_invalid")
        input_rate = self.cached_input_usd_per_million if cached else self.input_usd_per_million
        one = Decimal("0.000001")
        input_cost = (Decimal(input_tokens) * input_rate).quantize(one, rounding=ROUND_CEILING)
        output_cost = (Decimal(output_tokens) * self.output_usd_per_million).quantize(one, rounding=ROUND_CEILING)
        return input_cost, output_cost, input_cost + output_cost

    def cost(self, input_tokens: int, output_tokens: int) -> tuple[Decimal, Decimal, Decimal]:
        """Backward-compatible alias for callers that do not price cached input."""
        return self.costs(input_tokens, output_tokens)


@dataclass(frozen=True)
class BillingReservation:
    """Result of the atomic pre-call fence."""

    request_hash: str
    reservation_id: UUID | None
    cached_response: dict[str, Any] | None = None

    @property
    def cached(self) -> bool:
        """Whether the provider response can be replayed without a new paid call."""
        return self.cached_response is not None


class OrganizationBilling:
    """Perform provider cache lookup and budget reservation in one database transaction."""

    def __init__(self, database_url: str, *, limit_micro_usd: Decimal = Decimal(1000000)) -> None:
        self.database_url = database_url
        self.limit_micro_usd = limit_micro_usd

    @staticmethod
    def input_token_bound(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        """Bound input tokens from UTF-8 JSON bytes plus framing, never from a guessed ratio."""
        body = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        framing = 32 + 8 * len(messages) + 16 * len(tools)
        return len(body) + framing

    def begin(self, owner_id: UUID, turn_id: UUID, request_hash: str, *, input_bound: Decimal,
              output_bound: Decimal) -> BillingReservation:
        """Lookup completed cache before creating any reservation; fence in-flight calls forever."""
        amount = input_bound + output_bound
        if (not all(isinstance(value, Decimal) and value.is_finite() for value in (input_bound, output_bound))
                or amount < 0 or input_bound < 0 or output_bound < 0):
            raise BillingError("usage_reservation_invalid")
        with connect(self.database_url) as db, db.transaction():
            db.execute("SELECT owner_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            prior = db.execute("SELECT status,response FROM agents_service.organization_provider_calls WHERE owner_id=%s AND request_hash=%s FOR UPDATE",
                               (owner_id, request_hash)).fetchone()
            if prior is not None:
                if prior["status"] == "completed" and prior["response"] is not None:
                    return BillingReservation(request_hash, None, dict(prior["response"]))
                raise BillingError("provider_call_in_flight")
            totals = db.execute("""SELECT
                COALESCE((SELECT SUM(total_micro_usd) FROM agents_service.organization_usage_ledger WHERE owner_id=%s AND created_at>=now()-interval '7 days'),0) spent,
                COALESCE((SELECT SUM(upper_bound_micro_usd) FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND status='reserved'),0) reserved""",
                               (owner_id, owner_id)).fetchone()
            spent = Decimal(str(totals["spent"] or 0))
            reserved = Decimal(str(totals["reserved"] or 0))
            if spent + reserved + amount > self.limit_micro_usd:
                raise BillingError("organization_usage_limit_exceeded")
            reservation_id = uuid4()
            db.execute("""INSERT INTO agents_service.organization_usage_reservations
                (reservation_id,owner_id,turn_id,input_upper_bound_micro_usd,output_upper_bound_micro_usd,upper_bound_micro_usd,status)
                VALUES (%s,%s,%s,%s,%s,%s,'reserved')""",
                        (reservation_id, owner_id, turn_id, input_bound, output_bound, amount))
            db.execute("INSERT INTO agents_service.organization_provider_calls(owner_id,request_hash,status,reservation_id) VALUES (%s,%s,'in_flight',%s)",
                        (owner_id, request_hash, reservation_id))
            return BillingReservation(request_hash, reservation_id)

    def settle(self, owner_id: UUID, reservation: BillingReservation, response: Mapping[str, Any], *,
               input_tokens: int, output_tokens: int, pricing: Pricing) -> Decimal:
        """Persist exact usage, ledger and completed response cache atomically."""
        if reservation.reservation_id is None:
            return Decimal(0)
        input_cost, output_cost, total = pricing.costs(input_tokens, output_tokens)
        with connect(self.database_url) as db, db.transaction():
            row = db.execute("""SELECT * FROM agents_service.organization_usage_reservations
                WHERE reservation_id=%s AND owner_id=%s FOR UPDATE""", (reservation.reservation_id, owner_id)).fetchone()
            if row is None:
                raise BillingError("usage_reservation_not_found")
            if row["status"] == "captured":
                return total
            if (input_cost > Decimal(str(row["input_upper_bound_micro_usd"]))
                    or output_cost > Decimal(str(row["output_upper_bound_micro_usd"]))
                    or total > Decimal(str(row["upper_bound_micro_usd"]))):
                raise BillingError("provider_usage_exceeded_reservation")
            db.execute("UPDATE agents_service.organization_usage_reservations SET status='captured',updated_at=now() WHERE reservation_id=%s",
                        (reservation.reservation_id,))
            db.execute("""INSERT INTO agents_service.organization_usage_ledger
                (ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(reservation_id) DO NOTHING""",
                        (uuid4(), reservation.reservation_id, owner_id, input_tokens, output_tokens, input_cost,
                         output_cost, total, Jsonb({"provider": pricing.provider, "model": pricing.model,
                                                    "version": pricing.version, "rate_hash": pricing.rate_hash,
                                                    "input_usd_per_million": str(pricing.input_usd_per_million),
                                                    "output_usd_per_million": str(pricing.output_usd_per_million),
                                                    "cached_input_usd_per_million": str(pricing.cached_input_usd_per_million)})))
            updated = db.execute("""UPDATE agents_service.organization_provider_calls
                SET status='completed',response=%s,input_tokens=%s,output_tokens=%s,updated_at=now()
                WHERE owner_id=%s AND request_hash=%s AND status='in_flight' RETURNING request_hash""",
                                 (Jsonb(dict(response)), input_tokens, output_tokens, owner_id, reservation.request_hash)).fetchone()
            if updated is None:
                raise BillingError("provider_call_fence_missing")
        return total
