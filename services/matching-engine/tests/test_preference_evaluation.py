"""Offline, train-only quality checks for vacancy preference shadow reports."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from matching_engine import preference_evaluation as evaluation
from matching_engine.rerank_model import validate_artifact

BASELINE = validate_artifact(json.loads(files("matching_engine").joinpath("artifacts/r0-c18f4bc1.json").read_text()))
GRADE_INDEX = BASELINE["feature_names"].index("grade")


def row(index: int, label: int, *, split: str = "train") -> dict:
    """Build one minimal sealed observation."""
    values = list(BASELINE["means"])
    values[GRADE_INDEX] += float(label)
    features = {"names": BASELINE["feature_names"], "values": values,
                "available": [True] * len(values), "schema_hash": BASELINE["feature_schema_hash"]}
    offset = {"train": 0, "validation": 100, "golden": 200}[split]
    return {"features": features, "baseline_score": 0.0,
            "label": label, "candidate_key": f"{split}-{index}",
            "decision_at": f"2026-01-01T00:{(offset + index) // 60:02d}:{(offset + index) % 60:02d}Z"}


def context(train: int = 24, validation: int = 8, golden: int = 8) -> dict:
    """Build balanced disjoint temporal partitions."""
    return {"identity": {"owner": "owner-a", "vacancy": "vacancy-a"},
            "train": [row(index, index % 2) for index in range(train)],
            "validation": [row(index, index % 2, split="validation") for index in range(validation)],
            "golden": [row(index, index % 2, split="golden") for index in range(golden)]}


def test_cold_start_is_explicit_and_never_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A context below every minimum emits needs_more_data with no model."""
    called = False

    def unavailable(*args, **kwargs) -> object:
        nonlocal called
        called = True
        raise AssertionError("fit must not run for insufficient data")

    monkeypatch.setattr(evaluation, "fit_preference_model", unavailable)
    # One row below the live-policy train minimum (14 since the 30-reaction
    # first-fit decision, issue #11): the evaluator must still refuse to fit.
    report = evaluation.evaluate({"cold": context(train=evaluation.MINIMUMS["train"] - 1)},
                                 BASELINE, "synthetic", "dataset", "split")
    assert report["status"] == "needs_more_data"
    assert report["contexts"]["cold"]["status"] == "needs_more_data"
    assert report["contexts"]["cold"]["fit"] is None
    assert not called
    assert report["mode"] == "SHADOW" and report["test_only"] is True


def test_fit_receives_train_labels_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker sees exactly train labels; validation/golden remain held out."""
    observed: list[int] = []
    real_fit = evaluation.fit_preference_model

    def fit(rows, baseline_artifact):
        observed.extend(item["label"] for item in rows)
        return real_fit(rows, baseline_artifact)

    monkeypatch.setattr(evaluation, "fit_preference_model", fit)
    report = evaluation.evaluate({"c": context()}, BASELINE, "synthetic", "dataset", "split")
    assert report["fit_performed"] is True
    assert report["state"] == "shadow_test_only" and report["model_count"] == 1
    assert len(observed) == 24
    assert observed == [index % 2 for index in range(24)]
    assert report["contexts"]["c"]["validation"]["n"] == 8
    assert report["contexts"]["c"]["golden"]["n"] == 8


def test_metrics_are_separated_and_shadow_cannot_promote() -> None:
    """Baseline and learned metrics remain separate under a positive shadow result."""
    report = evaluation.evaluate({"c": context()}, BASELINE, "synthetic", "dataset", "split")
    validation = report["contexts"]["c"]["validation"]
    assert validation["baseline"] != validation["learned"]
    assert validation["learned"]["pair_accuracy"] == 1.0
    assert validation["learned"]["ndcg_at_10"] == 1.0
    assert report["live_promotion_allowed"] is False
    assert report["propensity"] == "unknown"
    assert report["activation"] == "test_only"


def test_candidate_key_cannot_cross_partitions() -> None:
    """A reused candidate identity is a hard split integrity error."""
    broken = context()
    broken["validation"][0]["candidate_key"] = broken["train"][0]["candidate_key"]
    with pytest.raises(ValueError, match="crosses"):
        evaluation.evaluate({"c": broken}, BASELINE, "synthetic", "dataset", "split")
