"""Cache-only fact audit and one bounded opt-in prompt-robustness pilot.

This diagnostic never generates profiles, fits a model, changes the cohort,
or promotes a teacher. Assistant judgements are not independent human labels.
"""

import argparse
import fcntl
import json
import random
import time
from pathlib import Path

from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    SYSTEMONE_PAIR_ABSTENTION_POLICY,
    SYSTEMONE_PAIR_PROMPT_VERSION,
    SystemOneProviders,
    teacher_identity,
)

PAIRS = ((0, 1), (0, 2), (0, 3), (0, 5), (0, 4), (0, 6), (0, 7), (0, 8), (1, 2), (2, 3))
REVERSE = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seal(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError("immutable diagnostic receipt differs")
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _cache(directory: Path, key: str) -> dict:
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid diagnostic source key")
    record = _read(directory / "provider-cache" / f"{key}.json")
    if (record["request_hash"] != key or digest(record["request"]) != key
            or digest(record["response"]) != record["response_hash"]):
        raise ValueError("diagnostic source cache integrity failed")
    return record


def audit_cached_pairs(directory: Path, source_report: Path) -> dict:
    """Verify the three exact A/B swaps and preserve a score-blind fact packet without HTTP."""
    report = _read(source_report)
    if report["report_hash"] != digest({k: v for k, v in report.items() if k != "report_hash"}):
        raise ValueError("diagnostic source report integrity failed")
    if report["failed_family"] != "frontend-05" or len(report["failed_family_swap_audits"]) != 3:
        raise ValueError("diagnostic requires the three original frontend-05 pairs")
    cases, mapping, rows = [], [], []
    for index, audit in enumerate(report["failed_family_swap_audits"]):
        direct = _cache(directory, audit["direct_request_hash"])
        reverse = _cache(directory, audit["swapped_request_hash"])
        state, swapped = (json.loads(r["request"]["payload"]["state"]) for r in (direct, reverse))
        if not (state["A"] == swapped["B"] and state["B"] == swapped["A"]
                and {k: v for k, v in state.items() if k not in ("A", "B")} ==
                {k: v for k, v in swapped.items() if k not in ("A", "B")}):
            raise ValueError("cached requests are not exact swaps")
        case_id = digest({"request": audit["direct_request_hash"], "blind": "v1"})[:12]
        flip = index % 2 == 0
        blind_state = dict(state)
        if flip:
            blind_state["A"], blind_state["B"] = blind_state["B"], blind_state["A"]
        cases.append({"case_id": case_id, **blind_state})
        mapping.append({"case_id": case_id, "original_a": audit["a"], "original_b": audit["b"],
                        "swapped": flip, "source_request_hash": audit["direct_request_hash"]})
        answers = [r["response"]["answers"]["preference"] for r in (direct, reverse)]
        rows.append({"a": audit["a"], "b": audit["b"], "state": state,
                     "direct_source_hash": direct["request_hash"], "reverse_source_hash": reverse["request_hash"],
                     "direct_answer": answers[0], "reverse_answer": answers[1],
                     "exact_swap": True, "raw_consistent": answers[1]["choice"] == REVERSE[answers[0]["choice"]]})
    random.Random(137).shuffle(cases)
    packet = {"purpose": "Blind assistant fact audit; not an independent human hiring preference evaluation",
              "level_scale": "0..7; levels are declarations, status states evidence; missing structured facts are unknown, not zero experience; grade is distinct from matching requirements",
              "cases": cases}
    packet["packet_hash"] = digest(packet)
    target = directory / "diagnostic-frontend-05"
    _seal(target / "blind-packet.json", packet)
    _seal(target / "blind-map.json", mapping)
    result = {"source_report_hash": report["report_hash"], "blind_packet_hash": packet["packet_hash"],
              "teacher": teacher_identity(), "pairs": rows, "http_calls": 0,
              "confidence_definition": "(maximum probability - 1/4) / (1 - 1/4); not calibrated correctness",
              "confidence_source": "https://github.com/jaredpalmer/kev/blob/main/kev/api.py",
              "raw_consistency": sum(r["raw_consistent"] for r in rows) / len(rows)}
    result["report_hash"] = digest(result)
    _seal(target / "raw-audit.json", result)
    return result


def _pilot_manifest(directory: Path, audit: dict, review: dict) -> dict:
    if (review["packet_hash"] != audit["blind_packet_hash"] or review["reviewer_kind"] != "assistant"
            or review["independent_human_evaluation"] is not False
            or review["review_hash"] != digest({k: v for k, v in review.items() if k != "review_hash"})):
        raise ValueError("blind assistant review receipt is missing or modified")
    expected_ids = {row["case_id"] for row in _read(directory / "diagnostic-frontend-05/blind-packet.json")["cases"]}
    if (len(review["cases"]) != 3 or {row["case_id"] for row in review["cases"]} != expected_ids
            or any(row["label"] not in REVERSE for row in review["cases"])):
        raise ValueError("blind review does not cover all original cases")
    traits = []
    for batch in (1, 2):
        receipt = _read(directory / "generation-batches" / f"frontend-05-{batch}.json")
        if receipt["receipt_hash"] != digest({k: v for k, v in receipt.items() if k != "receipt_hash"}):
            raise ValueError("source generation batch integrity failed")
        traits.extend(receipt["traits"])
    if len(traits) != 10:
        raise ValueError("unexpected source family size")
    for index, trait in enumerate(traits):
        trait["grade"] = ("intern", "junior", "middle", "senior")[(index + 5) % 4]
        if index == 9:
            trait["status"] = "rejected"
        trait["structured_facts_available"] = index % 4 != 0
        if not trait["structured_facts_available"]:
            trait["duration_months"] = None
            trait["production_support"] = None
    source = audit["pairs"][0]["state"]
    legacy = SystemOneProviders(directory / "provider-cache", key_file=directory / "unused-key")
    cases = []
    for a, b in PAIRS:
        payload = legacy._pair_payload(source["vacancy"], traits[a], traits[b], source["preferences"])
        key = digest(legacy._identity("/v1/systemone", payload, "pair"))
        cached = _cache(directory, key)
        cases.append({"a": a, "b": b, "original_hard_case": (a, b) in PAIRS[:3],
                      "salary_focused_nonisolated_control": (a, b) == (0, 5),
                      "source_request_hash": key, "state": json.loads(cached["request"]["payload"]["state"])})
    result = {"purpose": "Prompt-robustness diagnostic, not relevance or hiring quality evaluation",
              "teacher": teacher_identity(SYSTEMONE_PAIR_PROMPT_VERSION), "abstention_policy": SYSTEMONE_PAIR_ABSTENTION_POLICY,
              "maximum_pair_attempts": 20, "family_id": "frontend-05", "split": "validation",
              "source_audit_hash": audit["report_hash"], "review_hash": review["review_hash"], "cases": cases,
              "gates": {"minimum_raw_consistent": 8, "minimum_final_consistent": 8,
                        "minimum_direct_decisive": 6, "all_original_hard_cases_decisive_and_consistent": True}}
    result["manifest_hash"] = digest(result)
    return result


def pilot_gates(rows: list[dict]) -> dict:
    """Require raw and derived symmetry; abstention cannot rescue any original hard case."""
    raw = sum(row["raw_consistent"] for row in rows)
    final = sum(row["final_consistent"] for row in rows)
    decisive = sum(row["direct_label"] in ("A", "B") for row in rows)
    hard = [r for r in rows if r["original_hard_case"]]
    hard_pass = len(hard) == 3 and all(r["raw_consistent"] and r["final_consistent"] and
        all(r[key] in ("A", "B") for key in ("direct_label", "reverse_label", "direct_raw_label", "reverse_raw_label")) for r in hard)
    return {"raw_consistent": raw, "final_consistent": final, "direct_decisive": decisive,
            "hard_cases_passed": hard_pass,
            "passed": len(rows) == 10 and raw >= 8 and final >= 8 and decisive >= 6 and hard_pass}


def run_prompt_pilot(directory: Path, source_report: Path) -> dict:
    """Run at most twenty new pair attempts once; failed/crashed pilots never retry automatically."""
    target = directory / "diagnostic-frontend-05"
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".pilot.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_prompt_pilot(directory, source_report)


def _run_prompt_pilot(directory: Path, source_report: Path) -> dict:
    audit = audit_cached_pairs(directory, source_report)
    target = directory / "diagnostic-frontend-05"
    review = _read(target / "blind-review.json")
    manifest = _pilot_manifest(directory, audit, review)
    _seal(target / "pilot-manifest.json", manifest)
    destination = target / "pilot-report.json"
    if destination.exists():
        previous = _read(destination)
        if (previous["manifest_hash"] != manifest["manifest_hash"] or
                previous["report_hash"] != digest({k: v for k, v in previous.items() if k != "report_hash"})):
            raise ValueError("diagnostic pilot report integrity failed")
        for row in previous["comparisons"]:
            for key, name in zip(row["cache_keys"], ("direct_answer", "reverse_answer"), strict=True):
                cached = _cache(directory, key)
                if (cached["request"].get("prompt_version") != SYSTEMONE_PAIR_PROMPT_VERSION
                        or cached["response"]["answers"]["preference"] != row[name]):
                    raise ValueError("diagnostic pilot differs from cached teacher receipt")
        return previous
    started = target / "pilot-started.json"
    if started.exists():
        raise ValueError("interrupted pilot requires operator review; do not retry automatically")
    old_cache = {path.name: digest(path.read_bytes().hex()) for path in (directory / "provider-cache").glob("*.json")
                 if path.name != "usage.json"}
    before = _read(directory / "provider-cache/usage.json")
    _seal(started, {"manifest_hash": manifest["manifest_hash"], "ledger_before": before})
    teacher = SystemOneProviders(directory / "provider-cache", pair_prompt_version=SYSTEMONE_PAIR_PROMPT_VERSION)
    rows = []
    started_at = time.monotonic()
    error = None
    try:
        for case in manifest["cases"]:
            state = case["state"]
            labels, answers, keys = [], [], []
            for a, b in ((state["A"], state["B"]), (state["B"], state["A"])):
                labels.append(teacher.pair(state["vacancy"], a, b, state["preferences"]))
                payload = teacher._pair_payload(state["vacancy"], a, b, state["preferences"])
                key = digest(teacher._identity("/v1/systemone", payload, "pair"))
                keys.append(key)
                answers.append(_cache(directory, key)["response"]["answers"]["preference"])
            raw = [answer["choice"] for answer in answers]
            rows.append({"a": case["a"], "b": case["b"], "original_hard_case": case["original_hard_case"],
                         "salary_focused_nonisolated_control": case["salary_focused_nonisolated_control"],
                         "direct_label": labels[0], "reverse_label": labels[1],
                         "direct_raw_label": raw[0], "reverse_raw_label": raw[1],
                         "direct_answer": answers[0], "reverse_answer": answers[1], "cache_keys": keys,
                         "raw_consistent": raw[1] == REVERSE[raw[0]], "final_consistent": labels[1] == REVERSE[labels[0]]})
            print(json.dumps({"pilot_completed_pairs": len(rows), "raw_consistent": sum(r["raw_consistent"] for r in rows)}), flush=True)
    except Exception as exc:  # noqa: BLE001 - seal failures without leaking provider text or retrying.
        error = type(exc).__name__
    preserved = all((directory / "provider-cache" / name).exists() and
                    digest((directory / "provider-cache" / name).read_bytes().hex()) == value for name, value in old_cache.items())
    after = _read(directory / "provider-cache/usage.json")
    gates = pilot_gates(rows)
    budget_preserved = (0 <= after["pair"] - before["pair"] <= 20 and
                        all(after.get(kind, 0) == before.get(kind, 0) for kind in ("generation", "grade")))
    result = {"state": "passed" if gates["passed"] and preserved and budget_preserved and error is None else "stopped",
              "purpose": manifest["purpose"], "teacher": manifest["teacher"], "manifest_hash": manifest["manifest_hash"],
              "source_audit_hash": audit["report_hash"], "review_hash": review["review_hash"],
              "abstention_policy": manifest["abstention_policy"], "comparisons": rows, "gates": gates,
              "error_type": error, "elapsed_seconds": time.monotonic() - started_at,
              "ledger_before": before, "ledger_after": after, "historical_cache_bytes_preserved": preserved,
              "budget_preserved": budget_preserved, "training_started": False, "cohort_teacher_changed": False,
              "independent_human_evaluation": False, "activation_allowed": False}
    result["report_hash"] = digest(result)
    _seal(destination, result)
    return result


def main() -> None:
    """Audit existing cache or explicitly run the frozen twenty-call diagnostic pilot."""
    parser = argparse.ArgumentParser(description="Bounded frontend-05 teacher diagnosis")
    parser.add_argument("command", choices=("audit", "pilot"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--source-report", type=Path, default=Path("data/validation/rerank-remote-generation-report.json"))
    args = parser.parse_args()
    result = audit_cached_pairs(args.directory, args.source_report) if args.command == "audit" else run_prompt_pilot(args.directory, args.source_report)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
