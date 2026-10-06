"""Eight-call targeted teacher comparison; never training, relabeling or activation."""

import argparse
import fcntl
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_gemma_format import _attempt_receipt
from matching_engine.rerank_gemma_pilot import _review, _verified, prepare_pilot
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import (
    COMPARATIVE_MODELS,
    ComparativeTeacherProviders,
)
from matching_engine.rerank_providers import LIMITS

SELECTION = (("frontend-05", 0, 1), ("backend-05", 5, 3))


def prepare_comparison(directory: Path) -> dict:
    """Freeze two previously reviewed validation cases and eight requests without credentials or HTTP."""
    parent = directory / "diagnostic-gemma-teacher"
    manifest = prepare_pilot(directory)
    original = _verified(parent / "report.json", "report_hash")
    review = _review(directory, manifest)
    packet = _verified(parent / "blind-packet.json", "packet_hash")
    mapping_rows = _read(parent / "blind-map.json")
    if (len(mapping_rows) != len(manifest["cases"])
            or {r["case_id"] for r in mapping_rows} != {r["case_id"] for r in manifest["cases"]}
            or any(type(r["swapped"]) is not bool for r in mapping_rows)
            or original["manifest_hash"] != manifest["manifest_hash"]
            or original["review_hash"] != review["review_hash"]
            or packet["packet_hash"] != review["packet_hash"]):
        raise ValueError("comparison parent receipts mismatch")
    mapping = {r["case_id"]: r["swapped"] for r in mapping_rows}
    cases, requests = [], []
    for family, a, b in SELECTION:
        case = next(r for r in manifest["cases"] if (r["family"], r["a"], r["b"]) == (family, a, b))
        if case["split"] != "validation":
            raise ValueError("comparison requires validation sources")
        state = case["state"]
        expected = dict(state)
        if mapping[case["case_id"]]:
            expected["A"], expected["B"] = expected["B"], expected["A"]
        blind_case = next(r for r in packet["cases"] if r["case_id"] == case["case_id"])
        if blind_case != {"case_id": case["case_id"], **expected}:
            raise ValueError("comparison blind mapping invalid")
        label = next(r["label"] for r in review["cases"] if r["case_id"] == case["case_id"])
        label = REVERSE[label] if mapping[case["case_id"]] else label
        cases.append({**case, "blind_label": label})
        for model in COMPARATIVE_MODELS:
            teacher = ComparativeTeacherProviders(directory / "provider-cache", model=model)
            for orientation, left, right in (("direct", state["A"], state["B"]), ("reverse", state["B"], state["A"])):
                payload = teacher._pair_payload(state["vacancy"], left, right, state["preferences"])
                identity = teacher._identity("/v1/chat/completions", payload, "pair")
                requests.append({"case_id": case["case_id"], "model": model, "orientation": orientation,
                                 "request_hash": digest(identity), "request": identity})
    if len({r["request_hash"] for r in requests}) != 8:
        raise ValueError("comparison requests must be unique")
    frozen = {"version": "rerank-teacher-comparison-v3", "maximum_attempts": 8,
              "source_manifest_hash": manifest["manifest_hash"], "source_report_hash": original["report_hash"],
              "review_hash": review["review_hash"], "cases": cases, "requests": requests,
              "diagnostic_pair_baseline": original["ledger_before"].get("pair", 0),
              "diagnostic_allowance": manifest["maximum_pair_attempts"],
              "selection": "Purposeful reuse: clear and ambiguous cases; prior predictions exposed to selector",
              "evaluation": "Frozen score-blind assistant review; not independent human evaluation",
              "gates": {"accepted_per_model": 4, "symmetric_per_model": 2, "blind_agreement_per_model": 2,
                        "unresolved_fact_errors": 0, "prose_review_required": True}}
    frozen["manifest_hash"] = digest(frozen)
    _seal(directory / "diagnostic-teacher-comparison-v3/manifest.json", frozen)
    return frozen


def summarize_comparison(attempts: list[dict], cases: list[dict]) -> dict:
    """Report strict format, swap consistency and frozen assistant agreement as separate signals."""
    result = {}
    for model in COMPARATIVE_MODELS:
        rows = [r for r in attempts if r["model"] == model]
        comparisons = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        usage_complete = True
        for row in rows:
            actual = row["raw_receipt"].get("response", {}).get("usage", {})
            for field in usage:
                if type(actual.get(field)) is int and actual[field] >= 0:
                    usage[field] += actual[field]
                else:
                    usage_complete = False
        for case in cases:
            pair = [r for r in rows if r["case_id"] == case["case_id"]]
            direct = next(r["answer"] for r in pair if r["orientation"] == "direct")
            reverse = next(r["answer"] for r in pair if r["orientation"] == "reverse")
            comparisons.append({"case_id": case["case_id"], "family": case["family"],
                                "blind_label": case["blind_label"],
                                "direct_label": direct["label"] if direct else None,
                                "reverse_label": reverse["label"] if reverse else None,
                                "symmetric": bool(direct and reverse and reverse["label"] == REVERSE[direct["label"]]),
                                "agrees_with_blind": bool(direct and reverse and direct["label"] == case["blind_label"]
                                                          and reverse["label"] == REVERSE[case["blind_label"]])})
        accepted = sum(r["answer"] is not None for r in rows)
        symmetric = sum(r["symmetric"] for r in comparisons)
        agreement = sum(r["agrees_with_blind"] for r in comparisons)
        result[model] = {"accepted": accepted, "symmetric": symmetric, "blind_agreement": agreement,
                         "mechanical_gate_passed": accepted == 4 and symmetric == 2 and agreement == 2,
                         "comparisons": comparisons, "observed_usage": usage, "usage_complete": usage_complete,
                         "factual_prose_review": "pending", "quality_gate_passed": False}
    return result


