"""Two-call format probe with homogeneous exact source citations; never cohort labels."""

import argparse
import fcntl
import json
import math
import time
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _cache, _read, _seal
from matching_engine.rerank_gemma_pilot import _verified
from matching_engine.rerank_gemma_teacher import (
    EVIDENCE_FIELDS,
    GEMMA_TEACHER_MODEL,
    GEMMA_TEACHER_URL,
    LABELS,
    GemmaTeacherProviders,
    _bounded_text,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import LIMITS, SystemOneProviders

FORMAT_PROMPT_VERSION = "rerank-gemma-pair-v2-format"


def source_citations(a: dict, b: dict) -> dict[str, dict]:
    """Map homogeneous citation strings to exact typed primitives from two synthetic profiles."""
    result = {}
    kinds = {str: "str", int: "int", float: "float", bool: "bool", type(None): "null"}
    for side, candidate in (("A", a), ("B", b)):
        for field in EVIDENCE_FIELDS:
            value = candidate
            for key in field.split("."):
                if not isinstance(value, dict) or key not in value:
                    break
                value = value[key]
            else:
                if type(value) not in kinds or (type(value) is float and not math.isfinite(value)):
                    raise ValueError("unsupported source citation primitive")
                encoded = value if type(value) is str else json.dumps(value, allow_nan=False)
                token = f"{side}.{field}[{kinds[type(value)]}]={encoded}"
                result[token] = {"candidate": side, "field": field, "value": value}
    if not result:
        raise ValueError("source facts required for format probe")
    return result


class SimpleGemmaTeacherProviders(GemmaTeacherProviders):
    """Opt-in pair-only format version; original Gemma/SystemOne identities remain unchanged."""

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        self._require_pair(path, payload, kind)
        return {"path": path, "payload": payload, "kind": kind, "endpoint": GEMMA_TEACHER_URL,
                "model": GEMMA_TEACHER_MODEL, "prompt_version": FORMAT_PROMPT_VERSION}

    def _pair_payload(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Use a simple schema of strings/string arrays; references retain original fact types."""
        citations = source_citations(a, b)
        instructions = SystemOneProviders._pair_payload(self, vacancy, a, b, preferences)[
            "questions"]["preference"]["instructions"]
        instructions += (
            " Return JSON only: label, rationale, evidence, uncertainties. Rationale is one short sentence "
            "of at most 160 characters. Evidence is 2 to 4 exact strings copied from available_citations; "
            "never reconstruct or edit a citation. A/B labels require at least one citation per candidate. "
            "Use at most two short uncertainties. Tie/insufficient are legitimate answers. "
            "Do not report confidence or probabilities."
        )
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["label", "rationale", "evidence", "uncertainties"], "properties": {
                      "label": {"type": "string", "enum": list(LABELS)},
                      "rationale": {"type": "string", "maxLength": 240},
                      "evidence": {"type": "array", "maxItems": 4,
                                   "items": {"type": "string", "enum": list(citations)}},
                      "uncertainties": {"type": "array", "maxItems": 2,
                                        "items": {"type": "string", "maxLength": 180}}}}
        return {"model": GEMMA_TEACHER_MODEL, "temperature": 0, "max_tokens": 1400,
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(
                    {"vacancy": vacancy, "preferences": preferences, "A": a, "B": b,
                     "available_citations": list(citations)}, ensure_ascii=False)}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "synthetic_pair_format_v2", "strict": True, "schema": schema}}}

    def judge(self, vacancy: dict, a: dict, b: dict, preferences: str) -> dict:
        """Validate a new raw reply against exact references; do not coerce or repair source facts."""
        response = self.request("/v1/chat/completions", self._pair_payload(vacancy, a, b, preferences), "pair")
        if response.get("model") != GEMMA_TEACHER_MODEL or response.get("truncated") is True:
            raise ValueError("format probe model mismatch or truncation")
        choices = response.get("choices")
        if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"):
            raise ValueError("format probe response unfinished")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise TypeError("format probe content missing")
        answer = json.loads(content)
        if (not isinstance(answer, dict) or set(answer) != {"label", "rationale", "evidence", "uncertainties"}
                or answer["label"] not in LABELS):
            raise ValueError("format probe fields or label invalid")
        _bounded_text(answer["rationale"], 240)
        evidence, unknown = answer["evidence"], answer["uncertainties"]
        if (not isinstance(evidence, list) or not 2 <= len(evidence) <= 4
                or not isinstance(unknown, list) or len(unknown) > 2):
            raise ValueError("format probe evidence or uncertainties invalid")
        citations = source_citations(a, b)
        if any(type(item) is not str or item not in citations for item in evidence):
            raise ValueError("format probe citation differs from source")
        if len(set(evidence)) != len(evidence):
            raise ValueError("format probe citation duplicated")
        for item in unknown:
            _bounded_text(item, 180)
        if answer["label"] in ("A", "B") and {citations[item]["candidate"] for item in evidence} != {"A", "B"}:
            raise ValueError("decisive format probe requires both candidates")
        return answer


def run_format_probe(directory: Path) -> dict:
    """Freeze and execute at most two A/B swaps once inside the original diagnostic allowance."""
    parent = directory / "diagnostic-gemma-teacher"
    manifest = _verified(parent / "manifest.json", "manifest_hash")
    original = _verified(parent / "report.json", "report_hash")
    review = _verified(parent / "blind-review.json", "review_hash")
    mapping = {r["case_id"]: r["swapped"] for r in _read(parent / "blind-map.json")}
    packet = _verified(parent / "blind-packet.json", "packet_hash")
    if (original["manifest_hash"] != manifest["manifest_hash"] or original["review_hash"] != review["review_hash"]
            or review["packet_hash"] != manifest["blind_packet_hash"]
            or packet["packet_hash"] != review["packet_hash"]):
        raise ValueError("format probe parent receipts mismatch")
    case = manifest["cases"][0]
    state = case["state"]
    blind_case = next(r for r in packet["cases"] if r["case_id"] == case["case_id"])
    expected = dict(state)
    if mapping[case["case_id"]]:
        expected["A"], expected["B"] = expected["B"], expected["A"]
    if blind_case != {"case_id": case["case_id"], **expected}:
        raise ValueError("format probe blind mapping invalid")
    blind = next(r["label"] for r in review["cases"] if r["case_id"] == case["case_id"])
    if mapping[case["case_id"]]:
        blind = REVERSE[blind]
    cache = directory / "provider-cache"
    teacher = SimpleGemmaTeacherProviders(cache)
    jobs = [(state["vacancy"], a, b, state["preferences"]) for a, b in
            ((state["A"], state["B"]), (state["B"], state["A"]))]
    identities = [teacher._identity("/v1/chat/completions", teacher._pair_payload(*job), "pair") for job in jobs]
    frozen = {"purpose": "Two-call format probe only; not a quality or activation gate",
              "source_manifest_hash": manifest["manifest_hash"], "source_report_hash": original["report_hash"],
              "review_hash": review["review_hash"], "case": case, "blind_label": blind,
              "maximum_attempts": 2, "request_hashes": [digest(i) for i in identities], "requests": identities}
    frozen["manifest_hash"] = digest(frozen)
    target = directory / "diagnostic-gemma-format-v2"
    _seal(target / "manifest.json", frozen)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            result = _verified(target / "report.json", "report_hash")
            if result["manifest_hash"] != frozen["manifest_hash"]:
                raise ValueError("format probe report mismatch")
            for row, key in zip(result["attempts"], frozen["request_hashes"], strict=True):
                if row["request_hash"] != key or row["raw_receipt"] != _attempt_receipt(directory, key):
                    raise ValueError("format probe cached response changed")
            return result
        if (target / "started.json").exists():
            raise ValueError("interrupted format probe requires operator review")
        before = _read(cache / "usage.json")
        if (before.get("pair", 0) + 2 > LIMITS["pair"] or
                before.get("pair", 0) - original["ledger_before"].get("pair", 0) + 2 > manifest["maximum_pair_attempts"]):
            raise ValueError("format probe exceeds remaining diagnostic budget")
        old = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": frozen["manifest_hash"], "ledger_before": before})
        attempts = []
        start = time.monotonic()
        for index, (job, key) in enumerate(zip(jobs, frozen["request_hashes"], strict=True)):
            answer, error = None, None
            try:
                answer = teacher.judge(*job)
            except Exception as exc:  # noqa: BLE001 - keep raw rejected replies, never echo HTTP headers/errors.
                error = type(exc).__name__
            attempts.append({"orientation": "reverse" if index else "direct", "request_hash": key,
                             "answer": answer, "error_type": error, "raw_receipt": _attempt_receipt(directory, key)})
        after = _read(cache / "usage.json")
        preserved = all((cache / name).exists() and digest((cache / name).read_bytes().hex()) == value for name, value in old.items())
        budget_ok = (0 <= after.get("pair", 0) - before.get("pair", 0) <= 2 and
                     all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade")))
        valid = sum(r["answer"] is not None for r in attempts)
        labels = [r["answer"]["label"] if r["answer"] else None for r in attempts]
        result = {"state": "format_passed" if valid == 2 and preserved and budget_ok else "stopped",
                  "manifest_hash": frozen["manifest_hash"], "attempts": attempts, "valid_responses": valid,
                  "symmetric": labels[1] == REVERSE[labels[0]] if valid == 2 else None,
                  "agrees_with_blind": labels[0] == blind if valid == 2 else None,
                  "ledger_before": before, "ledger_after": after, "budget_preserved": budget_ok,
                  "historical_cache_bytes_preserved": preserved, "elapsed_seconds": time.monotonic() - start,
                  "training_started": False, "teacher_promoted": False, "activation_allowed": False,
                  "quality_gate_passed": False, "factual_prose_review": "pending"}
        result["report_hash"] = digest(result)
        _seal(target / "report.json", result)
        return result


def _attempt_receipt(directory: Path, key: str) -> dict:
    if (directory / "provider-cache" / f"{key}.json").exists():
        return _cache(directory, key)
    failures = sorted((directory / "provider-cache").glob(f"{key}.failure-*.json"))
    if not failures:
        raise ValueError("format probe attempt receipt missing")
    rows = [_read(path) for path in failures]
    if any(r["request_hash"] != key or digest(r["request"]) != key for r in rows):
        raise ValueError("format probe failure receipt modified")
    return {"failures": rows}


def main() -> None:
    """Run or replay the explicitly authorized two-request diagnostic probe, without training."""
    parser = argparse.ArgumentParser(description="Two-call Gemma format probe")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    result = run_format_probe(args.directory)
    print(json.dumps({key: result[key] for key in ("state", "valid_responses", "symmetric", "agrees_with_blind", "ledger_after")}), flush=True)


if __name__ == "__main__":
    main()
