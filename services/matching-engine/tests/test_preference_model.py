"""Offline contracts for the vacancy-local residual preference model."""

import json
import math
from pathlib import Path

import pytest
from matching_engine.preference_model import (
    SELECTED_FEATURES,
    FeatureSnapshot,
    PreferenceModel,
    apply,
    train,
)
from matching_engine.rerank_model import RerankArtifact, validate_artifact

ARTIFACT_PATH = Path(__file__).parents[1] / "src/matching_engine/artifacts/r0-c18f4bc1.json"


@pytest.fixture(scope="module")
def artifact() -> dict:
    """Load and validate the repository's packaged full R0 artifact."""
    payload = json.loads(ARTIFACT_PATH.read_text())
    RerankArtifact.model_validate(payload)
    return payload


def snapshot(artifact: dict, grade: float, *, missing: set[str] | None = None,
             names: tuple[str, ...] | None = None, schema: str | None = None) -> FeatureSnapshot:
    """Build a full source-schema snapshot with one selected signal changed."""
    source_names = tuple(names or artifact["feature_names"])
    values = [0.0] * len(source_names)
    values[source_names.index("grade")] = grade
    missing = missing or set()
    return FeatureSnapshot(names=source_names, values=tuple(values),
                           available=tuple(name not in missing for name in source_names),
                           schema_hash=schema or artifact["feature_schema_hash"])


def rows(artifact: dict) -> list[dict]:
    """Build eight unique, balanced examples with a grade preference."""
    result = []
    for index in range(8):
        grade = 1.0 if index >= 4 else 0.0
        result.append({"features": snapshot(artifact, grade).model_dump(), "baseline_score": 0.0,
                       "label": int(grade), "candidate_key": f"candidate-{index}"})
    return result


def test_fit_moves_in_preferred_direction_with_bounded_runtime_effect(artifact: dict) -> None:
    """Positive grade labels produce a bounded positive residual contribution."""
    model = train(rows(artifact), artifact)
    assert model.coefficients[0] > 0
    assert 0 < model.gamma <= 0.5
    high = apply(snapshot(artifact, 1.0), 0.0, model, artifact)
    low = apply(snapshot(artifact, 0.0), 0.0, model, artifact)
    assert high["score"] >= low["score"]
    assert abs(high["adjustment"]) <= model.baseline_scale * model.gamma * 0.5 + 1e-12
    assert sum(high["contributions"].values()) == pytest.approx(high["adjustment"])
    assert high["uncertainty"] >= 0 and math.isfinite(high["uncertainty"])
    assert high["confidence"] == model.trust


def test_cold_start_has_zero_delta_and_is_serialisable(artifact: dict) -> None:
    """Insufficient or single-class observations cannot change R0."""
    cold = train(rows(artifact)[:7], artifact)
    assert cold.diagnostics["cold_start"] is True
    assert cold.gamma == cold.trust == 0
    result = apply(snapshot(artifact, 1.0), 2.5, cold, artifact)
    assert result["score"] == pytest.approx(2.5)
    assert result["adjustment"] == 0
    assert PreferenceModel.model_validate(cold.model_dump()).content_hash == cold.content_hash
    single = train([{**row, "label": 1} for row in rows(artifact)], artifact)
    assert single.diagnostics["cold_start"] is True


def test_duplicate_candidate_key_is_rejected(artifact: dict) -> None:
    """A candidate impression may not count twice under the same identity."""
    duplicate = rows(artifact)
    duplicate[1] = {**duplicate[1], "candidate_key": duplicate[0]["candidate_key"]}
    with pytest.raises(ValueError, match="duplicate candidate"):
        train(duplicate, artifact)


def test_exact_full_feature_order_and_schema_are_required(artifact: dict) -> None:
    """The selected projection can reorder names, but source schema cannot drift."""
    reordered = list(artifact["feature_names"])
    reordered[0], reordered[1] = reordered[1], reordered[0]
    with pytest.raises(ValueError):
        train(rows(artifact), {**artifact, "feature_names": reordered})
    bad = rows(artifact)
    bad[0] = {**bad[0], "features": snapshot(artifact, 0, schema="other").model_dump()}
    with pytest.raises(ValueError, match="feature_schema_mismatch"):
        train(bad, artifact)
    model = train(rows(artifact), artifact)
    wrong_order = snapshot(artifact, 0, names=tuple(reversed(artifact["feature_names"])))
    with pytest.raises(ValueError, match="feature_schema_mismatch"):
        apply(wrong_order, 0.0, model, artifact)


def test_missing_signals_are_zero_after_centering_without_mask_penalty(artifact: dict) -> None:
    """Unavailable features are mean-imputed to zero and cannot teach mask penalties."""
    model = train(rows(artifact), artifact)
    unavailable = snapshot(artifact, 999.0, missing={"grade"})
    unavailable_zero = snapshot(artifact, 0.0, missing={"grade"})
    assert model.model_vector(unavailable)[0] == 0
    assert model.model_vector(unavailable) == model.model_vector(unavailable_zero)


def test_deterministic_fit_and_finite_laplace_posterior(artifact: dict) -> None:
    """Repeated pure-Python fits have byte-stable parameters and covariance."""
    first, second = train(rows(artifact), artifact), train(rows(artifact), artifact)
    assert first.model_dump() == second.model_dump()
    assert all(math.isfinite(value) for row in first.covariance for value in row)
    assert all(first.covariance[index][index] > 0 for index in range(15))


def test_same_schema_different_baseline_is_rejected(artifact: dict) -> None:
    """Runtime and loss cannot silently use another full R0 content hash."""
    model = train(rows(artifact), artifact)
    changed = dict(artifact)
    changed["coefficients"] = list(changed["coefficients"])
    changed["coefficients"][0] += 0.01
    changed.pop("content_hash")
    changed = validate_artifact(changed)
    assert changed["feature_schema_hash"] == artifact["feature_schema_hash"]
    assert changed["content_hash"] != artifact["content_hash"]
    with pytest.raises(ValueError):
        apply(snapshot(artifact, 1.0), 0.0, model, changed)


def test_offline_predict_and_loss_remain_internal_diagnostics(artifact: dict) -> None:
    """The diagnostic is available for evaluation while runtime returns score correction."""
    model = train(rows(artifact), artifact)
    assert 0 <= model.predict(snapshot(artifact, 1.0), 0.0) <= 1
    assert model.loss(rows(artifact)[0], artifact) >= 0
    assert set(apply(snapshot(artifact, 1.0), 0.0, model, artifact)["contributions"]) == set(SELECTED_FEATURES)
