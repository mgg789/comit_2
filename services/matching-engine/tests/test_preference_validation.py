"""Offline tests for the synthetic employer preference validation harness."""

import json
import math
from pathlib import Path

from matching_engine.preference_validation import (
    ADJUSTMENT_CLIP_BOUND,
    BUDGETS,
    CONVERGENCE_NDCG_BAR,
    HOLDOUT_EVERY,
    SELECTED_FEATURES,
    VERSION,
    _macro,
    _rng,
    hidden_labels,
    reaction_stream,
    standardize,
    true_score,
)


def _row(family_id: str, index: int, value: float, available: bool = True) -> dict:
    return {"family_id": family_id, "index": index,
            "snapshot": {"names": tuple(SELECTED_FEATURES),
                         "values": tuple(0.5 if name != "stack.proximity" else value
                                         for name in SELECTED_FEATURES),
                         "available": tuple(name != "semantic_cosine" or available
                                            for name in SELECTED_FEATURES),
                         "schema_hash": "0" * 64},
            "baseline_score": 0.5, "candidate_key": family_id + "/" + str(index)}


def test_standardization_uses_only_observed_values() -> None:
    """Unobserved features never enter means/std; observed get sane stats."""
    pool = [_row("fam", index, 0.2 + index * 0.1, available=False) for index in range(7)]
    pool.append(_row("fam", 7, 0.9))
    stats = standardize(pool)
    entry = stats["stack.proximity"]
    assert entry["observed"] == 8
    assert entry["std"] > 0
    assert stats["semantic_cosine"]["observed"] == 1


def test_true_score_ignores_missing_and_scales_by_z() -> None:
    """Policy score is a weighted z-sum; unobserved features contribute nothing."""
    pool = [_row("fam", index, 0.2 + index * 0.1) for index in range(7)]
    stats = standardize(pool)
    weights = {"stack.proximity": 1.0, "semantic_cosine": 5.0}
    low = true_score(pool[0], weights, stats)
    high = true_score(pool[-1], weights, stats)
    assert high > low
    z = (pool[-1]["snapshot"]["values"][SELECTED_FEATURES.index("stack.proximity")]
         - stats["stack.proximity"]["mean"]) / stats["stack.proximity"]["std"]
    assert math.isclose(high, z, rel_tol=1e-9)


def test_hidden_labels_mark_top_three() -> None:
    """Exactly the top-3 by policy score per family get label 1."""
    pool = [_row("fam", index, 0.1 * index) for index in range(7)]
    stats = standardize(pool)
    labels = hidden_labels(pool, ["fam"], {"stack.proximity": 1.0}, stats)
    assert sum(labels.values()) == 3
    assert labels["fam/6"] == 1 and labels["fam/0"] == 0


def test_reaction_stream_is_deterministic_and_flips_rarely() -> None:
    """Same seed reproduces the stream; flips stay near the configured rate."""
    pool = [_row("fam", index, 0.1 * index) for index in range(7)]
    pool += [_row("fam2", index, 0.1 * index) for index in range(7)]
    stats = standardize(pool)
    labels = hidden_labels(pool, ["fam", "fam2"], {"stack.proximity": 1.0}, stats)
    first = reaction_stream(pool, ["fam", "fam2"], labels, kind="signal",
                            rng=_rng("coverage_first"))
    second = reaction_stream(pool, ["fam", "fam2"], labels, kind="signal",
                             rng=_rng("coverage_first"))
    assert first == second
    flips = sum(1 for row in first if row["label"] != labels[row["candidate_key"]])
    assert flips <= 2


def test_macro_aggregates_and_gains() -> None:
    """Macro averaging reports learned-vs-baseline gains and bounded adjustments."""
    family = {"learned": {"pair_accuracy": 0.8, "ndcg_at_10": 0.9},
              "baseline": {"pair_accuracy": 0.6, "ndcg_at_10": 0.8},
              "mean_abs_adjustment": 0.1, "max_abs_adjustment": 0.2}
    macro = _macro([family, family])
    assert math.isclose(macro["ndcg_gain"], 0.1)
    assert math.isclose(macro["pair_gain"], 0.2)
    assert math.isclose(macro["max_abs_adjustment"], 0.2)


def test_constants_are_frozen_and_declared() -> None:
    """Pre-registered constants stay in their declared ranges."""
    assert VERSION.startswith("preference-validation-v")
    assert BUDGETS[0] >= 8 and list(BUDGETS) == sorted(BUDGETS)
    assert 0.0 < CONVERGENCE_NDCG_BAR < 0.05
    assert ADJUSTMENT_CLIP_BOUND == 0.25
    assert HOLDOUT_EVERY == 3
    module = Path(__file__).parents[1] / "src" / "matching_engine" / "preference_validation.py"
    text = module.read_text(encoding="utf-8")
    assert "evaluation-only" in text
    json.dumps({"budgets": list(BUDGETS), "clip": ADJUSTMENT_CLIP_BOUND})
