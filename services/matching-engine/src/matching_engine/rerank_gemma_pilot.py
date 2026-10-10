"""Frozen twenty-request alternative-teacher diagnosis, never training or activation."""

import argparse
import fcntl
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _cache, _read, _seal
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import LIMITS

SELECTION = {
    "frontend-05": ((0, 1), (0, 2), (0, 3)),
    "backend-05": ((5, 3), (5, 8), (2, 3), (7, 3)),
    "backend-06": ((5, 3), (1, 2), (0, 4)),
}
GATES = {"minimum_symmetric": 8, "minimum_blind_agreement": 8,
         "minimum_blind_decisive": 6, "minimum_decisive_agreement_fraction": .8,
         "maximum_fact_errors": 0}


def _verified(path: Path, hash_field: str) -> dict:
    value = _read(path)
    if value[hash_field] != digest({k: v for k, v in value.items() if k != hash_field}):
        raise ValueError("pilot source integrity failed")
    return value


def prepare_pilot(directory: Path) -> dict:
    """Freeze source facts, ten comparisons, gates and a shuffled score-blind packet without HTTP."""
    from matching_engine.rerank_gemma_teacher import (
        GemmaTeacherProviders,
        gemma_teacher_identity,
    )

    cases, sources = [], {}
    for family, pairs in SELECTION.items():
        if family == "frontend-05":
            source = _verified(directory / "diagnostic-frontend-05/pilot-manifest.json", "manifest_hash")
            sources[family] = source["manifest_hash"]
            states = {(r["a"], r["b"]): r["state"] for r in source["cases"]}
            split = source["split"]
        else:
            source = _verified(directory / "families" / f"{family}.json", "record_hash")
            sources[family] = source["record_hash"]
            split = source["manifest"]["split"]
            states = {(a, b): {"vacancy": source["vacancy"], "A": source["traits"][a],
                       "B": source["traits"][b], "preferences": source["manifest"]["preference"]}
                      for a, b in pairs}
        if split not in ("validation", "calibration"):
            raise ValueError("pilot must not consume train or golden labels")
        for a, b in pairs:
            case_id = digest({"family": family, "a": a, "b": b, "pilot": "gemma-v1"})[:12]
            cases.append({"case_id": case_id, "family": family, "split": split,
                          "a": a, "b": b, "state": states[a, b]})
    packet_cases, mapping = [], []
    for index, case in enumerate(cases):
        state = dict(case["state"])
        flip = index % 2 == 0
        if flip:
            state["A"], state["B"] = state["B"], state["A"]
        packet_cases.append({"case_id": case["case_id"], **state})
        mapping.append({"case_id": case["case_id"], "swapped": flip})
    random.Random(941).shuffle(packet_cases)
    packet = {"purpose": "Blind assistant preference audit; not independent human evaluation",
              "semantics": "Levels 0..7 are declarations; confirmation is a separate evidence axis. Formal grade differs from narrative role. Missing duration/production means unknown, never zero/false. Only stated requirements and preferences apply; unspecified salary/technical tradeoffs may justify insufficient. No candidate position bias.",
              "cases": packet_cases}
    packet["packet_hash"] = digest(packet)
    manifest = {"version": "rerank-gemma-pilot-v1", "teacher": gemma_teacher_identity(),
                "maximum_pair_attempts": 20, "sources": sources, "cases": cases,
                "gates": GATES, "blind_packet_hash": packet["packet_hash"],
                "scope": "Alternative teacher mechanics and subjective assistant agreement only"}
    manifest["manifest_hash"] = digest(manifest)
    target = directory / "diagnostic-gemma-teacher"
    _seal(target / "manifest.json", manifest)
    _seal(target / "blind-packet.json", packet)
    _seal(target / "blind-map.json", mapping)
    provider = GemmaTeacherProviders(directory / "provider-cache")
    request_keys = []
    for case in cases:
        state = case["state"]
        for a, b in ((state["A"], state["B"]), (state["B"], state["A"])):
            payload = provider._pair_payload(state["vacancy"], a, b, state["preferences"])
            request_keys.append(digest(provider._identity("/v1/chat/completions", payload, "pair")))
    _seal(target / "request-plan.json", {"manifest_hash": manifest["manifest_hash"], "request_keys": request_keys})
    return manifest


