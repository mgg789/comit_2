"""Answer checks; candidate code only runs through the isolated broker."""

import hashlib
import json
import os
import ssl
import urllib.request
from pathlib import Path

from testing_engine.rubric import RubricJudge


def check_mcq(payload: dict, answer: int | str) -> bool | None:
    """Compare a validated option index to the private bank answer."""
    try:
        return int(answer) == payload["answer_index"]
    except (TypeError, ValueError, KeyError):
        return None


class LocalPythonJudge:
    """Removed unsafe legacy executor: even fixture flags cannot enable it."""

    def judge(self, payload: dict, code: str) -> tuple[None, str]:
        """Never execute untrusted Python in testing-engine or its host."""
        return None, "local_judge_disabled"


class CodeJudgeClient:
    """Authenticated client of the isolated broker, with no local fallback."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("CODE_JUDGE_URL", "")).rstrip("/")
        self.token = os.getenv("CODE_JUDGE_TOKEN", "")

    def configured(self) -> bool:
        """Require both internal endpoint and explicit service credential."""
        return self.base_url.startswith(("http://", "https://")) and bool(self.token)

    def _request(self, route: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(self.base_url + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "x-internal-token": self.token})
        context = ssl.create_default_context()
        private_ca = os.getenv("CODE_JUDGE_CA_PEM", "").replace("\\n", "\n")
        if private_ca:
            context.load_verify_locations(cadata=private_ca)
        with urllib.request.urlopen(request, timeout=55 if body else 12, context=context) as response:
            record = json.loads(response.read(65537))
        if not isinstance(record, dict):
            raise TypeError("invalid_judge_response")
        return record

    def ready(self) -> bool:
        """Probe the broker's actual sandbox, not merely the HTTP liveness."""
        try:
            return self.configured() and self._request("/ready").get("runner") == "docker-isolated"
        except (OSError, ValueError, TypeError):
            return False

    def judge(self, payload: dict, code: str, *, owner: str = "offline", key: str = "") -> tuple[bool | None, str]:
        """Submit server-owned tests and accept only a terminal typed verdict."""
        if not self.configured():
            return None, "code_judge_unavailable"
        try:
            record = self._request("/submissions", {"item_id": payload.get("item_id", "reference"),
                "owner_id": owner, "idempotency_key": key or hashlib.sha256(code.encode()).hexdigest(),
                "code": code, "problem": {"function_name": payload["function_name"], "tests": payload["tests"]}})
            if record.get("status") != "judged" or type(record.get("correct")) is not bool:
                return None, "code_judge_unavailable"
            return record["correct"], "code_judge:" + str(record.get("verdict_kind", "unknown"))
        except (OSError, ValueError, KeyError, TypeError):
            return None, "code_judge_unavailable"


def judge_answer(payload: dict, answer: str, *, owner: str = "offline", key: str = "",
                 cache_dir: Path | None = None) -> tuple[bool | None, str]:
    """Dispatch question checks; None means retryable infrastructure failure."""
    task_type = payload.get("type")
    if task_type == "mcq":
        return check_mcq(payload, answer), "auto"
    if task_type == "short_answer":
        normalized = " ".join(answer.strip().casefold().split())
        return normalized in payload.get("expected_answers", []), "auto"
    if task_type == "code":
        return CodeJudgeClient().judge(payload, answer, owner=owner, key=key)
    if task_type == "extended":
        return RubricJudge(cache_dir).judge(payload, answer, owner=owner)
    return None, "unsupported_question_type"
