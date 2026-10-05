"""Bounded synthetic providers; secrets and live candidate data never enter caches."""

import fcntl
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from matching_engine.rerank_hash import digest

QWEN = "mlx-community/Qwen3.5-4B-MLX-4bit"
LAYA = "aac6fef/laya-multilingual-mlx"
PROMPT_VERSION = "rerank-synthetic-v1"
LIMITS = {"generation": 120, "pair": 3000, "grade": 600}
ENDPOINTS = {"generation": "/api/v1/chat/completions", "pair": "/v1/choice", "grade": "/v1/score"}
SYSTEMONE_URL = "https://ai.sverk.tech/v1/systemone"
SYSTEMONE_MODEL = "kev-latest"
SYSTEMONE_PROMPT_VERSION = "rerank-systemone-v1"
GENERATION_URL = "https://ai.sverk.tech/v1/chat/completions"
GENERATION_MODEL = "qwen3.8-27b"
GENERATION_PROMPT_VERSION = "rerank-sverk-generation-v2"
GEMMA_GENERATION_MODEL = "gemma4-vlm"
GENERATION_MODELS = (GENERATION_MODEL, GEMMA_GENERATION_MODEL)
GENERATION_PROMPT_VERSIONS = {
    GENERATION_MODEL: GENERATION_PROMPT_VERSION,
    GEMMA_GENERATION_MODEL: "rerank-sverk-generation-gemma4-v1",
}
REMOTE_CONCURRENCY = 5


def generation_identity(model: str = GENERATION_MODEL) -> dict[str, str]:
    """Identify an authorized generator and its prompt version; reject other model aliases."""
    if model not in GENERATION_MODELS:
        raise ValueError("unknown synthetic generator model")
    return {"endpoint": GENERATION_URL, "model": model,
            "prompt_version": GENERATION_PROMPT_VERSIONS[model]}


