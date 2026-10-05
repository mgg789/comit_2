"""Bounded real provider clients; no synthetic replacement on network/model failure."""

import hashlib
import json
import os
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import ValidationError

from agents_service.models import MatchingStatus, SchemaSnapshot


class ProviderError(RuntimeError):
    """Safe error code only: upstream bodies and credentials must never enter logs."""

    def __init__(self, code: str, retryable: bool = True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def fingerprint(value: object) -> str:
    """Hash canonical JSON for owner-scoped caching and reproducible derivation."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict:
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError("invalid_provider_response", False)
        return payload
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        raise ProviderError(f"upstream_http_{status}", status in (408, 429) or status >= 500) from None
    except httpx.TimeoutException:
        raise ProviderError("provider_timeout") from None
    except (httpx.HTTPError, ValueError):
        raise ProviderError("provider_unavailable") from None


class MatchingClient:
    """Trusted matching transport; schema is fetched fresh at approve and Create."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 client: httpx.Client | None = None):
        self.base_url = (base_url or os.getenv("MATCHING_ENGINE_URL", "http://matching-engine:8101")).rstrip("/")
        self.token = token or os.getenv("CANDIDATE_PIPELINE_TOKEN", "")
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.token:
            raise ProviderError("matching_not_configured", False)
        return _request(self.client, method, self.base_url + path,
                        headers={"X-Internal-Token": self.token}, **({"json": payload} if payload is not None else {}))

    def schema(self) -> SchemaSnapshot:
        """Return matching's current coordinate registry as the shared draft schema."""
        try:
            return SchemaSnapshot.model_validate(self._call("GET", "/internal/strict-schema"))
        except ValidationError:
            raise ProviderError("invalid_matching_schema", False) from None

    def publish(self, vacancy_id: str, snapshot: dict) -> dict:
        """Idempotently send the entire confirmed snapshot; ACK is not technical ready."""
        try:
            result = MatchingStatus.model_validate(self._call("PUT", f"/internal/vacancies/{vacancy_id}", snapshot))
            if result.vacancy_id != UUID(vacancy_id) or result.source_revision != snapshot["source_revision"] or result.status != "open" or result.missing_requirements:
                raise ValueError()
            return result.model_dump(mode="json")
        except (ValidationError, ValueError):
            raise ProviderError("invalid_matching_ack", False) from None

    def status(self, vacancy_id: str) -> dict:
        """Read matching technical status without changing selection state."""
        try:
            return MatchingStatus.model_validate(self._call("GET", f"/internal/vacancies/{vacancy_id}")).model_dump(mode="json")
        except ValidationError:
            raise ProviderError("invalid_matching_status", False) from None


class LayaClient:
    """Form-only Jev Local ordinal decisions, preserving absent versus explicit zero."""

    model = "aac6fef/laya-multilingual-mlx"
    version = "laya-requirements-v1"

    def __init__(self, base_url: str | None = None, client: httpx.Client | None = None):
        self.base_url = (base_url or os.getenv("JEV_LOCAL_URL", "http://127.0.0.1:12444")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.hostname not in ("localhost", "127.0.0.1", "host.docker.internal"):
            raise ValueError("JEV_LOCAL_URL must refer to the local host")
        self.client = client or httpx.Client(timeout=90, follow_redirects=False)

    def derive(self, fields: dict, schema: dict) -> dict:
        """Evaluate every current coordinate in batches <=32; return editable groups and evidence."""
        groups = {"domain": "domens", "stack": "stacks", "soft_skill": "soft_skills"}
        result = {name: {} for name in groups.values()}
        evidence = {}
        coordinates = schema["coordinates"]
        state = {key: fields.get(key) for key in ("title", "class", "bio", "daily", "city", "type", "zp_min", "zp_max")}
        rubric = ["Не требуется и не упомянуто", "Явно задан уровень 0: требований нет",
                  "Уровень 1: минимальная потребность", "Уровень 2: слабая потребность",
                  "Уровень 3: желательный навык", "Уровень 4: существенное требование",
                  "Уровень 5: важное требование", "Уровень 6: сильное требование",
                  "Уровень 7: ключевое обязательное требование"]
        for start in range(0, len(coordinates), 24):
            batch = coordinates[start:start + 24]
            questions = {str(c["index"]): {"type": "score", "instructions":
                f"Какова относительная потребность вакансии в {c['group']} {c['key']}? "
                "Используй только явно заданное описание. Не выдумывай навыки из класса. "
                "Если навык не требуется или не упомянут, выбирай первое значение.", "criteria": rubric}
                for c in batch}
            response = _request(self.client, "POST", self.base_url + "/api/v1/decisions",
                                headers={"Host": "localhost:12444"}, json={"state": state, "questions": questions})
            answers = response.get("answers")
            if not isinstance(answers, dict) or set(answers) != set(questions):
                raise ProviderError("invalid_laya_answers", False)
            for coordinate in batch:
                answer = answers[str(coordinate["index"])]
                probs = answer.get("probabilities") if isinstance(answer, dict) else None
                try:
                    if not isinstance(probs, dict) or set(probs) != {str(i) for i in range(9)}:
                        raise ValueError()
                    values = [float(probs[str(i)]) for i in range(9)]
                    if any(not 0 <= p <= 1 for p in values) or abs(sum(values) - 1) > .02:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ProviderError("invalid_laya_probabilities", False) from None
                selected = max(range(9), key=lambda i: values[i])
                if selected:
                    result[groups[coordinate["group"]]][coordinate["key"]] = selected - 1
                evidence[f"{coordinate['group']}:{coordinate['key']}"] = {"selected": selected,
                    "probabilities": probs, "confidence": answer.get("confidence")}
        return {"fields": result, "provenance": {"model": self.model, "version": self.version,
                "input_hash": fingerprint(state), "schema_hash": schema["schema_hash"], "evidence": evidence}}


class DeepSeekClient:
    """OpenAI-compatible non-thinking tool calls; credentials remain only in worker env."""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 token: str | None = None, client: httpx.Client | None = None):
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.token = token or os.getenv("DEEPSEEK_API_KEY", "")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com" or parsed.username or parsed.password:
            raise ValueError("DeepSeek transport requires the official HTTPS endpoint")
        self.client = client or httpx.Client(timeout=90, follow_redirects=False)

    def complete(self, messages: list[dict], tools: list[dict], max_tokens: int = 1536) -> dict:
        """Return a checked response envelope; runtime persists it before executing tools."""
        if not self.token:
            raise ProviderError("deepseek_not_configured", False)
        result = _request(self.client, "POST", self.base_url + "/chat/completions",
                          headers={"Authorization": "Bearer " + self.token}, json={"model": self.model,
                          "messages": messages, "tools": tools, "max_tokens": max_tokens,
                          "thinking": {"type": "disabled"}, "stream": False})
        try:
            message = result["choices"][0]["message"]
            usage = result["usage"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError()
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if type(usage[key]) is not int or usage[key] < 0:
                    raise ValueError()
            if usage["completion_tokens"] > max_tokens:
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("invalid_deepseek_response", False) from None
        return result
