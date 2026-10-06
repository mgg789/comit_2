"""Opt-in pair pilot whose explanations project exact facts instead of generated prose."""

import json
from copy import deepcopy
from pathlib import Path

from matching_engine.rerank_gemma_format import source_citations
from matching_engine.rerank_gemma_teacher import GEMMA_TEACHER_URL, LABELS
from matching_engine.rerank_pair_teacher import (
    COMPARATIVE_MODELS,
    SALARY_CHOICES,
    CompatibleTeacherProviders,
    salary_comparison,
)
from matching_engine.rerank_providers import GENERATION_MODEL, SystemOneProviders

GROUNDED_PROMPT_VERSION = "rerank-pair-grounded-v5"
REASON_CODES = (
    "prefer_known_facts", "comparable_fit", "missing_tradeoff_priorities", "insufficient_source_facts",
)
SOURCE_STATUSES = ("confirmed", "partial", "self_reported", "pending")
ANSWER_FIELDS = (
    "label", "evidence_A", "evidence_B", "unknown_A", "unknown_B", "salary_comparison_choice", "reason_code",
)


def grounded_teacher_identity(model: str = GENERATION_MODEL) -> dict[str, str]:
    """Identify either approved grounded pilot model without changing v1-v4 defaults."""
    if model not in COMPARATIVE_MODELS:
        raise ValueError("unknown grounded pair teacher model")
    return {"endpoint": GEMMA_TEACHER_URL, "model": model, "prompt_version": GROUNDED_PROMPT_VERSION}


def _sources(a: dict, b: dict) -> tuple[dict, dict, dict, dict]:
    citations = source_citations(a, b)
    sides, unknown, statuses = {}, {}, {}
    for side, candidate in (("A", a), ("B", b)):
        status = candidate.get("status")
        if type(status) is not str or status not in SOURCE_STATUSES:
            raise ValueError("grounded teacher requires an original evidence status for both candidates")
        sides[side] = [token for token, fact in citations.items() if fact["candidate"] == side]
        statuses[side] = next(token for token in sides[side] if citations[token]["field"] == "status")
        if len(sides[side]) < 2:
            raise ValueError("grounded teacher requires status and at least one other source fact")
        unknown[side] = [token for token in sides[side] if citations[token]["value"] is None]
    return citations, sides, unknown, statuses


def _validate_answer(answer: object, a: dict, b: dict) -> dict:
    if not isinstance(answer, dict) or set(answer) != set(ANSWER_FIELDS):
        raise ValueError("grounded teacher returned unexpected fields")
    label, reason = answer["label"], answer["reason_code"]
    if type(label) is not str or label not in LABELS or type(reason) is not str or reason not in REASON_CODES:
        raise ValueError("grounded teacher label or reason code invalid")
    reasons = {"A": ("prefer_known_facts",), "B": ("prefer_known_facts",), "tie": ("comparable_fit",),
               "insufficient": ("missing_tradeoff_priorities", "insufficient_source_facts")}
    if reason not in reasons[label]:
        raise ValueError("grounded teacher reason contradicts label")
    choice = answer["salary_comparison_choice"]
    if (type(choice) is not str or choice not in SALARY_CHOICES
            or choice != salary_comparison(a, b)["cheaper_side"]):
        raise ValueError("grounded teacher salary direction differs from fixed facts")
    _citations, sides, unknown, statuses = _sources(a, b)
    for side in ("A", "B"):
        evidence = answer[f"evidence_{side}"]
        missing = answer[f"unknown_{side}"]
        if type(evidence) is not list or not 2 <= len(evidence) <= 4:
            raise ValueError("grounded teacher evidence count invalid")
        if type(missing) is not list or len(missing) > 3:
            raise ValueError("grounded teacher unknown count invalid")
        for refs, allowed in ((evidence, sides[side]), (missing, unknown[side])):
            if any(type(ref) is not str or ref not in allowed for ref in refs):
                raise ValueError("grounded teacher citation differs from exact source side or null fact")
            if len(set(refs)) != len(refs):
                raise ValueError("grounded teacher citation duplicated")
        if statuses[side] not in evidence:
            raise ValueError("grounded teacher must retain both original evidence statuses")
    return answer


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("grounded teacher returned duplicate JSON fields")
        result[key] = value
    return result


def grounded_explanation(vacancy: dict, a: dict, b: dict, answer: dict) -> dict:
    """Validate and project exact cited facts and source statuses without inference or HTTP.

    The copied status is the candidate's original evidence state, not a claim
    that individual skill levels are confirmed. Vacancy context is copied intact;
    no priorities, confidence, computed scores, or generated prose are added.
    """
    _validate_answer(answer, a, b)
    citations = source_citations(a, b)
    result = {"vacancy": deepcopy(vacancy), "label": answer["label"], "reason_code": answer["reason_code"],
              "salary_comparison_choice": answer["salary_comparison_choice"],
              "salary_comparison": salary_comparison(a, b)}
    for side, candidate in (("A", a), ("B", b)):
        for prefix in ("evidence", "unknown"):
            field = f"{prefix}_{side}"
            result[field] = [{**citations[token], "status": candidate["status"]} for token in answer[field]]
    return result


