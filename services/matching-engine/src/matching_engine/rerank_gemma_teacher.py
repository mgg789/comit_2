"""Pair-only Gemma pilot adapter; evidence must quote exact synthetic source facts."""

import json
import math
from pathlib import Path
from urllib.request import Request

from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_URL,
    SYSTEMONE_PAIR_PROMPT_VERSION,
    SystemOneProviders,
)

GEMMA_TEACHER_URL = GENERATION_URL
GEMMA_TEACHER_MODEL = GEMMA_GENERATION_MODEL
GEMMA_TEACHER_PROMPT_VERSION = "rerank-gemma-pair-v1"
EVIDENCE_FIELDS = (
    "grade", "status", "salary_min", "duration_months", "production_support", "structured_facts_available",
    "levels.python", "levels.react", "levels.sql", "levels.rest_api", "levels.teamwork",
)
LABELS = ("A", "B", "tie", "insufficient")


def gemma_teacher_identity() -> dict[str, str]:
    """Identify this opt-in pair teacher without changing the active SystemOne cohort."""
    return {"endpoint": GEMMA_TEACHER_URL, "model": GEMMA_TEACHER_MODEL,
            "prompt_version": GEMMA_TEACHER_PROMPT_VERSION}


class GemmaTeacherProviders(SystemOneProviders):
    """Judge synthetic pairs with the existing shared pair ledger, slots, and lazy key reader.

    Generation and grade requests are rejected before a budget is reserved.
    Raw chat responses stay immutable in cache; derived judgements quote exact
    primitive facts and never expose a calibrated-confidence claim.
    """

    def __init__(self, directory: Path, *, key_file: Path | None = None) -> None:
        """Reuse experiment-local receipts and optionally select an existing credential file."""
        super().__init__(directory, key_file=key_file, generator_model=GEMMA_GENERATION_MODEL,
                         pair_prompt_version=SYSTEMONE_PAIR_PROMPT_VERSION)

    def _endpoint(self, kind: str) -> str:
        if kind != "pair":
            raise ValueError("Gemma teacher supports only pair requests")
        return "/v1/chat/completions"

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        self._require_pair(path, payload, kind)
        return {"path": path, "payload": payload, "kind": kind, **gemma_teacher_identity()}

    def _require_pair(self, path: str, payload: dict, kind: str) -> None:
        if path != self._endpoint(kind) or payload.get("model") != GEMMA_TEACHER_MODEL:
            raise ValueError("Gemma teacher endpoint or model mismatch")

    def _request(self, path: str, payload: dict, kind: str) -> Request:
        self._require_pair(path, payload, kind)
        return Request(GEMMA_TEACHER_URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                       headers={"Content-Type": "application/json",
                                "Authorization": f"Bearer {self._api_key()}"}, method="POST")

    def request(self, path: str, payload: dict, kind: str) -> dict:
        """Reuse the bounded transport, then verify the complete immutable request/response binding."""
        self._require_pair(path, payload, kind)
        response = super().request(path, payload, kind)
        key = digest(self._identity(path, payload, kind))
        record = json.loads((self.directory / f"{key}.json").read_text(encoding="utf-8"))
        if (record["request_hash"] != key or digest(record["request"]) != key
                or digest(record["response"]) != record["response_hash"]
                or digest(response) != record["response_hash"]):
            raise ValueError("Gemma teacher cache integrity failed")
        return response

    def _pair_payload(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Build score-blind schema-constrained input without keys, cache access, or HTTP."""
        instructions = super()._pair_payload(vacancy, a, b, preferences)["questions"]["preference"]["instructions"]
        instructions += (
            " Return only JSON with label, a concise rationale, evidence, and uncertainties. "
            "Every evidence item must quote an exact primitive value from its candidate's named field. "
            "Evidence candidate must be A or B; do not invent facts or cite computed scores. "
            "A decisive A/B judgement requires evidence from both candidates. "
            "Unknown facts belong in uncertainties. Do not report confidence or probabilities."
        )
        evidence = {"type": "object", "additionalProperties": False,
                    "required": ["candidate", "field", "value", "reason"], "properties": {
                        "candidate": {"type": "string", "enum": ["A", "B"]},
                        "field": {"type": "string", "enum": list(EVIDENCE_FIELDS)},
                        "value": {"type": ["string", "number", "boolean", "null"]},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 180}}}
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["label", "rationale", "evidence", "uncertainties"], "properties": {
                      "label": {"type": "string", "enum": list(LABELS)},
                      "rationale": {"type": "string", "minLength": 1, "maxLength": 360},
                      "evidence": {"type": "array", "maxItems": 8, "items": evidence},
                      "uncertainties": {"type": "array", "maxItems": 8,
                                        "items": {"type": "string", "minLength": 1, "maxLength": 180}}}}
        return {"model": GEMMA_TEACHER_MODEL, "temperature": 0, "max_tokens": 1400,
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(
                    {"vacancy": vacancy, "preferences": preferences, "A": a, "B": b}, ensure_ascii=False)}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "synthetic_pair_judgement", "strict": True, "schema": schema}}}

    def judge(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Return a bounded judgement after validating every evidence value against source facts."""
        response = self.request("/v1/chat/completions", self._pair_payload(vacancy, a, b, preferences), "pair")
        if "model" in response and response["model"] != GEMMA_TEACHER_MODEL:
            raise ValueError("Gemma teacher model mismatch")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("Gemma teacher returned invalid choices")
        choice = choices[0]
        if response.get("truncated") is True or choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Gemma teacher response was truncated or unfinished")
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise TypeError("Gemma teacher returned invalid message content")
        judgement = json.loads(message["content"])
        if not isinstance(judgement, dict) or set(judgement) != {"label", "rationale", "evidence", "uncertainties"}:
            raise ValueError("Gemma teacher returned unexpected judgement fields")
        if judgement["label"] not in LABELS:
            raise ValueError("Gemma teacher returned invalid pair label")
        _bounded_text(judgement["rationale"], 360)
        evidence, uncertainties = judgement["evidence"], judgement["uncertainties"]
        if not isinstance(evidence, list) or len(evidence) > 8:
            raise ValueError("Gemma teacher returned invalid evidence")
        if not isinstance(uncertainties, list) or len(uncertainties) > 8:
            raise ValueError("Gemma teacher returned invalid uncertainties")
        for uncertainty in uncertainties:
            _bounded_text(uncertainty, 180)
        candidates = {"A": a, "B": b}
        covered = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"candidate", "field", "value", "reason"}:
                raise ValueError("Gemma teacher returned unexpected evidence fields")
            candidate, field = item["candidate"], item["field"]
            if type(candidate) is not str or candidate not in candidates or field not in EVIDENCE_FIELDS:
                raise ValueError("Gemma teacher cited an unapproved candidate or field")
            _bounded_text(item["reason"], 180)
            value = candidates[candidate]
            for key in field.split("."):
                if not isinstance(value, dict) or key not in value:
                    raise ValueError("Gemma teacher cited a missing source fact")
                value = value[key]
            if (type(value) not in (str, int, float, bool, type(None)) or type(item["value"]) is not type(value)
                    or item["value"] != value or (type(value) is float and not math.isfinite(value))):
                raise ValueError("Gemma teacher evidence differs from the exact source fact")
            covered.add(candidate)
        if judgement["label"] in ("A", "B") and covered != {"A", "B"}:
            raise ValueError("decisive Gemma judgement requires evidence from both candidates")
        return judgement

    def pair(self, vacancy: dict, a: dict, b: dict, preferences: str) -> str:
        """Return the validated Gemma label while retaining full rationale/evidence in cache."""
        return self.judge(vacancy, a, b, preferences)["label"]


def _bounded_text(value: object, maximum: int) -> None:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("Gemma teacher returned invalid bounded text")
