"""Bounded, replayable local synthetic generation; never used by live ranking."""

import fcntl
import json
import math
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from matching_engine.rerank_hash import digest

QWEN = "mlx-community/Qwen3.5-4B-MLX-4bit"
LAYA = "aac6fef/laya-multilingual-mlx"
PROMPT_VERSION = "rerank-synthetic-v1"
LIMITS = {"generation": 120, "pair": 3000, "grade": 600}
ENDPOINTS = {"generation": "/api/v1/chat/completions", "pair": "/v1/choice", "grade": "/v1/score"}


class BudgetExceeded(RuntimeError):
    """Hard experiment ceiling; restarting the command does not reset usage."""


class LocalProviders:
    """Sequential local HTTP calls with disk cache and crash-safe budget reservation.

    Only synthetic data belongs here. Cache contains raw requests/responses;
    callers must not pass private candidate records. No automatic retries.
    """

    def __init__(self, directory: Path, base_url: str = "http://127.0.0.1:12444") -> None:
        """Require loopback HTTP and create an experiment-local immutable cache."""
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("synthetic providers must be loopback HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider URL must not contain credentials or parameters")
        self.directory, self.base_url = directory, base_url.rstrip("/")
        directory.mkdir(parents=True, exist_ok=True)

    def request(self, path: str, payload: dict, kind: str) -> dict:
        """Reserve one attempt before HTTP; cache successful calls for exact replay."""
        if kind not in LIMITS:
            raise ValueError("unknown provider budget")
        if path != ENDPOINTS[kind]:
            raise ValueError("endpoint does not match the reserved provider budget")
        identity = {"path": path, "payload": payload, "prompt_version": PROMPT_VERSION,
                    "model": QWEN if kind == "generation" else LAYA}
        key = digest(identity)
        target = self.directory / f"{key}.json"
        # Lock covers inference too: multiple CLI processes cannot run heavy jobs concurrently.
        with (self.directory / ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists():
                stored = json.loads(target.read_text())
                if stored["request_hash"] != key or digest(stored["response"]) != stored["response_hash"]:
                    raise ValueError("provider cache integrity failed")
                return stored["response"]
            ledger_path = self.directory / "usage.json"
            ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
            if ledger.get(kind, 0) >= LIMITS[kind]:
                raise BudgetExceeded(f"{kind} budget exhausted")
            ledger[kind] = ledger.get(kind, 0) + 1
            temporary = ledger_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(ledger), encoding="utf-8")
            temporary.replace(ledger_path)
            start = time.monotonic()
            request = Request(self.base_url + path, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=120) as response:
                    result = json.load(response)
            except Exception as exc:
                error = {"request": identity, "request_hash": key,
                         "error_type": type(exc).__name__, "elapsed_seconds": time.monotonic() - start}
                (self.directory / f"{key}.failure-{ledger[kind]}.json").write_text(
                    json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
                raise
            record = {"request": identity, "request_hash": key, "response": result,
                      "response_hash": digest(result), "elapsed_seconds": time.monotonic() - start,
                      "usage": result.get("usage"), "usage_estimated": False}
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return result

    def generate(self, direction: str, scenario: str, count: int = 5) -> list[dict]:
        """Generate at most five bounded traits; deterministic validators reject malformed facts."""
        if not 1 <= count <= 5:
            raise ValueError("generation batch must contain one to five profiles")
        instruction = (
            f"Create {count} diverse synthetic IT candidate traits for {direction}. Scenario: {scenario}. "
            "JSON only, an array. Each object must have grade (intern/junior/middle/senior), "
            "levels (object mapping python,react,sql,rest_api,teamwork to integers 0..7), "
            "status (confirmed/partial/self_reported/pending), duration_months (integer 0..120), "
            "production_support (boolean), salary_min (integer 50000..180000), "
            "description (Russian string <=180 characters). No names, contacts, scores, labels. "
            "Vary tradeoffs, do not make one candidate dominate all fields."
        )
        response = self.request("/api/v1/chat/completions", {
            "model": QWEN, "messages": [{"role": "user", "content": instruction}],
            "max_tokens": 1800, "temperature": 0.7, "enable_thinking": False,
        }, "generation")
        content = response["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        profiles = json.loads(content)
        if not isinstance(profiles, list) or len(profiles) != count:
            raise ValueError("generator returned wrong profile count")
        for profile in profiles:
            validate_traits(profile)
        return profiles

    def pair(self, vacancy: dict, a: dict, b: dict, preferences: str) -> str:
        """Label one synthetic comparison without exposing computed matching scores."""
        response = self.request("/v1/choice", {
            "state": {"vacancy": vacancy, "A": a, "B": b, "preferences": preferences},
            "question": "Which candidate better fits these employer preferences? Consider stated facts and uncertainty.",
            "options": ["A", "B", "tie", "insufficient"],
            "criteria": {"A": "A preferable to B", "B": "B preferable to A",
                         "tie": "Comparable overall suitability", "insufficient": "Insufficient facts to compare"},
        }, "pair")
        label = response.get("choice")
        if label not in ("A", "B", "tie", "insufficient"):
            raise ValueError("teacher returned invalid pair label")
        return label

    def grade(self, vacancy: dict, candidate: dict, preferences: str) -> int | None:
        """Independent suitability assessment, never derived from pair victories."""
        response = self.request("/v1/score", {
            "state": {"vacancy": vacancy, "candidate": candidate, "preferences": preferences},
            "question": "Assess overall suitability for these employer preferences using available facts.",
            "criteria": ["Unsuitable", "Weak fit", "Suitable", "Strong fit"],
        }, "grade")
        # Laya score is an expected ordinal float, not a discrete class label.
        probabilities = response.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != {"0", "1", "2", "3"}:
            raise ValueError("teacher returned invalid suitability distribution")
        values = [probabilities[str(index)] for index in range(4)]
        if any(type(p) not in (float, int) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
            raise ValueError("teacher returned invalid suitability probabilities")
        if abs(sum(values) - 1) > .002:
            raise ValueError("teacher suitability probabilities do not sum to one")
        maximum = max(values)
        if values.count(maximum) != 1 or response.get("confidence", 0) < .2:
            return None
        return values.index(maximum)


def validate_traits(value: dict) -> None:
    """Reject out-of-contract generator output before it enters a training dataset."""
    if set(value) != {"grade", "levels", "status", "duration_months", "production_support", "salary_min", "description"}:
        raise ValueError("unexpected synthetic trait fields")
    if value["grade"] not in ("intern", "junior", "middle", "senior"):
        raise ValueError("invalid grade")
    if value["status"] not in ("confirmed", "partial", "self_reported", "pending"):
        raise ValueError("invalid evidence status")
    levels = value["levels"]
    if not isinstance(levels, dict) or set(levels) != {"python", "react", "sql", "rest_api", "teamwork"}:
        raise ValueError("invalid synthetic coordinates")
    for number in levels.values():
        if type(number) is not int or not 0 <= number <= 7:
            raise ValueError("invalid level")
    for key, maximum, minimum in (("duration_months", 120, 0), ("salary_min", 180000, 50000)):
        if type(value[key]) is not int or not minimum <= value[key] <= maximum:
            raise ValueError(f"invalid {key}")
    if type(value["production_support"]) is not bool:
        raise ValueError("invalid production flag")
    if not isinstance(value["description"], str) or not 1 <= len(value["description"]) <= 180:
        raise ValueError("invalid description")
