"""Real local sklearn fitting, group-held-out gates and portable inference parity."""

from dataclasses import replace
from itertools import combinations

import pytest
from matching_engine.rerank_features import FeatureSchema, FeatureVector
from matching_engine.rerank_model import (
    RerankArtifact,
    promotion_eligible,
    scaled_features,
)
from matching_engine.rerank_training import (
    TrainingFamily,
    calibrate_tail,
    evaluate,
    validate_families,
)
from matching_engine.rerank_training import (
    train as fit,
)

SCHEMA = FeatureSchema(("skill", "optional", "available.optional"), "synthetic-feature-hash", "synthetic-strict-hash")


def train(families, schema, metadata):
    """Mathematical test fixtures cannot satisfy or activate the production protocol."""
    return fit(families, schema, {"require_protocol": False, **metadata})


def family(identifier, split, direction="backend", count=4):
    """Simple teacher-independent mathematical fixture; only tests use this source."""
    vectors = [FeatureVector(SCHEMA.names, (i / 3, 100 if i == 0 else i / 3, float(i != 0)),
                              (True, i != 0, True), SCHEMA.schema_hash) for i in range(count)]
    return TrainingFamily(identifier, direction, split, vectors, list(reversed(range(count))),
                          [min(i, 3) for i in range(count)],
                          [(a, b, "B") for a, b in combinations(range(count), 2)])


def dataset():
    return [family("train", "train"), family("val", "validation"), family("cal", "calibration"),
            *(family(f"gold-{i}", "golden") for i in range(12))]


def test_real_fit_reproduces_portable_runtime_scaling_and_defaults_unapproved():
    result = train(dataset(), SCHEMA, {})
    again = train(dataset(), SCHEMA, {})
    assert result["content_hash"] == again["content_hash"]
    assert result["training"]["independent_training_pairs"] == 6
    assert result["training"]["training_rows"] == 12
    assert result["training"]["intercept"] is False
    assert result["means"][1] == pytest.approx(2 / 3)  # missing placeholder 100 is excluded
    assert result["tail_gap"] is None and not result["gate"]["tail"]["validated"]
    assert not result["gate"]["passed"] and not promotion_eligible(result)
    model = RerankArtifact.model_validate(result)
    scores = [sum(c * x for c, x in zip(model.coefficients, scaled_features(vector, model), strict=True))
              for vector in dataset()[0].vectors]
    assert scores == sorted(scores)
    assert scaled_features(dataset()[0].vectors[0], model)[1] == 0
    assert result["gate"]["macro_ndcg_delta"] > .02
    assert result["gate"]["macro_pair_delta"] > .03
    assert result["gate"]["ndcg_delta_ci_low"] > 0
    assert result["gate"]["pair_delta_ci_low"] > 0


def test_label_validation_gate_is_separate_from_absent_human_reviews():
    result = train(dataset(), SCHEMA, {"label_validation_passed": True})
    assert result["gate"]["passed"] is False  # incomplete test protocol cannot be promoted
    assert result["reviews"]["user_pairs"] == 0 and result["reviews"]["assistant_pairs"] == 0
    assert not promotion_eligible(result)


def test_golden_cannot_select_regularization_or_normalization():
    first = train(dataset(), SCHEMA, {})
    altered = [replace(f, grades=list(reversed(f.grades)),
                       pairs=[(a, b, "A") for a, b, _ in f.pairs]) if f.split == "golden" else f
               for f in dataset()]
    second = train(altered, SCHEMA, {})
    assert first["training"]["C"] == second["training"]["C"]
    assert first["coefficients"] == second["coefficients"]
    assert first["means"] == second["means"] and first["scales"] == second["scales"]
    assert second["gate"]["macro_pair_accuracy"] == 0


def test_ties_and_insufficient_are_logged_but_not_mirrored_as_binary():
    families = dataset()
    families[0] = replace(families[0], pairs=[(0, 1, "tie"), (0, 2, "insufficient"), (0, 3, "B")])
    result = train(families, SCHEMA, {})
    assert result["training"]["independent_training_pairs"] == 1
    assert result["training"]["training_rows"] == 2


@pytest.mark.parametrize("modifier,message", [
    (lambda f: [*f, f[0]], "duplicate"),
    (lambda f: [replace(f[0], source_id="shared"), replace(f[1], source_id="shared"), *f[2:]], "ancestry"),
    (lambda f: [replace(f[0], pairs=[(0, 1, "B"), (1, 0, "A")]), *f[1:]], "duplicate independent"),
    (lambda f: [replace(f[0], pairs=[(0, 99, "B")]), *f[1:]], "indices"),
    (lambda f: [replace(f[0], grades=[0, 1, 2, 9]), *f[1:]], "grades"),
])
def test_split_and_label_integrity_rejects_leakage_or_corruption(modifier, message):
    with pytest.raises(ValueError, match=message):
        validate_families(modifier(dataset()), SCHEMA)


def test_failed_support_or_unknown_grades_do_not_earn_success():
    families = [family("g", "golden")]
    result = evaluate(families, {"g": [0, 1, 2, 3]})
    assert not result["passed"]
    unknown = replace(families[0], grades=[None] * 4)
    assert evaluate([unknown], {"g": [0, 1, 2, 3]})["ndcg_families"] == 0
    with pytest.raises(ValueError, match="tuning cycle"):
        train(dataset(), SCHEMA, {"tuning_cycles": 3})
    invalid_val = [replace(f, grades=[None] * 4) if f.split == "validation" else f for f in dataset()]
    with pytest.raises(ValueError, match="independent"):
        train(invalid_val, SCHEMA, {})


def test_direct_training_requires_sealed_protocol_and_boolean_masks():
    with pytest.raises(ValueError, match="sealed family protocol"):
        fit(dataset(), SCHEMA, {})
    families = dataset()
    vector = replace(families[0].vectors[0], available=(True, "false", True))
    families[0] = replace(families[0], vectors=[vector, *families[0].vectors[1:]])
    with pytest.raises(ValueError, match="schema/dimensions"):
        validate_families(families, SCHEMA)


def test_tail_only_enables_with_calibration_and_golden_relevant_support():
    calibration = replace(family("c", "calibration", count=55), grades=[3] * 55)
    golden = replace(family("g", "golden", count=55), grades=[3] * 55)
    scores = {"c": [0.] * 55, "g": [0.] * 55}
    safe = calibrate_tail([calibration], [golden], scores)
    assert safe["gap"] == 0 and safe["golden_wilson_low"] >= .90
    assert safe["cohort"] == "reranked_main_top_k"
    scores["g"] = [0.] * 54 + [1.]
    unsafe = calibrate_tail([calibration], [golden], scores)
    assert unsafe["gap"] is None and unsafe["reason"] == "golden_tail_safety_failed"
    assert calibrate_tail([], [], {})["gap"] is None


def test_supported_slice_decline_blocks_even_with_other_strong_families():
    families = [family(f"g-{i}", "golden", direction="frontend" if i < 3 else "backend") for i in range(15)]
    # Regress the supported frontend slice against a strong main baseline.
    families = [replace(f, main_scores=[0, 1, 2, 3]) if f.direction == "frontend" else f for f in families]
    scores = {f.family_id: ([3, 2, 1, 0] if f.direction == "frontend" else [0, 1, 2, 3]) for f in families}
    report = evaluate(families, scores)
    assert not report["passed"]
    assert any(s["slice"] == "frontend" and s["pair_delta"] < -.03 for s in report["supported_slices"])