def _review(directory: Path, manifest: dict) -> dict:
    review = _verified(directory / "diagnostic-gemma-teacher/blind-review.json", "review_hash")
    ids = {r["case_id"] for r in manifest["cases"]}
    if (review["packet_hash"] != manifest["blind_packet_hash"] or review["reviewer_kind"] != "assistant"
            or review["independent_human_evaluation"] is not False
            or len(review["cases"]) != 10 or {r["case_id"] for r in review["cases"]} != ids
            or any(r["label"] not in REVERSE for r in review["cases"])):
        raise ValueError("complete blind assistant receipt required before HTTP")
    return review


def evaluate_gates(rows: list[dict]) -> dict:
    """Assess symmetry and exact blind agreement separately; abstention is not a binary win."""
    symmetric = sum(r["symmetric"] for r in rows)
    agreement = sum(r["agrees_with_blind"] for r in rows)
    decisive = [r for r in rows if r["blind_label"] in ("A", "B")]
    wins = sum(r["agrees_with_blind"] for r in decisive)
    fraction = wins / len(decisive) if decisive else None
    passed = (len(rows) == 10 and symmetric >= GATES["minimum_symmetric"]
              and agreement >= GATES["minimum_blind_agreement"]
              and len(decisive) >= GATES["minimum_blind_decisive"]
              and fraction is not None and fraction >= GATES["minimum_decisive_agreement_fraction"])
    return {"symmetric": symmetric, "blind_agreement": agreement,
            "blind_decisive": len(decisive), "decisive_agreement_fraction": fraction, "passed": passed}


def _replay(directory: Path, manifest: dict, review: dict) -> dict:
    from matching_engine.rerank_gemma_teacher import GemmaTeacherProviders

    report = _verified(directory / "diagnostic-gemma-teacher/report.json", "report_hash")
    if report["manifest_hash"] != manifest["manifest_hash"] or report["review_hash"] != review["review_hash"]:
        raise ValueError("pilot replay receipt mismatch")
    plan = _read(directory / "diagnostic-gemma-teacher/request-plan.json")["request_keys"]
    if report["attempt_keys"] != plan[:len(report["attempt_keys"])]:
        raise ValueError("pilot attempted requests differ from frozen plan")
    _check_attempt_receipts(directory, report["attempt_keys"])
    provider = GemmaTeacherProviders(directory / "provider-cache")
    states = {c["case_id"]: c["state"] for c in manifest["cases"]}
    for row in report["comparisons"]:
        state = states[row["case_id"]]
        for reverse, key in enumerate(row["cache_keys"]):
            a, b = (state["B"], state["A"]) if reverse else (state["A"], state["B"])
            payload = provider._pair_payload(state["vacancy"], a, b, state["preferences"])
            if digest(provider._identity("/v1/chat/completions", payload, "pair")) != key:
                raise ValueError("pilot request no longer matches frozen facts")
            cached = _cache(directory, key)
            if cached["response_hash"] != row["response_hashes"][reverse]:
                raise ValueError("pilot response changed after publication")
    return report


def _check_attempt_receipts(directory: Path, keys: list[str]) -> list[dict]:
    """Seal and check raw successful/failed attempts, including responses rejected by the parser."""
    receipts = []
    for key in keys:
        path = directory / "provider-cache" / f"{key}.json"
        if path.exists():
            record = _cache(directory, key)
            receipts.append({"request_hash": key, "response_hash": record["response_hash"]})
        else:
            failures = sorted((directory / "provider-cache").glob(f"{key}.failure-*.json"))
            if not failures:
                raise ValueError("pilot attempt receipt missing")
            records = [_read(p) for p in failures]
            if any(r["request_hash"] != key or digest(r["request"]) != key for r in records):
                raise ValueError("pilot failure receipt integrity failed")
            receipts.append({"request_hash": key, "failure_hashes": [digest(r) for r in records]})
    _seal(directory / "diagnostic-gemma-teacher/attempt-receipts.json", receipts)
    return receipts