def run_comparison(directory: Path) -> dict:
    """Run at most eight requests once, drain four concurrent calls per case and replay sealed raw receipts."""
    frozen = prepare_comparison(directory)
    target = directory / "diagnostic-teacher-comparison-v3"
    cache = directory / "provider-cache"
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            result = _verified(target / "report.json", "report_hash")
            if result["manifest_hash"] != frozen["manifest_hash"]:
                raise ValueError("comparison report mismatch")
            for row, request in zip(result["attempts"], frozen["requests"], strict=True):
                if (any(row[k] != request[k] for k in ("case_id", "model", "orientation", "request_hash"))
                        or row["raw_receipt"] != _attempt_receipt(directory, row["request_hash"])):
                    raise ValueError("comparison cached response changed")
            return result
        if (target / "started.json").exists():
            raise ValueError("interrupted comparison requires operator review")
        before = _read(cache / "usage.json")
        spent = before.get("pair", 0) - frozen["diagnostic_pair_baseline"]
        if (spent < 0 or spent + 8 > frozen["diagnostic_allowance"] or before.get("pair", 0) + 8 > LIMITS["pair"]):
            raise ValueError("comparison exceeds remaining diagnostic budget")
        if any((cache / f'{r["request_hash"]}.json').exists()
               or list(cache.glob(f'{r["request_hash"]}.failure-*.json')) for r in frozen["requests"]):
            raise ValueError("unpublished comparison attempts require operator review")
        old = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": frozen["manifest_hash"], "ledger_before": before})
        states = {c["case_id"]: c["state"] for c in frozen["cases"]}

        def execute(request: dict) -> dict:
            state = states[request["case_id"]]
            a, b = (state["A"], state["B"]) if request["orientation"] == "direct" else (state["B"], state["A"])
            teacher = ComparativeTeacherProviders(cache, model=request["model"])
            answer, error = None, None
            start = time.monotonic()
            try:
                answer = teacher.judge(state["vacancy"], a, b, state["preferences"])
            except Exception as exc:  # noqa: BLE001 - raw receipts retained, no secret-bearing exception text.
                error = type(exc).__name__
            return {k: request[k] for k in ("case_id", "model", "orientation", "request_hash")} | {
                "answer": answer, "error_type": error, "elapsed_seconds": time.monotonic() - start,
                "raw_receipt": _attempt_receipt(directory, request["request_hash"])}

        attempts = []
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for offset in (0, 4):
                attempts.extend(pool.map(execute, frozen["requests"][offset:offset + 4]))
        after = _read(cache / "usage.json")
        preserved = all((cache / name).exists() and digest((cache / name).read_bytes().hex()) == value for name, value in old.items())
        budget_ok = (after.get("pair", 0) - before.get("pair", 0) == 8
                     and all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade")))
        models = summarize_comparison(attempts, frozen["cases"])
        result = {"state": "needs_fact_review" if preserved and budget_ok else "stopped",
                  "manifest_hash": frozen["manifest_hash"], "attempts": attempts, "models": models,
                  "ledger_before": before, "ledger_after": after, "budget_preserved": budget_ok,
                  "historical_cache_bytes_preserved": preserved, "elapsed_seconds": time.monotonic() - started,
                  "diagnostic_attempts_remaining": frozen["diagnostic_allowance"] - (after.get("pair", 0) - frozen["diagnostic_pair_baseline"]),
                  "training_started": False, "teacher_promoted": False, "activation_allowed": False,
                  "quality_gate_passed": False}
        result["report_hash"] = digest(result)
        _seal(target / "report.json", result)
        return result


def main() -> None:
    """Execute or replay only the explicitly approved eight-call teacher comparison."""
    parser = argparse.ArgumentParser(description="Bounded Gemma/Qwen comparison")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    result = run_comparison(args.directory)
    print(json.dumps({"state": result["state"], "models": result["models"], "ledger_after": result["ledger_after"],
                      "diagnostic_attempts_remaining": result["diagnostic_attempts_remaining"]}), flush=True)


if __name__ == "__main__":
    main()
