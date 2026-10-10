"""Operator MVP status assignment for the packaged R0 artifact.

Operator decision (Mike, 2026-10-08): recalibrate the activation gate and close
the reranker workstream with the packaged artifact c18f4bc1 at status
"mvp-prod-ready" - a working hackathon-MVP variant, explicitly not a
production certification.

Why the old gate was replaced, in one paragraph: the 0.02 NDCG threshold
exceeded the perfect-ordering ceiling of BOTH evaluation instruments (0.0041 on
the sealed v9 golden, 0.0130 on the pre-registered hard benchmark), so it was
unattainable by construction. The revised gate keeps the safety floors (no
sealed-corpus regression, slice safety) and demands a real measured gain
(>= 0.01 NDCG, roughly three quarters of the hard benchmark's perfect-ordering
ceiling) on the independent instrument, plus both completed human review legs.

Honesty boundaries:
- The status receipt recomputes every gate row from SEALED evidence files
  (hash-verified); nothing is hand-typed.
- Historical sealed receipts (training gate passed=false, benchmark
  passed=false) are preserved as history and quoted as-is; this module does
  not rewrite them.
- Runtime code and the served artifact are unchanged; the artifact remains in
  its audited manual sort-only rollout.
- The status is a hackathon-MVP operator label, not a production certification.
"""

import argparse
import json
from pathlib import Path

from matching_engine.rerank_diagnostics import _read, _seal
from matching_engine.rerank_hash import digest

VERSION = "rerank-mvp-status-v1"
STATUS = "mvp-prod-ready"
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "r0-c18f4bc1.json"
HARD_BENCHMARK = "rerank-hard-benchmark-v7"
REPAIR_NAMESPACE = "rerank-grounded-pair-repair-v4"
DECIDED_BY = "Mike (operator), 2026-10-08"

# Revised thresholds (operator decision 2026-10-08).
GOLDEN_NDCG_DELTA_MIN = 0.0
GOLDEN_NDCG_CI_LOW_MIN = -0.005
GOLDEN_PAIR_MIN = 0.65
GOLDEN_PAIR_DELTA_MIN = 0.03
HARD_NDCG_DELTA_MIN = 0.01
HARD_NDCG_CI_LOW_MIN = 0.0
HARD_SLICE_NDCG_MIN = -0.03
USER_AGREEMENTS_MIN = 15

RATIONALE = (
    "Old activation gate demanded macro NDCG delta >= 0.02 with positive "
    "bootstrap bounds. Both evaluation instruments cannot express that gain even "
    "for a perfect ordering: sealed v9 golden ceiling 0.0041 (main reaches "
    "NDCG=1.0 on 8 of 12 families), pre-registered hard benchmark ceiling "
    "0.0130 (same-grade close calls cap listwise headroom). The recalibrated "
    "gate requires a measured gain of at least 0.01 NDCG on the independent "
    "hard benchmark (about 77 percent of its ceiling, 4x the sealed-corpus "
    "delta the artifact actually achieves), keeps no-regression floors on the "
    "sealed corpus, keeps supported-slice safety, and requires both human "
    "review legs without unresolved factual issues. Pair accuracy on hard "
    "close-call families stays a diagnostic, not a gate: teacher swap audits "
    "there are 26/36, so the pairwise signal sits at the teacher noise floor.")

LIMITATIONS = (
    "Hackathon-MVP working variant, not production: labels are synthetic "
    "teacher judgments (real employer preferences unvalidated); pair accuracy "
    "on close-call families is 0.635 with bootstrap low -0.091 (teacher-noise "
    "dominated); the artifact is sort-only with no tail cut; the tail gate was "
    "never calibrated (insufficient relevant support); no production, legal or "
    "152-FZ operator processes are certified by this status.")


def _verified_report(path: Path) -> dict:
    """Load a sealed receipt and verify its self-reported report_hash."""
    value = _read(path)
    if value.get("report_hash") != digest({k: v for k, v in value.items() if k != "report_hash"}):
        raise ValueError("sealed receipt hash mismatch: " + path.name)
    return value


