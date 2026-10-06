"""Opt-in comparative pair teachers with exact source citations and fixed salary facts."""

import json
import re
from pathlib import Path

from matching_engine.rerank_gemma_format import source_citations
from matching_engine.rerank_gemma_teacher import (
    GEMMA_TEACHER_URL,
    LABELS,
    GemmaTeacherProviders,
    _bounded_text,
)
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_MODEL,
    SystemOneProviders,
)

COMPARATIVE_MODELS = (GEMMA_GENERATION_MODEL, GENERATION_MODEL)
COMPARATIVE_PROMPT_VERSION = "rerank-pair-comparative-v3"
SALARY_CHOICES = ("A", "B", "equal", "unknown")
_SALARY_TERMS = re.compile(
    r"\b(?:salary|salaries|pay|paid|compensation|cost\w*|budget\w*|cheap\w*|econom\w*|"
    r"зарплат\w*|зп|дешев\w*|дешёв\w*|бюджет\w*|эконом\w*|стоим\w*)\b", re.IGNORECASE,
)


def comparative_teacher_identity(model: str = GEMMA_GENERATION_MODEL) -> dict[str, str]:
    """Identify an approved comparative model while retaining all prior teacher versions."""
    if model not in COMPARATIVE_MODELS:
        raise ValueError("unknown comparative pair teacher model")
    return {"endpoint": GEMMA_TEACHER_URL, "model": model, "prompt_version": COMPARATIVE_PROMPT_VERSION}


def salary_comparison(a: dict, b: dict) -> dict:
    """Compare exact integer salary_min facts; missing/non-integer values remain unknown.

    Amounts retain their original side. Delta is B minus A, so reversing the
    candidates negates delta and swaps the cheaper side without changing facts.
    """
    amount_a = a.get("salary_min") if type(a.get("salary_min")) is int else None
    amount_b = b.get("salary_min") if type(b.get("salary_min")) is int else None
    delta = None if amount_a is None or amount_b is None else amount_b - amount_a
    cheaper = "unknown" if delta is None else ("equal" if delta == 0 else ("A" if delta > 0 else "B"))
    return {"salary_min_A": amount_a, "salary_min_B": amount_b,
            "cheaper_side": cheaper, "delta_B_minus_A": delta}


