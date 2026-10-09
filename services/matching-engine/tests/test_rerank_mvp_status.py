"""Offline tests for the operator MVP status gate and payload."""

import json
from pathlib import Path

from matching_engine.rerank_mvp_status import (
    HARD_NDCG_DELTA_MIN,
    STATUS,
    USER_AGREEMENTS_MIN,
    VERSION,
    build_status,
    collect_evidence,
    evaluate_gate,
)


def _evidence(**overrides) -> dict:
    """Return the sealed-evidence shape that satisfies every gate row."""
    evidence = {
        "artifact": {"content_hash": "a" * 64, "model_version": "linear-pairwise",
                     "dataset_hash": "b" * 64, "historical_gate_passed": False},
        "golden": {"macro_ndcg_delta": 0.002647, "ndcg_delta_ci_low": 0.0,
                   "macro_pair_accuracy": 0.958643, "macro_pair_delta": 0.039815,
                   "historical_ceiling": 0.004110},
        "hard": {"macro_ndcg_delta": 0.010718, "ndcg_delta_ci_low": 0.0,
                 "macro_pair_accuracy": 0.635088, "macro_pair_delta": 0.005191,
                 "min_slice_ndcg_delta": 0.0, "oracle_headroom": 0.012955,
                 "manifest_hash": "c" * 64, "dataset_hash": "d" * 64},
        "assistant": {"agreements": 20, "unresolved_factual_issues": 0,
                      "review_hash": "e" * 64},
        "user": {"agreements": 17, "unresolved_factual_issues": 0,
                 "review_hash": "f" * 64},
    }
    for section, values in overrides.items():
        evidence[section].update(values)
    return evidence


def test_sealed_evidence_passes_every_row() -> None:
    """The packaged artifact's actual numbers satisfy the recalibrated gate."""
    rows, passed = evaluate_gate(_evidence())
    assert passed is True
    assert {row["id"] for row in rows} == {
        "G1-sealed-no-regression", "G2-sealed-pair-quality", "G3-hard-measured-gain",
        "G4-hard-slice-safety", "G5-human-review"}
    assert all(row["passed"] for row in rows)


def test_each_gate_rule_can_fail() -> None:
    """Every row has a working rejection boundary."""
    failures = [
        _evidence(golden={"macro_ndcg_delta": -0.001}),
        _evidence(golden={"macro_pair_accuracy": 0.60}),
        _evidence(hard={"macro_ndcg_delta": 0.009}),
        _evidence(hard={"min_slice_ndcg_delta": -0.04}),
        _evidence(user={"unresolved_factual_issues": 1}),
        _evidence(user={"agreements": 14}),
    ]
    for evidence in failures:
        rows, passed = evaluate_gate(evidence)
        assert passed is False
        assert sum(row["passed"] for row in rows) == 4


def test_status_payload_is_deterministic_and_labeled() -> None:
    """Same evidence gives a byte-stable payload; blocked evidence downgrades status."""
    first = build_status(_evidence())
    second = build_status(_evidence())
    assert first == second
    assert first["status"] == "mvp-prod-ready"
    assert first["gate_passed"] is True
    assert first["runtime_changed"] is False
    blocked = build_status(_evidence(hard={"macro_ndcg_delta": 0.0}))
    assert blocked["status"] == "blocked"
    assert blocked["gate_passed"] is False


def test_thresholds_and_boundaries_are_declared() -> None:
    """The recalibrated thresholds are explicit and consistent with evidence."""
    assert HARD_NDCG_DELTA_MIN == 0.01
    assert USER_AGREEMENTS_MIN == 15
    assert STATUS == "mvp-prod-ready"
    assert VERSION.startswith("rerank-mvp-status-")
    module = Path(__file__).parents[1] / "src" / "matching_engine" / "rerank_mvp_status.py"
    text = module.read_text(encoding="utf-8")
    assert "0.0041" in text and "0.0130" in text, "rationale must cite both ceilings"
    assert "not a production certification" in text
    json.dumps(build_status(_evidence()), ensure_ascii=False)


def test_collect_evidence_rejects_tampered_artifact(tmp_path: Path) -> None:
    """Integrity check refuses an artifact with a broken content hash."""
    import matching_engine.rerank_mvp_status as module

    original = module.ARTIFACT_PATH
    try:
        tampered = tmp_path / "artifact.json"
        payload = json.loads(original.read_text(encoding="utf-8"))
        payload["coefficients"] = [0.5]
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        module.ARTIFACT_PATH = tampered
        try:
            collect_evidence(tmp_path)
        except ValueError as error:
            assert "integrity" in str(error)
        else:
            raise AssertionError("tampered artifact accepted")
    finally:
        module.ARTIFACT_PATH = original