def collect_evidence(directory: Path) -> dict:
    """Load and hash-verify every sealed evidence file for the status decision."""
    artifact = _read(ARTIFACT_PATH)
    expected_hash = digest({k: v for k, v in artifact.items() if k != "content_hash"})
    if artifact.get("content_hash") != expected_hash:
        raise ValueError("packaged artifact integrity mismatch")
    golden = artifact.get("gate")
    if not isinstance(golden, dict):
        raise TypeError("packaged artifact lacks its sealed golden gate")
    evaluation = _verified_report(directory / HARD_BENCHMARK / "evaluation.json")
    reviews_dir = directory / REPAIR_NAMESPACE / "reviews"
    user = _verified_report(next(reviews_dir.glob("user-comparison-*.json")))
    assistant = _verified_report(next(reviews_dir.glob("assistant-comparison-*.json")))
    slices = [row.get("ndcg_delta", 0.0) for row in evaluation.get("supported_slices", [])]
    return {
        "artifact": {"content_hash": artifact["content_hash"],
                     "model_version": artifact.get("model_version"),
                     "dataset_hash": artifact.get("dataset_hash"),
                     "historical_gate_passed": artifact["gate"].get("passed") is True},
        "golden": {"macro_ndcg_delta": golden["macro_ndcg_delta"],
                   "ndcg_delta_ci_low": golden["ndcg_delta_ci_low"],
                   "macro_pair_accuracy": golden["macro_pair_accuracy"],
                   "macro_pair_delta": golden["macro_pair_delta"],
                   "historical_ceiling": 0.004110},
        "hard": {"macro_ndcg_delta": evaluation["macro_ndcg_delta"],
                 "ndcg_delta_ci_low": evaluation["ndcg_delta_ci_low"],
                 "macro_pair_accuracy": evaluation["macro_pair_accuracy"],
                 "macro_pair_delta": evaluation["macro_pair_delta"],
                 "min_slice_ndcg_delta": min(slices) if slices else 0.0,
                 "oracle_headroom": evaluation.get("oracle_headroom", {})
                 .get("macro_max_attainable_ndcg_delta"),
                 "manifest_hash": evaluation.get("benchmark", {}).get("manifest_hash"),
                 "dataset_hash": evaluation.get("benchmark", {}).get("dataset_hash")},
        "assistant": {"agreements": assistant["agreements"],
                      "unresolved_factual_issues": assistant["unresolved_factual_issues"],
                      "review_hash": assistant["review_hash"]},
        "user": {"agreements": user["agreements"],
                 "unresolved_factual_issues": user["unresolved_factual_issues"],
                 "review_hash": user["review_hash"]},
    }


def evaluate_gate(evidence: dict) -> tuple[list[dict], bool]:
    """Compute the recalibrated mvp-prod-ready gate rows from sealed numbers."""
    golden = evidence["golden"]
    hard = evidence["hard"]
    rows = [
        {"id": "G1-sealed-no-regression",
         "rule": "golden macro NDCG delta >= 0.0 and CI low >= -0.005",
         "value": [golden["macro_ndcg_delta"], golden["ndcg_delta_ci_low"]],
         "passed": (golden["macro_ndcg_delta"] >= GOLDEN_NDCG_DELTA_MIN
                    and golden["ndcg_delta_ci_low"] >= GOLDEN_NDCG_CI_LOW_MIN)},
        {"id": "G2-sealed-pair-quality",
         "rule": "golden macro pair accuracy >= 0.65 and delta >= 0.03",
         "value": [golden["macro_pair_accuracy"], golden["macro_pair_delta"]],
         "passed": (golden["macro_pair_accuracy"] >= GOLDEN_PAIR_MIN
                    and golden["macro_pair_delta"] >= GOLDEN_PAIR_DELTA_MIN)},
        {"id": "G3-hard-measured-gain",
         "rule": "hard benchmark macro NDCG delta >= 0.01 and CI low >= 0.0",
         "value": [hard["macro_ndcg_delta"], hard["ndcg_delta_ci_low"]],
         "passed": (hard["macro_ndcg_delta"] >= HARD_NDCG_DELTA_MIN
                    and hard["ndcg_delta_ci_low"] >= HARD_NDCG_CI_LOW_MIN)},
        {"id": "G4-hard-slice-safety",
         "rule": "hard benchmark minimum supported-slice NDCG delta >= -0.03",
         "value": hard["min_slice_ndcg_delta"],
         "passed": hard["min_slice_ndcg_delta"] >= HARD_SLICE_NDCG_MIN},
        {"id": "G5-human-review",
         "rule": ("assistant 20/20 agreement, user agreements >= 15, "
                  "zero unresolved factual issues on both legs"),
         "value": [evidence["assistant"]["agreements"],
                   evidence["user"]["agreements"],
                   evidence["assistant"]["unresolved_factual_issues"],
                   evidence["user"]["unresolved_factual_issues"]],
         "passed": (evidence["assistant"]["agreements"] == 20
                    and evidence["user"]["agreements"] >= USER_AGREEMENTS_MIN
                    and evidence["assistant"]["unresolved_factual_issues"] == 0
                    and evidence["user"]["unresolved_factual_issues"] == 0)},
    ]
    return rows, all(row["passed"] for row in rows)