class GroundedTeacherProviders(CompatibleTeacherProviders):
    """Judge synthetic pairs with exact references and fixed codes, using shared pair receipts.

    Both models use one schema and domain instructions. Qwen alone disables
    thinking. Existing pair-only HTTP, budgets, cache integrity, and five remote
    slots are inherited; no default teacher is replaced.
    """

    def __init__(self, directory: Path, *, model: str = GENERATION_MODEL,
                 key_file: Path | None = None) -> None:
        """Choose a grounded pilot model with lazy credentials and an experiment-local cache."""
        grounded_teacher_identity(model)
        super().__init__(directory, model=model, key_file=key_file)

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        self._require_pair(path, payload, kind)
        return {"path": path, "payload": payload, "kind": kind, **grounded_teacher_identity(self.model)}

    def _pair_payload(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Build one portable schema without free prose or unsupported uniqueItems/empty enums."""
        _citations, sides, unknown, _statuses = _sources(a, b)
        instructions = SystemOneProviders._pair_payload(self, vacancy, a, b, preferences)[
            "questions"]["preference"]["instructions"]
        instructions += (
            " Return JSON only with label, evidence_A, evidence_B, unknown_A, unknown_B, "
            "salary_comparison_choice, and reason_code. Never return free prose, rationale, confidence, or scores. "
            "Each evidence array must contain 2 to 4 unique exact strings copied from its own available_citations. "
            "Always include the candidate's original status citation and at least one other fact. "
            "Retain confirmed, partial, self_reported, or pending exactly; do not declare a level confirmed by inference. "
            "Each unknown array contains 0 to 3 unique exact strings copied from its own available_unknowns: "
            "only explicitly null facts qualify, never non-null or absent facts. Never edit or coerce a reference. "
            "salary_comparison_choice must copy salary_comparison.cheaper_side; these arithmetic facts do not "
            "determine label or invent a salary preference. A/B labels require reason_code prefer_known_facts; "
            "tie requires comparable_fit; insufficient requires missing_tradeoff_priorities or insufficient_source_facts. "
            "Do not invent technical minima, numeric weights, or priorities. For a class-only vacancy with opposing "
            "technical, salary, or teamwork advantages and missing tradeoff priorities, insufficient is legitimate. "
            "Formal test grade is never overwritten by a role or biography. Both candidates need evidence for every label."
        )
        fields = {"label": {"type": "string", "enum": list(LABELS)},
                  "salary_comparison_choice": {"type": "string", "enum": list(SALARY_CHOICES)},
                  "reason_code": {"type": "string", "enum": list(REASON_CODES)}}
        for side in ("A", "B"):
            fields[f"evidence_{side}"] = {"type": "array", "minItems": 2, "maxItems": 4,
                                          "items": {"type": "string", "enum": sides[side]}}
            items = {"type": "string"}
            if unknown[side]:
                items["enum"] = unknown[side]
            fields[f"unknown_{side}"] = {"type": "array", "maxItems": 3 if unknown[side] else 0, "items": items}
        schema = {"type": "object", "additionalProperties": False,
                  "required": list(ANSWER_FIELDS), "properties": fields}
        payload = {"model": self.model, "temperature": 0, "max_tokens": 700,
                   "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(
                       {"vacancy": vacancy, "preferences": preferences, "A": a, "B": b,
                        "salary_comparison": salary_comparison(a, b), "available_citations": sides,
                        "available_unknowns": unknown}, ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "synthetic_pair_grounded_v5", "strict": True, "schema": schema}}}
        if self.model == GENERATION_MODEL:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def judge(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Return strict fixed fields after model, completion, typed-reference, and reason validation."""
        response = self.request("/v1/chat/completions", self._pair_payload(vacancy, a, b, preferences), "pair")
        if not isinstance(response, dict) or response.get("model") != self.model or response.get("truncated") is True:
            raise ValueError("grounded teacher model mismatch or truncation")
        choices = response.get("choices")
        if (type(choices) is not list or len(choices) != 1 or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"):
            raise ValueError("grounded teacher response unfinished")
        message = choices[0].get("message")
        if not isinstance(message, dict) or type(message.get("content")) is not str:
            raise ValueError("grounded teacher content missing")
        answer = json.loads(message["content"], object_pairs_hook=_unique_object)
        return _validate_answer(answer, a, b)