def run_pilot(directory: Path) -> dict:
    """Make up to twenty attempts in bounded batches; crash/replay never starts an implicit retry."""
    from matching_engine.rerank_gemma_teacher import GemmaTeacherProviders

    manifest = prepare_pilot(directory)
    review = _review(directory, manifest)
    target = directory / "diagnostic-gemma-teacher"
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            return _replay(directory, manifest, review)
        if (target / "started.json").exists():
            raise ValueError("interrupted pilot requires operator review, not automatic retry")
        cache = directory / "provider-cache"
        before = _read(cache / "usage.json")
        if before.get("pair", 0) + 20 > LIMITS["pair"]:
            raise ValueError("insufficient remaining pair budget for fixed pilot")
        historical = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": manifest["manifest_hash"],
                                       "review_hash": review["review_hash"], "ledger_before": before})
        mapping = {r["case_id"]: r["swapped"] for r in _read(target / "blind-map.json")}
        labels = {r["case_id"]: REVERSE[r["label"]] if mapping[r["case_id"]] else r["label"]
                  for r in review["cases"]}
        provider = GemmaTeacherProviders(cache)
        rows, attempts = [], []
        error = None
        start = time.monotonic()
        jobs, completed = [], {}
        for index, case in enumerate(manifest["cases"]):
            state = case["state"]
            for reverse, (a, b) in enumerate(((state["A"], state["B"]), (state["B"], state["A"]))):
                payload = provider._pair_payload(state["vacancy"], a, b, state["preferences"])
                key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
                jobs.append((index, reverse, key, state["vacancy"], a, b, state["preferences"]))
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                for offset in range(0, len(jobs), 5):
                    batch = jobs[offset:offset + 5]
                    attempts.extend(job[2] for job in batch)
                    futures = [pool.submit(provider.judge, *job[3:]) for job in batch]
                    batch_error = None
                    # Drain every already submitted call before sealing the ledger/report.
                    for job, future in zip(batch, futures, strict=True):
                        try:
                            answer = future.result()
                            cached = _cache(directory, job[2])
                            completed[job[0], job[1]] = (answer, job[2], cached["response_hash"])
                        except Exception as exc:  # noqa: BLE001 - retain bounded completed work on failure.
                            batch_error = exc
                    print(json.dumps({"completed_requests": len(completed), "submitted_requests": len(attempts)}), flush=True)
                    if batch_error:
                        raise batch_error
        except Exception as exc:  # noqa: BLE001 - preserve failed attempts without exposing provider text.
            error = type(exc).__name__
        for index, case in enumerate(manifest["cases"]):
            if (index, 0) in completed and (index, 1) in completed:
                entries = [completed[index, reverse] for reverse in (0, 1)]
                answers, keys, hashes = ([entry[column] for entry in entries] for column in range(3))
                blind = labels[case["case_id"]]
                rows.append({"case_id": case["case_id"], "family": case["family"],
                             "a": case["a"], "b": case["b"], "answers": answers,
                             "cache_keys": keys, "response_hashes": hashes, "blind_label": blind,
                             "symmetric": answers[1]["label"] == REVERSE[answers[0]["label"]],
                             "agrees_with_blind": answers[0]["label"] == blind})
        after = _read(cache / "usage.json")
        preserved = all((cache / name).exists() and digest((cache / name).read_bytes().hex()) == value
                        for name, value in historical.items())
        budget_ok = (0 <= after.get("pair", 0) - before.get("pair", 0) <= 20
                     and all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade")))
        gates = evaluate_gates(rows)
        report = {"state": "needs_fact_review" if gates["passed"] and preserved and budget_ok and error is None else "stopped",
                  "manifest_hash": manifest["manifest_hash"], "review_hash": review["review_hash"],
                  "teacher": manifest["teacher"], "comparisons": rows, "attempt_keys": attempts,
                  "gates": gates, "error_type": error, "elapsed_seconds": time.monotonic() - start,
                  "ledger_before": before, "ledger_after": after, "budget_preserved": budget_ok,
                  "historical_cache_bytes_preserved": preserved, "training_started": False,
                  "activation_allowed": False, "cohort_teacher_changed": False,
                  "independent_human_evaluation": False,
                  "factual_review": "Exact cited values validated; prose interpretation still requires manual review",
                  "factual_gate_passed": False}
        report["report_hash"] = digest(report)
        _check_attempt_receipts(directory, attempts)
        _seal(target / "report.json", report)
        return report


def main() -> None:
    """Prepare a score-blind packet or explicitly execute the bounded Gemma diagnosis."""
    parser = argparse.ArgumentParser(description="Alternative teacher pilot, no training")
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    result = prepare_pilot(args.directory) if args.command == "prepare" else run_pilot(args.directory)
    print(json.dumps({k: result[k] for k in ("manifest_hash", "state", "gates", "error_type") if k in result}), flush=True)


if __name__ == "__main__":
    main()
