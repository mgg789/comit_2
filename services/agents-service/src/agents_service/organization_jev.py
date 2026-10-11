"""Paid, fail-closed Jev Local swipe flow for the organization agent."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

import httpx
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .database import connect
from .organization_billing import BillingError, OrganizationBilling, Pricing
from .organization_tools import OrganizationToolError, ToolRegistry
from .providers import fingerprint


class JevUsageUnavailable(RuntimeError):
    """Jev returned no authoritative usage or the backend budget was unavailable."""

    code = "jev_usage_unavailable"


class JevSwipeArgs(BaseModel):
    """The only user-controlled Jev input; vacancy identity is resolved by the bridge."""

    model_config = ConfigDict(extra="forbid")

    vacancy_id: uuid.UUID
    count: int = Field(strict=True, ge=1, le=50)
    comment: str = Field(default="", max_length=4_000)


class JevUsage(BaseModel):
    """Exact provider and price counters required before any feed decision is written."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(strict=True, ge=0)
    output_tokens: int = Field(strict=True, ge=0)
    input_micro_usd: Decimal = Field(ge=0)
    output_micro_usd: Decimal = Field(ge=0)
    total_micro_usd: Decimal = Field(ge=0)
    rate_config: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_response(cls, value: Any) -> JevUsage:
        """Accept the local decision API's token names only when exact prices exist."""
        if not isinstance(value, Mapping):
            raise JevUsageUnavailable
        input_tokens = value.get("input_tokens", value.get("prompt_tokens"))
        output_tokens = value.get("output_tokens", value.get("completion_tokens"))
        input_cost = value.get("input_micro_usd")
        output_cost = value.get("output_micro_usd")
        if input_cost is None or output_cost is None:
            input_rate = value.get("input_usd_per_million", os.getenv("JEV_INPUT_USD_PER_MILLION", "0.042"))
            output_rate = value.get("output_usd_per_million", os.getenv("JEV_OUTPUT_USD_PER_MILLION", "0"))
            if input_rate is not None and output_rate is not None and input_tokens is not None and output_tokens is not None:
                try:
                    input_cost = Decimal(str(input_tokens)) * Decimal(str(input_rate))
                    output_cost = Decimal(str(output_tokens)) * Decimal(str(output_rate))
                except (ArithmeticError, TypeError, ValueError):
                    input_cost = output_cost = None
        total = value.get("total_micro_usd")
        if input_cost is None or output_cost is None:
            # A provider that exposes one exact total is still acceptable only when
            # both token counts and a declared pricing receipt are present.
            if total is None or input_tokens is None or output_tokens is None or value.get("rate_config") is None:
                raise JevUsageUnavailable
            input_cost, output_cost = Decimal(str(total)), Decimal(0)
        if total is None:
            total = Decimal(str(input_cost)) + Decimal(str(output_cost))
        try:
            usage = cls(input_tokens=input_tokens, output_tokens=output_tokens,
                        input_micro_usd=input_cost, output_micro_usd=output_cost,
                        total_micro_usd=total, rate_config=value.get("rate_config", {}))
        except (ValidationError, TypeError, ValueError):
            raise JevUsageUnavailable from None
        if usage.total_micro_usd != usage.input_micro_usd + usage.output_micro_usd:
            raise JevUsageUnavailable
        return usage


class UsageBackend(Protocol):
    """Cost reservation protocol implemented by the organization budget backend."""

    def reserve(self, owner_id: str, request_hash: str, upper_bound_micro_usd: Decimal) -> Any: ...

    def settle(self, owner_id: str, reservation_id: Any, usage: JevUsage) -> Any: ...


class JevDecisionClient(Protocol):
    """Minimal provider protocol used by the real HTTP client and tests."""

    def decide(self, vacancy: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], comment: str) -> Mapping[str, Any]: ...