def build_status(evidence: dict) -> dict:
    """Build the sealed status payload from verified evidence."""
    rows, passed = evaluate_gate(evidence)
    return {
        "version": VERSION,
        "status": STATUS if passed else "blocked",
        "gate_passed": passed,
        "decided_by": DECIDED_BY,
        "artifact": evidence["artifact"],
        "gate": {"version": "mvp-prod-ready-gate-v1", "rows": rows,
                 "thresholds": {"golden_ndcg_delta_min": GOLDEN_NDCG_DELTA_MIN,
                                "golden_ndcg_ci_low_min": GOLDEN_NDCG_CI_LOW_MIN,
                                "golden_pair_min": GOLDEN_PAIR_MIN,
                                "golden_pair_delta_min": GOLDEN_PAIR_DELTA_MIN,
                                "hard_ndcg_delta_min": HARD_NDCG_DELTA_MIN,
                                "hard_ndcg_ci_low_min": HARD_NDCG_CI_LOW_MIN,
                                "hard_slice_ndcg_min": HARD_SLICE_NDCG_MIN,
                                "user_agreements_min": USER_AGREEMENTS_MIN}},
        "recalibration_rationale": RATIONALE,
        "limitations": LIMITATIONS,
        "runtime_changed": False,
        "training_started": False,
        "activation_allowed_note": ("artifact stays in its audited manual sort-only "
                                    "rollout; this status is an operator label, "
                                    "not a promotion"),
    }


def assign(directory: Path) -> dict:
    """Seal the status receipt; idempotent, recomputes from sealed evidence."""
    target = directory / VERSION
    target.mkdir(parents=True, exist_ok=True)
    status_path = target / "status.json"
    evidence = collect_evidence(directory)
    payload = build_status(evidence)
    payload["evidence"] = {
        "artifact_path": str(ARTIFACT_PATH.relative_to(Path(__file__).parent.parent)),
        "hard_benchmark_evaluation": HARD_BENCHMARK + "/evaluation.json",
        "user_review": REPAIR_NAMESPACE + "/reviews/user-comparison-"
                       + evidence["user"]["review_hash"][:12] + "…json",
        "assistant_review": REPAIR_NAMESPACE + "/reviews/assistant-comparison-"
                            + evidence["assistant"]["review_hash"][:12] + "…json",
    }
    payload["payload_hash"] = digest({k: v for k, v in payload.items() if k != "payload_hash"})
    _seal(status_path, payload)
    return payload


def verify(directory: Path) -> dict:
    """Recompute the gate and compare with the sealed status receipt."""
    sealed = _read(directory / VERSION / "status.json")
    if sealed.get("payload_hash") != digest({k: v for k, v in sealed.items() if k != "payload_hash"}):
        raise ValueError("sealed status payload hash mismatch")
    recomputed = build_status(collect_evidence(directory))
    for key, value in recomputed.items():
        if sealed.get(key) != value:
            raise ValueError("sealed status disagrees with current evidence: " + key)
    return {"verified": True, "status": sealed["status"], "gate_passed": sealed["gate_passed"]}


def main() -> None:
    """Assign or verify the operator status; offline, no credentials."""
    parser = argparse.ArgumentParser(description="Operator MVP status for packaged R0")
    parser.add_argument("command", choices=("assign", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    if args.command == "assign":
        payload = assign(args.directory)
        print(json.dumps({k: payload[k] for k in ("version", "status", "gate_passed",
                                                  "decided_by")}, ensure_ascii=False))
        for row in payload["gate"]["rows"]:
            print(row["id"], "PASS" if row["passed"] else "FAIL", json.dumps(row["value"]))
    else:
        print(json.dumps(verify(args.directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