def _atomic_write(path: Path, value: dict) -> None:
    """Publish a complete UTF-8 JSON record; concurrent readers never see partial JSON."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def teacher_identity() -> dict[str, str]:
    """Bind pilot and dataset labels to the explicitly authorized teacher contract."""
    return {"endpoint": SYSTEMONE_URL, "model": SYSTEMONE_MODEL,
            "prompt_version": SYSTEMONE_PROMPT_VERSION}


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
        if path != self._endpoint(kind):
            raise ValueError("endpoint does not match the reserved provider budget")
        identity = self._identity(path, payload, kind)
        key = digest(identity)
        target = self.directory / f"{key}.json"
        # Identical requests share one inference, even across processes.
        with (self.directory / f".{key}.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists():
                stored = json.loads(target.read_text())
                if stored["request_hash"] != key or digest(stored["response"]) != stored["response_hash"]:
                    raise ValueError("provider cache integrity failed")
                return stored["response"]
            attempt = self._reserve(kind)
            start = time.monotonic()
            remote = isinstance(self, SystemOneProviders)
            try:
                request = self._request(path, payload, kind)
                with self._inference_lock(), self._open(request) as response:
                    result = json.load(response)
                if not isinstance(result, dict):
                    raise TypeError("provider returned a non-object response")
            except Exception as exc:
                error = {"request": identity, "request_hash": key,
                         "error_type": type(exc).__name__, "elapsed_seconds": time.monotonic() - start}
                _atomic_write(self.directory / f"{key}.failure-{attempt}.json", error)
                # Remote errors may echo headers or request details; never propagate them.
                if remote:
                    raise RuntimeError(f"SystemOne request failed ({type(exc).__name__})") from None
                raise
            record = {"request": identity, "request_hash": key, "response": result,
                      "response_hash": digest(result), "elapsed_seconds": time.monotonic() - start,
                      "usage": result.get("usage"), "usage_estimated": False}
            _atomic_write(target, record)
            return result

    def _reserve(self, kind: str) -> int:
        """Reserve a cumulative attempt under the short shared ledger lock."""
        with (self.directory / ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger_path = self.directory / "usage.json"
            ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
            if ledger.get(kind, 0) >= LIMITS[kind]:
                raise BudgetExceeded(f"{kind} budget exhausted")
            ledger[kind] = ledger.get(kind, 0) + 1
            _atomic_write(ledger_path, ledger)
            return ledger[kind]

    @contextmanager
    def _inference_lock(self) -> Iterator[None]:
        """Serialize heavy localhost inference independently from remote HTTP."""
        with (self.directory / ".local-inference.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _endpoint(self, kind: str) -> str:
        return ENDPOINTS[kind]

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        return {"path": path, "payload": payload, "prompt_version": PROMPT_VERSION,
                "model": QWEN if kind == "generation" else LAYA}

    def _request(self, path: str, payload: dict, kind: str) -> Request:
        return Request(self.base_url + path, data=json.dumps(payload).encode(),
                       headers={"Content-Type": "application/json"}, method="POST")

    def _open(self, request: Request):
        return urlopen(request, timeout=120)

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
        response = self.request(self._endpoint("generation"), self._generation_payload(instruction, count), "generation")
        return self._parse_generation(response, count)

    def _generation_payload(self, instruction: str, count: int) -> dict:
        """Keep the legacy local payload stable so old Qwen receipts remain replayable."""
        return {
            "model": QWEN, "messages": [{"role": "user", "content": instruction}],
            "max_tokens": 1800, "temperature": 0.7, "enable_thinking": False,
        }

    def _parse_generation(self, response: dict, count: int) -> list[dict]:
        """Reject truncation and validate both local arrays and remote schema objects."""
        choice = response["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("generator response was truncated")
        content = choice["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        profiles = json.loads(content)
        if isinstance(profiles, dict) and set(profiles) == {"profiles"}:
            profiles = profiles["profiles"]
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


class _NoRedirect(HTTPRedirectHandler):
    """Never forward a bearer credential through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SystemOneProviders(LocalProviders):
    """Remote Sverk generation and the authorized SystemOne teacher for synthetic facts.

    The key is read only on a cache miss, never persisted or included in a hash.
    Legacy Laya receipts remain replayable through LocalProviders, not this adapter.
    Five shared disk slots bound concurrent HTTP across processes using this cache.
    """

    def __init__(self, directory: Path, base_url: str = "http://127.0.0.1:12444",
                 *, key_file: Path | None = None, generator_model: str = GENERATION_MODEL) -> None:
        """Select an authorized generator; all models share the experiment ledger and slots."""
        generation_identity(generator_model)
        super().__init__(directory, base_url)
        self.generator_model = generator_model
        self.key_file = key_file or Path(os.environ.get(
            "RERANK_SYSTEMONE_KEY_FILE", str(Path.home() / "Desktop/qwen_token.txt"))).expanduser()

    def _endpoint(self, kind: str) -> str:
        return "/v1/chat/completions" if kind == "generation" else "/v1/systemone"

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        if kind == "generation":
            return {"path": path, "payload": payload, "kind": kind, **generation_identity(self.generator_model)}
        return {"path": path, "payload": payload, "kind": kind, **teacher_identity()}

    def _request(self, path: str, payload: dict, kind: str) -> Request:
        endpoint = GENERATION_URL if kind == "generation" else SYSTEMONE_URL
        return Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                       headers={"Content-Type": "application/json",
                                "Authorization": f"Bearer {self._api_key()}"}, method="POST")

    def _api_key(self) -> str:
        """Read the same unambiguous Sverk credential for generation and teacher calls."""
        text = self.key_file.read_text(encoding="utf-8").strip()
        keys = set(re.findall(r"(?<![\w-])sk-[A-Za-z0-9_-]+", text))
        if re.fullmatch(r"sk-[A-Za-z0-9_-]+", text) is None:
            # Operator files may contain credentials for several providers. Never
            # choose by position: require a Sverk-labelled key or following line.
            selected = set()
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if re.search(r"\bsverk\b", line, flags=re.IGNORECASE):
                    block = line
                    for following in lines[index + 1:]:
                        if not re.fullmatch(
                            r"\s*(?:(?:token|api[_ -]?key|key)\s*[:=]\s*)?sk-[A-Za-z0-9_-]+\s*",
                            following, flags=re.IGNORECASE,
                        ):
                            break
                        block += "\n" + following
                    selected.update(re.findall(r"(?<![\w-])sk-[A-Za-z0-9_-]+", block))
            keys = selected
        if len(keys) != 1:
            raise ValueError("credential file must contain one unambiguous API key")
        return keys.pop()

    def _open(self, request: Request):
        if request.full_url in (SYSTEMONE_URL, GENERATION_URL):
            return build_opener(_NoRedirect()).open(request, timeout=120)
        return super()._open(request)

    @contextmanager
    def _inference_lock(self) -> Iterator[None]:
        """Hold one shared remote slot during HTTP; OS releases slots after a crash."""
        while True:
            for index in range(REMOTE_CONCURRENCY):
                lock = (self.directory / f".remote-slot-{index}.lock").open("a+")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    continue
                try:
                    yield
                finally:
                    lock.close()
                return
            time.sleep(.01)

    def _generation_payload(self, instruction: str, count: int) -> dict:
        """Constrain the remote generator to bounded synthetic facts via JSON Schema."""
        properties = {
            "grade": {"type": "string", "enum": ["intern", "junior", "middle", "senior"]},
            "levels": {"type": "object", "additionalProperties": False,
                       "properties": {skill: {"type": "integer", "minimum": 0, "maximum": 7}
                                      for skill in ("python", "react", "sql", "rest_api", "teamwork")},
                       "required": ["python", "react", "sql", "rest_api", "teamwork"]},
            "status": {"type": "string", "enum": ["confirmed", "partial", "self_reported", "pending"]},
            "duration_months": {"type": "integer", "minimum": 0, "maximum": 120},
            "production_support": {"type": "boolean"},
            "salary_min": {"type": "integer", "minimum": 50000, "maximum": 180000},
            "description": {"type": "string", "minLength": 1, "maxLength": 180},
        }
        schema = {"type": "object", "additionalProperties": False, "required": ["profiles"],
                  "properties": {"profiles": {"type": "array", "minItems": count, "maxItems": count,
                      "items": {"type": "object", "additionalProperties": False,
                                "properties": properties, "required": list(properties)}}}}
        payload = {"model": self.generator_model,
                   "messages": [{"role": "user", "content": instruction.replace(
                       "JSON only, an array.", 'JSON only, an object with a "profiles" array.')}],
                   "max_tokens": 4096, "temperature": 0.7,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "synthetic_traits", "strict": True, "schema": schema}}}
        if self.generator_model == GENERATION_MODEL:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def _answer(self, response: dict, question: str, options: tuple[str, ...]) -> dict:
        if response.get("model") != SYSTEMONE_MODEL or response.get("truncated") is True:
            raise ValueError("teacher model mismatch or truncated state")
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != {question}:
            raise ValueError("teacher returned unexpected answer IDs")
        answer = answers[question]
        if not isinstance(answer, dict):
            raise TypeError("teacher returned a non-object answer")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(options):
            raise ValueError("teacher returned invalid probability keys")
        values = list(probabilities.values())
        if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
            raise ValueError("teacher returned invalid probabilities")
        if abs(sum(values) - 1) > .002:
            raise ValueError("teacher probabilities do not sum to one")
        confidence = answer.get("confidence")
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("teacher returned invalid confidence")
        return answer

    def pair(self, vacancy: dict, a: dict, b: dict, preferences: str) -> str:
        """Compare synthetic raw facts; computed matching scores must remain blind."""
        response = self.request("/v1/systemone", {
            "state": json.dumps({"vacancy": vacancy, "A": a, "B": b, "preferences": preferences},
                                ensure_ascii=False, sort_keys=True),
            "model": SYSTEMONE_MODEL,
            "questions": {"preference": {"type": "choice",
                "instructions": "Which candidate better fits these employer preferences? Consider stated facts and uncertainty.",
                "criteria": {"A": "A preferable to B", "B": "B preferable to A",
                             "tie": "Comparable overall suitability",
                             "insufficient": "Insufficient facts to compare"}}},
        }, "pair")
        answer = self._answer(response, "preference", ("A", "B", "tie", "insufficient"))
        label = answer.get("choice")
        probabilities = answer["probabilities"]
        if label not in probabilities or probabilities[label] != max(probabilities.values()):
            raise ValueError("teacher choice contradicts its distribution")
        if list(probabilities.values()).count(max(probabilities.values())) != 1:
            return "tie"
        return label

    def grade(self, vacancy: dict, candidate: dict, preferences: str) -> int | None:
        """Assess suitability independently; uncertain distributions remain unavailable."""
        response = self.request("/v1/systemone", {
            "state": json.dumps({"vacancy": vacancy, "candidate": candidate, "preferences": preferences},
                                ensure_ascii=False, sort_keys=True),
            "model": SYSTEMONE_MODEL,
            "questions": {"suitability": {"type": "score",
                "instructions": "Assess overall suitability for these employer preferences using available facts.",
                "criteria": ["Unsuitable", "Weak fit", "Suitable", "Strong fit"]}},
        }, "grade")
        answer = self._answer(response, "suitability", ("0", "1", "2", "3"))
        score = answer.get("score")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 3:
            raise ValueError("teacher returned invalid suitability score")
        values = [answer["probabilities"][str(i)] for i in range(4)]
        if values.count(max(values)) != 1 or answer["confidence"] < .2:
            return None
        return values.index(max(values))


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