class LocalJevClient:
    """Call the configured local decision endpoint using the existing Laya envelope."""

    def __init__(self, base_url: str | None = None, *, client: httpx.Client | None = None) -> None:
        self.base_url = (base_url or os.getenv("JEV_LOCAL_URL", "http://127.0.0.1:12444")).rstrip("/")
        self.client = client or httpx.Client(timeout=90, follow_redirects=False)

    def decide(self, vacancy: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], comment: str) -> Mapping[str, Any]:
        """Send only vacancy/card/comment context and require a JSON object response."""
        questions = {
            str(index): {
                "type": "score",
                "instructions": "Выбери like, если кандидат подходит вакансии по явным фактам, иначе dislike. Не выдумывай данные.",
                "criteria": ["dislike", "like"],
            }
            for index in range(len(cards))
        }
        payload = {"state": {"vacancy": dict(vacancy), "cards": [dict(card) for card in cards], "comment": comment},
                   "questions": questions}
        try:
            response = self.client.post(self.base_url + "/api/v1/decisions", headers={"Host": "localhost:12444"}, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise JevUsageUnavailable from error
        if not isinstance(body, Mapping):
            raise JevUsageUnavailable
        return body


class SystemOneJevClient(LocalJevClient):
    """Fixed HTTPS SystemOne transport for the configured ``kev-latest`` model."""

    def __init__(self, base_url: str | None = None, *, token: str | None = None,
                 token_file: str | None = None, client: httpx.Client | None = None) -> None:
        endpoint = base_url or os.getenv("JEV_API_URL", "https://ai.sverk.tech/v1/systemone")
        parsed = httpx.URL(endpoint)
        if parsed.scheme != "https" or parsed.host != "ai.sverk.tech" or parsed.path.rstrip("/") != "/v1/systemone":
            raise ValueError("JEV_API_URL must be the fixed ai.sverk.tech SystemOne endpoint")
        self.base_url = endpoint.rstrip("/")
        self.client = client or httpx.Client(timeout=90, follow_redirects=False)
        secret = token or os.getenv("JEV_API_KEY")
        if not secret:
            configured_file = token_file or os.getenv("JEV_TOKEN_FILE")
            if configured_file:
                try:
                    with open(configured_file, encoding="utf-8") as source:
                        secret = self.credential_from_text(source.read())
                except (OSError, ValueError):
                    secret = None
        self.token = secret

    @staticmethod
    def credential_from_text(text: str) -> str:
        """Accept a plain key or exactly one Sverk-labelled credential; never select by position."""
        text = text.strip()
        if re.fullmatch(r"sk-[A-Za-z0-9_-]+", text):
            return text
        selected = set()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\bsverk\b", line, re.IGNORECASE):
                block = line
                for following in lines[index + 1:]:
                    if not re.fullmatch(r"\s*(?:(?:token|api[_ -]?key|key)\s*[:=]\s*)?sk-[A-Za-z0-9_-]+\s*", following, re.IGNORECASE):
                        break
                    block += "\n" + following
                selected.update(re.findall(r"(?<![\w-])sk-[A-Za-z0-9_-]+", block))
        if len(selected) != 1:
            raise ValueError("credential_file_ambiguous")
        return selected.pop()

    def decide(self, vacancy: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], comment: str) -> Mapping[str, Any]:
        """Call SystemOne with the same bounded state/questions contract as local Jev."""
        if not self.token:
            raise JevUsageUnavailable
        questions = {
            str(index): {
                "type": "score",
                "instructions": "Выбери like, если кандидат подходит вакансии по явным фактам, иначе dislike. Не выдумывай данные.",
                "criteria": ["dislike", "like"],
            }
            for index in range(len(cards))
        }
        payload = {"model": "kev-latest", "state": json.dumps({"vacancy": dict(vacancy), "cards": [dict(card) for card in cards], "comment": comment}, ensure_ascii=False),
                   "questions": questions}
        try:
            response = self.client.post(self.base_url, headers={"Authorization": "Bearer " + self.token,
                                                                  "content-type": "application/json"}, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise JevUsageUnavailable from error
        if not isinstance(body, Mapping):
            raise JevUsageUnavailable
        return body


class OpenRouterMirrorJevClient:
    """Use the existing Navix mirror; verify choices and exact provider cost receipts."""

    def __init__(self, *, token: str | None = None, client: httpx.Client | None = None) -> None:
        self.base_url = os.getenv("JEV_API_URL", "https://openrouter.droidje.com/api/alpha/decisions")
        if self.base_url != "https://openrouter.droidje.com/api/alpha/decisions":
            raise ValueError("JEV_API_URL must refer to the fixed OpenRouter mirror decisions endpoint")
        self.model = os.getenv("JEV_MODEL", "typesafe/jev-1.13")
        if self.model != "typesafe/jev-1.13":
            raise ValueError("JEV_MODEL must be the configured decision model")
        self.token = token or os.getenv("JEV_API_KEY", "")
        self.client = client or httpx.Client(timeout=90, follow_redirects=False)

    def decide(self, vacancy: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], comment: str) -> Mapping[str, Any]:
        """Normalize only server-owned binary choices; ambiguous calls are never retried."""
        if not self.token:
            raise JevUsageUnavailable
        questions = {str(i): {"type": "choice", "instructions":
            "Choose like only if explicit candidate facts meet the vacancy requirements; otherwise dislike. Do not invent facts.",
            "criteria": {"0": "dislike", "1": "like"}} for i in range(len(cards))}
        state = json.dumps({"vacancy": dict(vacancy), "cards": [dict(c) for c in cards], "comment": comment}, ensure_ascii=False)
        try:
            response = self.client.post(self.base_url, headers={"X-Proxy-Token": self.token},
                json={"model": self.model, "state": {"text": state}, "questions": questions})
            response.raise_for_status()
            body = response.json()
            returned_model = body["model"]
            if returned_model != self.model and not re.fullmatch(re.escape(self.model) + r"-\d{8}", returned_model):
                raise ValueError()
            answers = body["answers"]
            if set(answers) != set(questions):
                raise ValueError()
            normalized = {}
            for key, answer in answers.items():
                probabilities = answer["probabilities"]
                if set(probabilities) != {"0", "1"} or answer["choice"] not in probabilities:
                    raise ValueError()
                values = list(probabilities.values())
                confidence = answer.get("confidence")
                if (any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                        or abs(sum(values) - 1) > 0.02
                        or type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0.5 <= confidence <= 1
                        or max(values) <= 0.5 or answer["choice"] != max(probabilities, key=probabilities.get)):
                    raise ValueError()
                normalized[key] = {"classprobs": probabilities, "confidence": confidence}
            usage = body["usage"]
            if type(usage["input_tokens"]) is not int or type(usage["output_tokens"]) is not int:
                raise ValueError()
            if min(usage["input_tokens"], usage["output_tokens"]) < 0:
                raise ValueError()
            pricing = OrganizationJev._pricing()
            input_cost = Decimal(usage["input_tokens"]) * pricing.input_usd_per_million
            output_cost = Decimal(usage["output_tokens"]) * pricing.output_usd_per_million
            total = Decimal(str(usage["cost"])) * Decimal(1_000_000)
            if not total.is_finite() or total != input_cost + output_cost:
                raise ValueError()
            return {"answers": normalized, "usage": {"input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"], "input_micro_usd": str(input_cost),
                "output_micro_usd": str(output_cost), "total_micro_usd": str(total),
                "rate_config": {"source": "openrouter_provider_receipt", "model": returned_model}}}
        except (httpx.HTTPError, ArithmeticError, ValueError, KeyError, TypeError):
            raise JevUsageUnavailable from None