class ComparativeTeacherProviders(GemmaTeacherProviders):
    """Use one prompt/schema for two pair-only pilot models with the shared ledger and slots.

    Exact typed citations are mandatory on both sides, including tie/insufficient.
    Salary direction is checked independently from free prose; a literal economy
    vocabulary guard prevents salary rationales when preferences do not mention it.
    These checks do not certify the semantic quality of the rationale.
    """

    def __init__(self, directory: Path, *, model: str = GEMMA_GENERATION_MODEL,
                 key_file: Path | None = None) -> None:
        """Choose an approved model without changing existing Gemma/SystemOne defaults."""
        comparative_teacher_identity(model)
        super().__init__(directory, key_file=key_file)
        self.model = model

    def _require_pair(self, path: str, payload: dict, kind: str) -> None:
        if path != self._endpoint(kind) or payload.get("model") != self.model:
            raise ValueError("comparative pair teacher endpoint or model mismatch")

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        self._require_pair(path, payload, kind)
        return {"path": path, "payload": payload, "kind": kind, **comparative_teacher_identity(self.model)}

    def _pair_payload(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Build identical score-blind model inputs except for model and Qwen's thinking setting."""
        citations = source_citations(a, b)
        side_citations = {side: [token for token, fact in citations.items() if fact["candidate"] == side]
                          for side in ("A", "B")}
        if any(not tokens for tokens in side_citations.values()):
            raise ValueError("both candidates require exact source citations")
        instructions = SystemOneProviders._pair_payload(self, vacancy, a, b, preferences)[
            "questions"]["preference"]["instructions"]
        instructions += (
            " Return JSON only with label, rationale, evidence_A, evidence_B, uncertainties, and "
            "salary_comparison_choice. Rationale is one short sentence of at most 240 characters. "
            "Each evidence_A/evidence_B is a nonempty list of at most three unique exact strings copied from "
            "that side's available_citations; never reconstruct, edit, or coerce a citation. "
            "Cite both candidates even for tie or insufficient. Use at most three short uncertainties. "
            "salary_comparison contains fixed arithmetic facts, not a score, ranking weight, or qualification rule. "
            "salary_comparison_choice must exactly copy salary_comparison.cheaper_side; it does not determine label. "
            "Rationale may cite salary economy only if the employer explicitly mentions it in preferences. "
            "For a class-only vacancy without explicit numeric weights for technical fit versus salary or teamwork, "
            "do not invent an acceptable technical minimum, priority, or weight. When there is no justified dominance "
            "and tradeoff priorities are missing, insufficient is legitimate. Tie requires supported comparable fit. "
            "Formal grade is not overwritten by a role or free biography. Do not report confidence or probabilities."
        )
        fields = {"label": {"type": "string", "enum": list(LABELS)},
                  "rationale": {"type": "string", "minLength": 1, "maxLength": 240},
                  "uncertainties": {"type": "array", "maxItems": 3,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 180}},
                  "salary_comparison_choice": {"type": "string", "enum": list(SALARY_CHOICES)}}
        for side in ("A", "B"):
            fields[f"evidence_{side}"] = {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                                          "items": {"type": "string", "enum": side_citations[side]}}
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["label", "rationale", "evidence_A", "evidence_B", "uncertainties", "salary_comparison_choice"],
                  "properties": fields}
        payload = {"model": self.model, "temperature": 0, "max_tokens": 1400,
                   "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(
                       {"vacancy": vacancy, "preferences": preferences, "A": a, "B": b,
                        "salary_comparison": salary_comparison(a, b), "available_citations": side_citations},
                       ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "synthetic_pair_comparative_v3", "strict": True, "schema": schema}}}
        if self.model == GENERATION_MODEL:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def judge(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Return an exact-reference judgement without coercion, confidence, or salary-code repair."""
        response = self.request("/v1/chat/completions", self._pair_payload(vacancy, a, b, preferences), "pair")
        if response.get("model") != self.model or response.get("truncated") is True:
            raise ValueError("comparative teacher model mismatch or truncation")
        choices = response.get("choices")
        if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"):
            raise ValueError("comparative teacher response unfinished")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise TypeError("comparative teacher content missing")
        answer = json.loads(content)
        expected = {"label", "rationale", "evidence_A", "evidence_B", "uncertainties", "salary_comparison_choice"}
        if not isinstance(answer, dict) or set(answer) != expected or answer["label"] not in LABELS:
            raise ValueError("comparative teacher fields or label invalid")
        _bounded_text(answer["rationale"], 240)
        if _SALARY_TERMS.search(answer["rationale"]) and not _SALARY_TERMS.search(preferences):
            raise ValueError("salary economy rationale requires explicit employer preferences")
        if answer["salary_comparison_choice"] != salary_comparison(a, b)["cheaper_side"]:
            raise ValueError("comparative teacher salary direction differs from fixed facts")
        citations = source_citations(a, b)
        for side in ("A", "B"):
            refs = answer[f"evidence_{side}"]
            if not isinstance(refs, list) or not 1 <= len(refs) <= 3:
                raise ValueError("comparative teacher requires nonempty bounded evidence for both candidates")
            if any(type(ref) is not str or ref not in citations or citations[ref]["candidate"] != side for ref in refs):
                raise ValueError("comparative teacher citation differs from its exact source side")
            if len(set(refs)) != len(refs):
                raise ValueError("comparative teacher citation duplicated")
        unknown = answer["uncertainties"]
        if not isinstance(unknown, list) or len(unknown) > 3:
            raise ValueError("comparative teacher uncertainties invalid")
        for uncertainty in unknown:
            _bounded_text(uncertainty, 180)
        return answer
