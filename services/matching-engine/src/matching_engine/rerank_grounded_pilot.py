"""Fresh bounded prompt robustness diagnostic; consume the global ledger without reopening old allowances."""

import argparse
import fcntl
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_gemma_format import _attempt_receipt
from matching_engine.rerank_gemma_pilot import _review, _verified, prepare_pilot
from matching_engine.rerank_grounded_teacher import (
    GroundedTeacherProviders,
    grounded_explanation,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import LIMITS
from matching_engine.rerank_teacher_comparison import prepare_comparison

GATES = {"total_cases": 10, "minimum_valid_pairs": 8, "minimum_symmetric": 8,
         "minimum_both_orientation_agreement": 8, "minimum_valid_decisive_pairs": 6,
         "minimum_decisive_agreement_fraction": .8}


def prepare_grounded_pilot(directory: Path, *, explicit_policy: bool = False) -> dict:
    """Freeze twenty requests and all denominators before HTTP; no main scores or reviewer labels in input."""
    comparison = prepare_comparison(directory)
    parent = prepare_pilot(directory)
    review = _review(directory, parent)
    source = directory / "diagnostic-gemma-teacher"
    packet = _verified(source / "blind-packet.json", "packet_hash")
    mapping = {r["case_id"]: r["swapped"] for r in _read(source / "blind-map.json")}
    labels = {r["case_id"]: r["label"] for r in review["cases"]}
    cases, requests = [], []
    provider = GroundedTeacherProviders(directory / "provider-cache")
    for case in parent["cases"]:
        state = case["state"]
        expected = dict(state)
        if mapping[case["case_id"]]:
            expected["A"], expected["B"] = expected["B"], expected["A"]
        if next(r for r in packet["cases"] if r["case_id"] == case["case_id"]) != {"case_id": case["case_id"], **expected}:
            raise ValueError("grounded pilot blind mapping invalid")
        label = REVERSE[labels[case["case_id"]]] if mapping[case["case_id"]] else labels[case["case_id"]]
        cases.append({**case, "blind_label": label, "role": f'diagnostic_{case["split"]}',
                      "candidate_A_id": f'{case["family"]}:{case["a"]}', "candidate_B_id": f'{case["family"]}:{case["b"]}'})
        for orientation, a, b in (("direct", state["A"], state["B"]), ("reverse", state["B"], state["A"])):
            payload = provider._pair_payload(state["vacancy"], a, b, state["preferences"])
            identity = provider._identity("/v1/chat/completions", payload, "pair")
            requests.append({"case_id": case["case_id"], "orientation": orientation,
                             "request": identity, "request_hash": digest(identity)})
    if len({r["request_hash"] for r in requests}) != 20:
        raise ValueError("grounded pilot requires twenty unique requests")
    variant_hash = None
    if explicit_policy:
        from matching_engine.rerank_explicit_cases import reviewed_explicit_cases

        variant, review, cases = reviewed_explicit_cases(directory)
        variant_hash = variant["manifest_hash"]
        requests = []
        for case in cases:
            state = case["state"]
            for orientation, a, b in (("direct", state["A"], state["B"]), ("reverse", state["B"], state["A"])):
                payload = provider._pair_payload(state["vacancy"], a, b, state["preferences"])
                identity = provider._identity("/v1/chat/completions", payload, "pair")
                requests.append({"case_id": case["case_id"], "orientation": orientation,
                                 "request": identity, "request_hash": digest(identity)})
        if len({r["request_hash"] for r in requests}) != 20:
            raise ValueError("explicit policy pilot requires twenty unique requests")
    frozen = {"version": "rerank-grounded-pilot-v5-explicit" if explicit_policy else "rerank-grounded-pilot-v5", "maximum_attempts": 20, "global_limits": LIMITS,
              "source_manifest_hash": parent["manifest_hash"], "review_hash": review["review_hash"],
              "comparison_manifest_hash": comparison["manifest_hash"], "cases": cases, "requests": requests,
              "gates": GATES, "budget_scope": "Fresh diagnostic within original global caps; prior closed 20 remain immutable",
              "reviewer_kind": "assistant", "independent_human_evaluation": False,
              "prior_exposure": "Original facts and prior model outputs known; this diagnostic is not held-out model evaluation",
              "training_eligible": False, "activation_eligible": False,
              "fatal_conditions": "Any HTTP 401/403/404, or at least two schema HTTP 400/422 in one drained batch. Individual malformed replies do not stop later requests.",
              "denominators": "Swap/agreement gate counts out of 10 expected cases, requiring both valid orientations; decisive fraction uses all expected decisive cases, invalid are non-successes. Valid-only metrics separately reported."}
    if variant_hash:
        frozen["variant_manifest_hash"] = variant_hash
    frozen["manifest_hash"] = digest(frozen)
    _seal(directory / ("diagnostic-grounded-qwen-v5-explicit" if explicit_policy else "diagnostic-grounded-qwen-v5") / "manifest.json", frozen)
    return frozen


def grounded_gates(attempts: list[dict], cases: list[dict]) -> dict:
    """Evaluate exact swaps through stable candidate IDs; report abstentions and invalid coverage separately."""
    pairs = []
    for case in cases:
        rows = [r for r in attempts if r["case_id"] == case["case_id"]]
        answers = {r["orientation"]: r["answer"] for r in rows if r["answer"] is not None}
        complete = set(answers) == {"direct", "reverse"}
        winners = {}
        for orientation, answer in answers.items():
            ids = {"A": case["candidate_A_id"], "B": case["candidate_B_id"]}
            if orientation == "reverse":
                ids = {"A": ids["B"], "B": ids["A"]}
            winners[orientation] = ids.get(answer["label"], answer["label"])
        agrees = bool(complete and answers["direct"]["label"] == case["blind_label"]
                      and answers["reverse"]["label"] == REVERSE[case["blind_label"]])
        pairs.append({"case_id": case["case_id"], "family": case["family"], "split": case["split"],
                      "blind_label": case["blind_label"], "labels": {k: a["label"] for k, a in answers.items()},
                      "candidate_outcomes": winners, "valid_pair": complete,
                      "symmetric": winners["direct"] == winners["reverse"] if complete else None,
                      "both_orientation_agreement": agrees})
    valid = sum(p["valid_pair"] for p in pairs)
    symmetric = sum(p["symmetric"] is True for p in pairs)
    agreement = sum(p["both_orientation_agreement"] for p in pairs)
    decisive = [p for p in pairs if p["blind_label"] in ("A", "B")]
    decisive_valid = sum(p["valid_pair"] for p in decisive)
    decisive_wins = sum(p["both_orientation_agreement"] for p in decisive)
    fraction = decisive_wins / len(decisive) if decisive else None
    passed = (len(cases) == GATES["total_cases"] and valid >= GATES["minimum_valid_pairs"]
              and symmetric >= GATES["minimum_symmetric"] and agreement >= GATES["minimum_both_orientation_agreement"]
              and decisive_valid >= GATES["minimum_valid_decisive_pairs"]
              and fraction is not None and fraction >= GATES["minimum_decisive_agreement_fraction"])
    return {"passed": passed, "pairs": pairs, "expected_pairs": len(cases), "valid_pairs": valid,
            "symmetric_pairs": symmetric, "both_orientation_agreement": agreement,
            "expected_decisive": len(decisive), "valid_decisive": decisive_valid,
            "decisive_agreement_count": decisive_wins, "decisive_agreement_expected_fraction": fraction,
            "valid_only_decisive_agreement_fraction": decisive_wins / decisive_valid if decisive_valid else None,
            "expected_abstentions": len(cases) - len(decisive),
            "abstention_agreement": sum(p["both_orientation_agreement"] for p in pairs if p not in decisive),
            "accepted_responses": sum(r["answer"] is not None for r in attempts),
            "invalid_responses": sum(r["answer"] is None for r in attempts)}


def run_grounded_pilot(directory: Path, *, explicit_policy: bool = False) -> dict:
    """Run one immutable twenty-call diagnostic, up to five parallel calls, no automatic retries or training."""
    frozen = prepare_grounded_pilot(directory, explicit_policy=explicit_policy)
    target = directory / ("diagnostic-grounded-qwen-v5-explicit" if explicit_policy else "diagnostic-grounded-qwen-v5")
    cache = directory / "provider-cache"
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            report = _verified(target / "report.json", "report_hash")
            if report["manifest_hash"] != frozen["manifest_hash"] or not 1 <= len(report["attempts"]) <= 20:
                raise ValueError("grounded pilot report mismatch")
            for row, request in zip(report["attempts"], frozen["requests"][:len(report["attempts"])], strict=True):
                if any(row[k] != request[k] for k in ("case_id", "orientation", "request_hash")) or row["raw_receipt"] != _attempt_receipt(directory, row["request_hash"]):
                    raise ValueError("grounded pilot cached receipt changed")
            return report
        if (target / "started.json").exists():
            raise ValueError("interrupted grounded pilot requires operator review")
        before = _read(cache / "usage.json")
        if before.get("pair", 0) + 20 > LIMITS["pair"]:
            raise ValueError("grounded pilot exceeds global pair budget")
        if any((cache / f'{r["request_hash"]}.json').exists() or list(cache.glob(f'{r["request_hash"]}.failure-*.json')) for r in frozen["requests"]):
            raise ValueError("unpublished grounded pilot requires operator review")
        historical = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": frozen["manifest_hash"], "ledger_before": before})
        states = {c["case_id"]: c["state"] for c in frozen["cases"]}

        def execute(request: dict) -> dict:
            state = states[request["case_id"]]
            a, b = (state["A"], state["B"]) if request["orientation"] == "direct" else (state["B"], state["A"])
            provider = GroundedTeacherProviders(cache)
            answer, explanation, error = None, None, None
            try:
                answer = provider.judge(state["vacancy"], a, b, state["preferences"])
                explanation = grounded_explanation(state["vacancy"], a, b, answer)
            except Exception as exc:  # noqa: BLE001 - every rejected/failed reply is retained without secret exception text.
                answer, explanation, error = None, None, type(exc).__name__
            return {k: request[k] for k in ("case_id", "orientation", "request_hash")} | {
                "answer": answer, "explanation": explanation, "error_type": error,
                "raw_receipt": _attempt_receipt(directory, request["request_hash"])}

        attempts, fatal = [], False
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=5) as pool:
            for offset in range(0, 20, 5):
                batch = list(pool.map(execute, frozen["requests"][offset:offset + 5]))
                attempts.extend(batch)
                failures = [failure for r in batch for failure in r["raw_receipt"].get("failures", [])]
                fatal = (any(f.get("http_status") in (401, 403, 404) for f in failures)
                         or sum(f.get("http_status") in (400, 422)
                                and f.get("http_error_classification", {}).get("code") in
                                ("schema_rejected", "schema_keyword_unsupported") for f in failures) >= 2)
                print(json.dumps({"submitted": len(attempts), "accepted": sum(r["answer"] is not None for r in attempts),
                                  "fatal_transport_failure": fatal}), flush=True)
                if fatal:
                    break
        after = _read(cache / "usage.json")
        preserved = all((cache / name).exists() and digest((cache / name).read_bytes().hex()) == value for name, value in historical.items())
        budget_ok = (after.get("pair", 0) - before.get("pair", 0) == len(attempts)
                     and all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade")))
        gates = grounded_gates(attempts, frozen["cases"])
        report = {"manifest_hash": frozen["manifest_hash"], "attempts": attempts, "gates": gates,
                  "state": "robustness_passed_needs_review" if gates["passed"] and preserved and budget_ok else "diagnostic_inconclusive_or_failed",
                  "fatal_transport_failure": fatal, "ledger_before": before, "ledger_after": after,
                  "budget_preserved": budget_ok, "historical_cache_bytes_preserved": preserved,
                  "elapsed_seconds": time.monotonic() - start, "factual_review": "pending",
                  "training_started": False, "teacher_promoted": False, "activation_allowed": False,
                  "quality_gate_passed": False}
        report["report_hash"] = digest(report)
        _seal(target / "report.json", report)
        return report


def main() -> None:
    """Run or replay the new bounded contract diagnostic within the unchanged global pair ceiling."""
    parser = argparse.ArgumentParser(description="Grounded Qwen contract robustness diagnostic")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--explicit-policy", action="store_true", help="Use separately frozen, newly blind-reviewed preference variants")
    args = parser.parse_args()
    report = run_grounded_pilot(args.directory, explicit_policy=args.explicit_policy)
    print(json.dumps({"state": report["state"], "gates": {k: v for k, v in report["gates"].items() if k != "pairs"},
                      "ledger_after": report["ledger_after"]}), flush=True)


if __name__ == "__main__":
    main()