class _StoreUsageBackend:
    """Adapter around the existing owner budget reservation and ledger tables."""

    def __init__(self, database_url: str, *, upper_bound_micro_usd: Decimal) -> None:
        self.database_url = database_url
        self.upper_bound = upper_bound_micro_usd

    def reserve(self, owner_id: str, request_hash: str, upper_bound_micro_usd: Decimal) -> uuid.UUID:
        """Reserve the configured worst-case Jev cost before calling the provider."""
        from .organization_store import OrganizationStore
        try:
            return OrganizationStore(self.database_url).reserve_usage(
                uuid.UUID(owner_id), uuid.UUID(int=0), upper_bound_micro_usd,
                input_bound=upper_bound_micro_usd, output_bound=Decimal(0),
            )
        except Exception as error:
            raise JevUsageUnavailable from error

    def settle(self, owner_id: str, reservation_id: Any, usage: JevUsage) -> Any:
        """Capture the exact provider usage; missing prices never become free usage."""
        from .organization_store import OrganizationStore
        try:
            store = OrganizationStore(self.database_url)
            try:
                return store.capture_usage(
                    uuid.UUID(owner_id), uuid.UUID(str(reservation_id)), input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens, input_micro_usd=usage.input_micro_usd,
                    output_micro_usd=usage.output_micro_usd, rate_config=usage.rate_config,
                    input_bound_micro_usd=usage.input_micro_usd, output_bound_micro_usd=usage.output_micro_usd,
                )
            except TypeError:
                # Compatibility with the pre-bound-capture OrganizationStore;
                # current deployments expose the stricter bound parameters.
                return store.capture_usage(
                    uuid.UUID(owner_id), uuid.UUID(str(reservation_id)), input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens, input_micro_usd=usage.input_micro_usd,
                    output_micro_usd=usage.output_micro_usd, rate_config=usage.rate_config,
                )
        except Exception as error:
            raise JevUsageUnavailable from error


