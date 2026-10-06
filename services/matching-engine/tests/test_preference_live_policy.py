"""Frozen activation and scheduling policy boundaries."""

import json
from pathlib import Path

import pytest
from matching_engine.preference_live_policy import (
    FOLLOWUP_ACTION_THRESHOLD,
    INITIAL_ACTION_THRESHOLD,
    LiveActivationDecision,
    ScheduleDecision,
    activation_gate,
    assess_live_model,
    policy_hash,
    schedule_live_training,
)
from matching_engine.preference_model import FeatureSnapshot, train

ARTIFACT_PATH = Path(__file__).parents[1] / "src/matching_engine/artifacts/r0-c18f4bc1.json"


@pytest.fixture(scope="module")
def artifact() -> dict:
    """Load the repository's sealed full baseline."""
    return json.loads(ARTIFACT_PATH.read_text())


def _snapshot(artifact: dict, grade: float) -> dict:
    """Create a full source-schema feature snapshot."""
    values = list(artifact["means"])
    values[artifact["feature_names"].index("grade")] = grade
    return FeatureSnapshot(names=tuple(artifact["feature_names"]), values=tuple(values),
                           available=tuple(True for _ in values),
                           schema_hash=artifact["feature_schema_hash"]).model_dump()


def _model(artifact: dict):
    """Fit a deterministic balanced candidate model with enough observations."""
    rows = []
    for index in range(24):
        label = index % 2
        rows.append({"features": _snapshot(artifact, float(label)), "baseline_score": 0.0,
                     "label": label, "candidate_key": f"candidate-{index}"})
    return train(rows, artifact)


def _part(*, pair_gain: float = 0.05, ndcg_gain: float = 0.0, logloss_delta: float = -0.01) -> dict:
    """Build one evaluator holdout partition with an actual opposite-label pair."""
    return {
        "n": 8,
        "classes": {"0": 4, "1": 4},
        "baseline": {"pair_accuracy": 0.5, "ndcg_at_10": 0.8, "logloss": 0.7, "pair_count": 16},
        "personalized": {"pair_accuracy": 0.5 + pair_gain, "ndcg_at_10": 0.8 + ndcg_gain,
                          "logloss": 0.7 + logloss_delta, "pair_count": 16},
        "bootstrap": {"mean": logloss_delta, "ci95_low": logloss_delta - 0.01,
                      "ci95_high": logloss_delta + 0.01},
    }


def _report(artifact: dict, model, *, source: str = "operational", part: dict | None = None) -> dict:
    """Build the actual evaluator report shape consumed by the policy."""
    return {
        "status": "shadow_test_only", "source": source, "baseline_hash": artifact["content_hash"],
        "contexts": {"ctx": {"fit": {"train_rows": 24, "diagnostics": {"converged": True}},
                               "validation": part or _part(), "golden": part or _part()}},
        "model_full_json": {"ctx": model.model_dump()},
        "bootstrap": {"contexts": {"ctx": {"validation": {}, "golden": {}}}},
    }


def test_eligible_requires_actual_non_degrading_holdouts_and_positive_gain(artifact: dict) -> None:
    """A converged trusted model passes only with real pair and NDCG metrics."""
    model = _model(artifact)
    decision = assess_live_model(_report(artifact, model), model, artifact)
    assert decision.status == "eligible"
    assert decision.activate is True
    assert decision.reasons == []
    assert decision.uncertainty["interpretation"].startswith("descriptive_")
    assert decision.decision_hash
    assert LiveActivationDecision.model_validate(decision.model_dump()) == decision


def test_synthetic_is_rejected_by_default_but_explicit_test_contour_is_allowed(artifact: dict) -> None:
    """Synthetic feedback cannot activate production policy without explicit opt in."""
    model = _model(artifact)
    report = _report(artifact, model, source="synthetic")
    assert assess_live_model(report, model, artifact).status == "fallback"
    assert assess_live_model(report, model, artifact, allow_synthetic=True).status == "eligible"


def test_store_activation_gate_adapts_one_context_and_returns_stable_contract(artifact: dict) -> None:
    """The parent store's compact call keeps the full policy checks intact."""
    model = _model(artifact)
    report = _report(artifact, model)
    result = activation_gate(report["contexts"]["ctx"], report["model_full_json"]["ctx"], artifact,
                             source="operational")
    assert result == {"passed": True, "reasons": [], "policy_hash": policy_hash()}

    missing_model = activation_gate(report["contexts"]["ctx"], None, artifact, source="operational")
    assert missing_model["passed"] is False
    assert missing_model["policy_hash"] == policy_hash()


def test_regressions_missing_pairs_and_model_hash_fall_back(artifact: dict) -> None:
    """Ranking regressions, absent raw pairs and report/model mismatch never activate."""
    model = _model(artifact)
    regression = _part(pair_gain=-0.01)
    decision = assess_live_model(_report(artifact, model, part=regression), model, artifact)
    assert decision.status == "fallback"
    assert any("ranking_regression" in reason for reason in decision.reasons)

    no_pairs = _part()
    no_pairs["baseline"]["pair_count"] = 0
    no_pairs["personalized"]["pair_count"] = 0
    decision = assess_live_model(_report(artifact, model, part=no_pairs), model, artifact)
    assert "ctx:validation_ranking_pairs_missing" in decision.reasons

    report = _report(artifact, model)
    report["model_full_json"]["ctx"] = {**report["model_full_json"]["ctx"], "content_hash": "0" * 64}
    assert assess_live_model(report, model, artifact).status == "fallback"


def test_needs_more_data_has_two_class_and_minimum_boundaries(artifact: dict) -> None:
    """The evaluator minimums remain a distinct needs-more-data state."""
    model = _model(artifact)
    report = _report(artifact, model)
    report["contexts"]["ctx"]["fit"]["train_rows"] = 23
    report["contexts"]["ctx"]["validation"]["n"] = 7
    decision = assess_live_model(report, model, artifact)
    assert decision.status == "needs_more_data"
    assert any("rows<" in reason for reason in decision.reasons)


def test_schedule_counts_unique_new_actions_and_resets_on_revision_or_baseline(artifact: dict) -> None:
    """Initial 40 and follow-up 16 unique actions are frozen trigger boundaries."""
    initial = schedule_live_training([f"a-{i}" for i in range(INITIAL_ACTION_THRESHOLD)])
    assert initial.trigger and initial.threshold == INITIAL_ACTION_THRESHOLD
    follow = schedule_live_training([f"a-{i}" for i in range(16)], [f"a-{i}" for i in range(8)], attempt=1)
    assert not follow.trigger and follow.threshold == FOLLOWUP_ACTION_THRESHOLD and follow.unique_new_count == 8

    # The previous eight are reused; only eight genuinely new actions exist.
    follow = schedule_live_training([f"a-{i}" for i in range(24)], [f"a-{i}" for i in range(8)], attempt=1)
    assert follow.unique_new_count == 16 and follow.trigger
    reset = schedule_live_training(["new"], ["old"], attempt=3, vacancy_revision=2,
                                   previous_vacancy_revision=1, baseline_hash=artifact["content_hash"],
                                   previous_baseline_hash=artifact["content_hash"])
    assert reset.reset is True and reset.threshold == INITIAL_ACTION_THRESHOLD and not reset.trigger
    changed_r0 = schedule_live_training(["new"], ["old"], attempt=3, baseline_hash="new-r0",
                                         previous_baseline_hash=artifact["content_hash"])
    assert changed_r0.reset is True
    assert ScheduleDecision.model_validate(changed_r0.model_dump()) == changed_r0