class OrganizationJev:
    """Run Jev card classification and apply guarded decisions through the bridge."""

    def __init__(self, registry: ToolRegistry, *, client: JevDecisionClient | None = None,
                 usage_backend: UsageBackend | None = None, database_url: str | None = None,
                 upper_bound_micro_usd: Decimal | None = None,
                 billing: OrganizationBilling | None = None) -> None:
        self.registry = registry
        if client is not None:
            self.client = client
        elif os.getenv("JEV_PROVIDER", "systemone").lower() == "local":
            self.client = LocalJevClient()
        elif os.getenv("JEV_PROVIDER", "systemone").lower() == "openrouter-mirror":
            self.client = OpenRouterMirrorJevClient()
        else:
            self.client = SystemOneJevClient()
        bound = upper_bound_micro_usd
        self.usage_backend = usage_backend or _StoreUsageBackend(database_url or registry.database_url,
                                                                  upper_bound_micro_usd=bound)
        self.upper_bound_micro_usd = bound
        # Injected unit-test usage backends intentionally bypass PostgreSQL;
        # production uses OrganizationBilling's atomic provider/cache receipt.
        self.billing = billing if billing is not None else (
            None if usage_backend is not None else OrganizationBilling(database_url or registry.database_url)
        )

    @staticmethod
    def _pricing() -> Pricing:
        """Build the user-confirmed Jev tariff snapshot without treating output as free input."""
        try:
            input_rate = Decimal(os.getenv("JEV_INPUT_USD_PER_MILLION", "0.042"))
            output_rate = Decimal(os.getenv("JEV_OUTPUT_USD_PER_MILLION", "0"))
        except (ArithmeticError, ValueError):
            raise JevUsageUnavailable from None
        if not input_rate.is_finite() or not output_rate.is_finite() or input_rate <= 0 or output_rate < 0:
            raise JevUsageUnavailable
        mirror = os.getenv("JEV_PROVIDER", "systemone").lower() == "openrouter-mirror"
        return Pricing(input_rate, output_rate, input_rate,
                       provider="openrouter-mirror" if mirror else "systemone",
                       model="typesafe/jev-1.13" if mirror else "kev-latest",
                       version=os.getenv("JEV_PRICING_VERSION", "openrouter-2026-10-11" if mirror else "user-2026-10-07"))

    @staticmethod
    def _args(arguments: Mapping[str, Any]) -> JevSwipeArgs:
        """Reject any Jev control field outside vacancy, count, and comment."""
        try:
            return JevSwipeArgs.model_validate(dict(arguments))
        except (ValidationError, TypeError):
            raise OrganizationToolError("invalid_jev_arguments", status=422) from None

    @staticmethod
    def _cards(feed: Any, count: int) -> list[dict[str, Any]]:
        """Extract bounded cards while preserving only JSON model context."""
        if isinstance(feed, Mapping):
            raw = feed.get("cards", feed.get("items", []))
        else:
            raw = feed
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw[:count] if isinstance(item, Mapping)]

    @staticmethod
    def _action(answer: Any) -> str | None:
        """Normalize explicit action or class probabilities into a guarded action."""
        if not isinstance(answer, Mapping):
            return None
        action = answer.get("action", answer.get("class", answer.get("selected_class")))
        if isinstance(action, str) and action in {"like", "dislike"}:
            return action
        probabilities = answer.get("classprobs", answer.get("class_probs", answer.get("probabilities", answer.get("probs"))))
        if not isinstance(probabilities, Mapping) or not probabilities:
            return None
        try:
            selected = max(probabilities, key=lambda key: float(probabilities[key]))
        except (TypeError, ValueError):
            return None
        if selected in {"like", "dislike"}:
            return str(selected)
        if str(selected) in {"0", "1"}:
            return "dislike" if str(selected) == "0" else "like"
        return None

    @classmethod
    def _decisions(cls, response: Mapping[str, Any], cards: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Require one explicit model decision per card before any bridge mutation."""
        answers = response.get("answers")
        if not isinstance(answers, Mapping):
            raise JevUsageUnavailable
        result: list[dict[str, str]] = []
        for index, card in enumerate(cards):
            exposure = card.get("exposure_id")
            if not isinstance(exposure, str):
                continue
            answer = answers.get(str(index), answers.get(exposure))
            action = cls._action(answer)
            if action is None:
                raise JevUsageUnavailable
            result.append({"exposure_id": exposure, "action": action})
        return result

    def _cached(self, owner_id: str, request_hash: str) -> Mapping[str, Any] | None:
        """Read a completed provider response before creating another paid reservation."""
        try:
            with connect(self.registry.database_url) as connection:
                row = connection.execute(
                    "SELECT response FROM agents_service.organization_provider_calls WHERE owner_id=%s AND request_hash=%s AND status='completed'",
                    (owner_id, request_hash),
                ).fetchone()
                return row["response"] if row and isinstance(row.get("response"), Mapping) else None
        except Exception as error:
            raise JevUsageUnavailable from error

    def _cache(self, owner_id: str, request_hash: str, response: Mapping[str, Any], usage: JevUsage,
               reservation_id: Any = None) -> None:
        """Persist provider output and exact usage in the owner-scoped call ledger."""
        try:
            with connect(self.registry.database_url) as connection:
                connection.execute(
                    "INSERT INTO agents_service.organization_provider_calls(owner_id,request_hash,status,response,input_tokens,output_tokens,reservation_id) "
                    "VALUES(%s,%s,'completed',%s,%s,%s,%s) ON CONFLICT(owner_id,request_hash) DO NOTHING",
                    (owner_id, request_hash, Jsonb(json.loads(json.dumps(dict(response), default=str))), usage.input_tokens, usage.output_tokens, reservation_id),
                )
        except Exception as error:
            raise JevUsageUnavailable from error

    def swipe(self, owner_id: str, arguments: Mapping[str, Any], idempotency_key: str,
              task_id: str | None = None) -> dict[str, Any]:
        """Classify one bounded feed and apply every resulting action idempotently."""
        args = self._args(arguments)
        owner = str(uuid.UUID(owner_id))
        # The user idempotency key is part of the paid deck identity.  A new
        # key is an explicit request for a new feed snapshot; only a retry with
        # the same key may replay the original provider result and deck.
        request_hash = fingerprint({"vacancy_id": str(args.vacancy_id), "count": args.count,
                                    "comment": args.comment, "idempotency_key": idempotency_key,
                                    "provider": self._pricing().provider, "model": self._pricing().model})
        response: Mapping[str, Any] | None = None
        reservation: Any = None
        pricing = self._pricing()
        # Probe the completed ledger before touching matching. This is what makes
        # a retry reuse the original card deck after the first attempt filtered it.
        cached = self._cached(owner, request_hash)
        if cached is not None:
            response = cached
            vacancy = response.get("vacancy")
            cards = response.get("cards")
            if not isinstance(vacancy, Mapping) or not isinstance(cards, list):
                raise JevUsageUnavailable
            cards = [dict(card) for card in cards if isinstance(card, Mapping)]
        else:
            # Reads happen through the same owner-authenticated registry boundary.
            read_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"comit:jev:read:{owner}:{args.vacancy_id}:{idempotency_key}"))
            def unwrap(value):
                return value.get("result", value) if isinstance(value, Mapping) else value

            vacancy = unwrap(self.registry.dispatch(owner, "vacancy_get", {"vacancy_id": str(args.vacancy_id)},
                str(uuid.uuid5(uuid.NAMESPACE_URL, read_key + ":vacancy")), task_id))
            feed = unwrap(self.registry.dispatch(owner, "candidate_feed", {"vacancy_id": str(args.vacancy_id), "limit": args.count},
                str(uuid.uuid5(uuid.NAMESPACE_URL, read_key + ":feed:0")), task_id))
            cards = self._cards(feed, args.count)
            # A matching page may be smaller than the requested count. Continue the
            # same owner-scoped session until count is reached or the reserve ends.
            seen_exposures = {card.get("exposure_id") for card in cards}
            session_id = feed.get("session_id") if isinstance(feed, Mapping) else None
            page_index = 0
            while len(cards) < args.count and isinstance(session_id, str):
                page_index += 1
                page = unwrap(self.registry.dispatch(owner, "candidate_feed_continue", {
                    "vacancy_id": str(args.vacancy_id), "session_id": session_id,
                }, str(uuid.uuid5(uuid.NAMESPACE_URL, read_key + f":feed:{page_index}")), task_id))
                page_cards = self._cards(page, args.count - len(cards))
                fresh = [card for card in page_cards if card.get("exposure_id") not in seen_exposures]
                if not fresh:
                    break
                cards.extend(fresh)
                seen_exposures.update(card.get("exposure_id") for card in fresh)
                session_id = page.get("session_id") if isinstance(page, Mapping) else None
            context = {"vacancy": vacancy, "cards": cards, "comment": args.comment}
            if not cards:
                return {"vacancy_id": str(args.vacancy_id), "decisions": [], "applied": [], "status": "no_cards", "usage": None}
        if response is None and self.billing is not None:
            try:
                max_output_tokens = int(os.getenv("JEV_MAX_OUTPUT_TOKENS", "0"))
            except ValueError:
                raise JevUsageUnavailable from None
            if pricing.output_usd_per_million > 0 and max_output_tokens <= 0:
                raise JevUsageUnavailable
            bound = self.upper_bound_micro_usd
            output_rate = pricing.output_usd_per_million
            if bound is None:
                # UTF-8 bytes are a conservative lower-level proxy for input
                # tokens.  Add no free output allowance: the configured Jev
                # output tariff is zero unless the deployment overrides it.
                input_rate = pricing.input_usd_per_million
                protocol = {
                    "model": pricing.model,
                    "state": context,
                    "questions": {str(index): {"type": "score", "criteria": ["dislike", "like"]}
                                  for index in range(len(cards))},
                }
                request_bytes = len(json.dumps(protocol, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                # SystemOne may classify each question separately with shared state.
                # Reserve the full state for every question plus service framing.
                bound = Decimal((request_bytes + 4096) * max(1, len(cards))) * input_rate
            try:
                turn_id = uuid.uuid5(uuid.NAMESPACE_URL, f"comit:jev:turn:{owner}:{idempotency_key}")
                reservation = self.billing.begin(uuid.UUID(owner), turn_id, request_hash,
                                                 input_bound=bound, output_bound=Decimal(max_output_tokens) * output_rate)
                if reservation.cached:
                    response = reservation.cached_response
                    vacancy = response.get("vacancy") if isinstance(response, Mapping) else None
                    cards = response.get("cards") if isinstance(response, Mapping) else None
                    if not isinstance(vacancy, Mapping) or not isinstance(cards, list):
                        raise JevUsageUnavailable
            except JevUsageUnavailable:
                raise
            except Exception as error:
                raise JevUsageUnavailable from error
        elif response is None:
            if self.usage_backend is None:
                raise JevUsageUnavailable
            bound = self.upper_bound_micro_usd
            if bound is None:
                input_rate = pricing.input_usd_per_million
                request_bytes = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                bound = Decimal(request_bytes) * input_rate
            try:
                reservation = self.usage_backend.reserve(owner, request_hash, bound)
            except Exception as error:
                raise JevUsageUnavailable from error
        if response is not None:
            try:
                usage = JevUsage.model_validate(response.get("usage"))
            except (ValidationError, AttributeError):
                raise JevUsageUnavailable from None
        else:
            try:
                raw = self.client.decide(vacancy if isinstance(vacancy, Mapping) else {}, cards, args.comment)
                usage = JevUsage.from_response(raw.get("usage") if isinstance(raw, Mapping) else None)
                response = {"usage": usage.model_dump(mode="json"), "provider": raw,
                            "vacancy": vacancy, "cards": cards, "comment": args.comment}
                if self.billing is not None:
                    self.billing.settle(uuid.UUID(owner), reservation, response,
                                        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                                        pricing=pricing)
                else:
                    self.usage_backend.settle(owner, reservation, usage)
            except JevUsageUnavailable:
                raise
            except BillingError as error:
                raise JevUsageUnavailable from error
            except Exception as error:
                raise JevUsageUnavailable from error
            if self.billing is None:
                self._cache(owner, request_hash, response, usage, reservation)
        decisions = response.get("decisions")
        if decisions is None:
            decisions = self._decisions(response.get("provider", {}), cards)
        if not isinstance(decisions, list):
            raise JevUsageUnavailable
        applied: list[Any] = []
        for decision in decisions:
            if not isinstance(decision, Mapping):
                raise JevUsageUnavailable
            exposure = decision.get("exposure_id")
            action = decision.get("action")
            if not isinstance(exposure, str) or action not in {"like", "dislike"}:
                raise JevUsageUnavailable
            decision_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"comit:jev:{owner}:{request_hash}:{exposure}"))
            applied.append(self.registry.dispatch(owner, "feed_decision", {
                "vacancy_id": str(args.vacancy_id), "exposure_id": exposure, "action": action,
                "idempotency_key": decision_key,
            }, decision_key, task_id))
        return {"vacancy_id": str(args.vacancy_id), "request_hash": request_hash,
                "decisions": decisions, "applied": applied, "usage": response.get("usage")}


JevService = OrganizationJev
JevClient = SystemOneJevClient
