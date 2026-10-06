"""Pure Python, vacancy-local residual preference model.

The model is deliberately small and portable.  It learns a regularised residual
over the immutable R0 score; it does not make hiring decisions and its internal
logistic response is only an offline diagnostic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, StrictBool, model_validator

from matching_engine.models import Contract
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import RerankArtifact as BaselineArtifact

SELECTED_FEATURES: tuple[str, ...] = (
    "grade",
    "rating",
    "domain.proximity",
    "stack.proximity",
    "soft_skill.proximity",
    "domain.confirmed_share",
    "stack.confirmed_share",
    "semantic_cosine",
    "project_relevance_evidence",
    "experience_relevance_evidence",
    "experience.production",
    "salary_affordability",
    "coverage_grade",
    "proximity_confirmed",
    "salary_technical_fit",
)
PREFERENCE_POLICY_VERSION = "vacancy-preference-residual-v1"
PREFERENCE_PROJECTION_VERSION = "selected15-residual-v1"
TRANSFORM_POLICY = {
    "observed_clip": 3.0,
    "delta_clip": 0.5,
    "gamma_max": 0.5,
    "l2_strength": 4.0,
    "max_iterations": 50,
    "baseline_offset_clip": 4.0,
    "calibration_prior": 0.25,
    "calibration_slope_prior": 1.0,
    "calibration_nonnegative_slope": True,
    "missing": "zero_after_centering",
}
PREFERENCE_SCHEMA_HASH = digest({"version": PREFERENCE_POLICY_VERSION, "features": SELECTED_FEATURES,
                                 "projection": PREFERENCE_PROJECTION_VERSION, "transform": TRANSFORM_POLICY})
L2_STRENGTH = 4.0
MAX_ITERATIONS = 50


def _finite(value: object, label: str) -> float:
    """Convert one numeric input and reject booleans, NaN and infinity."""
    if isinstance(value, bool):
        raise TypeError(f"{label} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite numeric")
    return result


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    """Numerically stable logistic function for diagnostics and optimisation."""
    if value >= 0:
        z = math.exp(-min(value, 709.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -709.0))
    return z / (1.0 + z)


def _logistic_loss(logit: float, label: float) -> float:
    """Stable binary cross entropy for one observation."""
    # log(1 + exp(logit)) - y*logit, evaluated without overflow.
    return max(logit, 0.0) + math.log1p(math.exp(-abs(logit))) - label * logit


class FeatureSnapshot(Contract):
    """Immutable serialisable feature snapshot used by training and runtime."""

    names: tuple[str, ...]
    values: tuple[float, ...]
    available: tuple[StrictBool, ...]
    schema_hash: str

    @model_validator(mode="after")
    def dimensions(self) -> FeatureSnapshot:
        """Reject malformed or non-finite snapshots before model fitting."""
        if len(self.names) != len(self.values) or len(self.names) != len(self.available):
            raise ValueError("feature snapshot dimensions mismatch")
        if len(set(self.names)) != len(self.names) or any(not name for name in self.names):
            raise ValueError("feature snapshot names must be unique")
        if any(not math.isfinite(value) for value in self.values):
            raise ValueError("feature snapshot contains nonfinite value")
        return self


@dataclass(frozen=True)
class FrozenBaseline:
    """Selected means/scales projected from one validated full R0 artifact."""

    source_feature_names: tuple[str, ...]
    source_schema_hash: str
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    content_hash: str


class PreferenceModel(Contract):
    """Portable MAP residual model with a Laplace covariance estimate."""

    policy_version: str = PREFERENCE_POLICY_VERSION
    feature_names: tuple[str, ...] = SELECTED_FEATURES
    schema_hash: str
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    baseline_scale: float = Field(ge=1.0)
    baseline_artifact_hash: str
    projection_version: str = PREFERENCE_PROJECTION_VERSION
    calibration_intercept: float = 0.0
    calibration_slope: float = 1.0
    gamma: float = Field(ge=0.0, le=0.5)
    trust: float = Field(ge=0.0, le=0.5)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def dimensions(self) -> PreferenceModel:
        """Reject corruption, schema drift and non-finite posterior values."""
        n = len(SELECTED_FEATURES)
        if (self.policy_version != PREFERENCE_POLICY_VERSION
                or self.projection_version != PREFERENCE_PROJECTION_VERSION
                or self.feature_names != SELECTED_FEATURES or self.schema_hash != PREFERENCE_SCHEMA_HASH
                or self.compatibility.get("transform") != TRANSFORM_POLICY):
            raise ValueError("selected feature order/schema mismatch")
        if len(self.means) != n or len(self.scales) != n or len(self.coefficients) != n:
            raise ValueError("preference model dimensions mismatch")
        if len(self.covariance) != n or any(len(row) != n for row in self.covariance):
            raise ValueError("preference covariance dimensions mismatch")
        values = (*self.means, *self.scales, *self.coefficients,
                  *[item for row in self.covariance for item in row], self.baseline_scale,
                  self.calibration_intercept, self.calibration_slope, self.gamma, self.trust)
        if any(not math.isfinite(value) for value in values) or any(value <= 0 for value in self.scales):
            raise ValueError("preference model contains nonfinite parameters")
        if self.calibration_slope < 0:
            raise ValueError("calibration slope must be nonnegative")
        if self.content_hash and self.content_hash != model_hash(self.model_dump()):
            raise ValueError("preference model integrity mismatch")
        return self

    def model_vector(self, snapshot: FeatureSnapshot | Mapping[str, Any]) -> tuple[float, ...]:
        """Return centred, clipped selected features with missing values as zero."""
        snap = _coerce_snapshot(snapshot)
        expected_names = tuple(self.compatibility.get("source_feature_names", ()))
        if (snap.schema_hash != self.compatibility.get("snapshot_schema_hash", snap.schema_hash)
                or snap.names != expected_names):
            raise ValueError("feature_schema_mismatch")
        return _selected_vector(snap, self.means, self.scales)

    @property
    def feature_schema_hash(self) -> str:
        """Expose the source snapshot schema under the naming used by R0."""
        return str(self.compatibility.get("snapshot_schema_hash", ""))

    def response_logit(self, snapshot: FeatureSnapshot | Mapping[str, Any], baseline_score: float,
                       *, personalized: bool = True) -> float:
        """Return an offline response logit; this is not a public probability."""
        value = _finite(baseline_score, "baseline_score")
        z = _clip(value / self.baseline_scale, -4.0, 4.0)
        result = self.calibration_intercept + self.calibration_slope * z
        if personalized:
            x = self.model_vector(snapshot)
            result += self.gamma * _clip(math.fsum(c * v for c, v in zip(self.coefficients, x)), -0.5, 0.5)
        if not math.isfinite(result):
            raise ValueError("nonfinite response logit")
        return result

    def predict(self, snapshot: FeatureSnapshot | Mapping[str, Any], baseline_score: float,
                *, personalized: bool = True) -> float:
        """Return an internal-only logistic diagnostic probability."""
        return _sigmoid(self.response_logit(snapshot, baseline_score, personalized=personalized))

    def loss(self, example: Mapping[str, Any] | PreferenceExample, artifact: object | None = None,
             *, personalized: bool = True) -> float:
        """Return one offline cross-entropy value, without exposing a hiring claim."""
        if artifact is None:
            raise ValueError("baseline artifact is required")
        item = _coerce_example(example)
        snap = item.snapshot
        _validate_runtime_artifact(self, artifact, snap)
        return _logistic_loss(self.response_logit(snap, item.baseline_score, personalized=personalized), item.label)


@dataclass(frozen=True)
class PreferenceExample:
    """One immutable labelled impression used only by the offline trainer."""

    snapshot: FeatureSnapshot
    baseline_score: float
    label: int
    candidate_key: str


def model_hash(value: Mapping[str, Any]) -> str:
    """Hash a model payload without its recursive content hash."""
    return digest({key: item for key, item in value.items() if key != "content_hash"})


def _coerce_snapshot(value: FeatureSnapshot | Mapping[str, Any]) -> FeatureSnapshot:
    """Parse a snapshot from the JSON shape emitted by learning exports."""
    if isinstance(value, FeatureSnapshot):
        return value
    if (hasattr(value, "names") and hasattr(value, "values") and hasattr(value, "available")
            and hasattr(value, "schema_hash")):
        return FeatureSnapshot(names=tuple(value.names), values=tuple(value.values),
                               available=tuple(value.available), schema_hash=str(value.schema_hash))
    if not isinstance(value, Mapping):
        raise TypeError("invalid feature snapshot")
    data = dict(value)
    return FeatureSnapshot.model_validate(data)


def _selected_vector(snapshot: FeatureSnapshot, means: Sequence[float], scales: Sequence[float]) -> tuple[float, ...]:
    """Select by immutable names, centre observed values and zero-fill unknowns."""
    positions = {name: index for index, name in enumerate(snapshot.names)}
    if any(name not in positions for name in SELECTED_FEATURES):
        raise ValueError("feature_schema_mismatch")
    result = []
    for name, mean, scale in zip(SELECTED_FEATURES, means, scales, strict=True):
        index = positions[name]
        if not snapshot.available[index]:
            result.append(0.0)
        else:
            result.append(_clip((snapshot.values[index] - mean) / scale, -3.0, 3.0))
    return tuple(result)


def _freeze_artifact(value: object, *, expected_schema: str | None = None) -> FrozenBaseline:
    """Validate the complete R0 artifact, then project selected15 by name."""
    if isinstance(value, BaselineArtifact):
        source = value
    elif isinstance(value, Mapping):
        source = BaselineArtifact.model_validate(value)
    elif hasattr(value, "model_dump"):
        source = BaselineArtifact.model_validate(value.model_dump())
    else:
        raise ValueError("invalid baseline artifact")
    if not source.content_hash:
        raise ValueError("baseline artifact content hash required")
    names = tuple(source.feature_names)
    if len(set(names)) != len(names):
        raise ValueError("selected feature order/schema mismatch")
    positions = {name: index for index, name in enumerate(names)}
    if any(name not in positions for name in SELECTED_FEATURES):
        raise ValueError("selected feature order/schema mismatch")
    selected = [positions[name] for name in SELECTED_FEATURES]
    means = tuple(_finite(source.means[index], "artifact mean") for index in selected)
    scales = tuple(_finite(source.scales[index], "artifact scale") for index in selected)
    if any(value <= 0 for value in scales):
        raise ValueError("artifact scales must be positive")
    if expected_schema is not None and source.feature_schema_hash != expected_schema:
        raise ValueError("feature_schema_mismatch")
    return FrozenBaseline(names, source.feature_schema_hash, means, scales,
                          tuple(_finite(value, "artifact coefficient") for value in source.coefficients),
                          source.content_hash)


def _validate_runtime_artifact(model: PreferenceModel, value: object,
                               snapshot: FeatureSnapshot) -> FrozenBaseline:
    """Require the exact full baseline artifact pinned by a trained model."""
    artifact = _freeze_artifact(value, expected_schema=snapshot.schema_hash)
    expected_names = tuple(model.compatibility.get("source_feature_names", ()))
    if artifact.content_hash != model.baseline_artifact_hash or artifact.source_feature_names != expected_names:
        raise ValueError("baseline_artifact_mismatch")
    return artifact


def _coerce_example(value: Mapping[str, Any] | PreferenceExample) -> PreferenceExample:
    """Parse one labelled training row and validate its finite scalar fields."""
    if isinstance(value, PreferenceExample):
        item = value
    elif isinstance(value, Mapping):
        snapshot = value.get("features", value.get("snapshot"))
        if snapshot is None:
            raise ValueError("training example missing features snapshot")
        candidate_key = value.get("candidate_key", value.get("candidate_id"))
        if not isinstance(candidate_key, str) or not candidate_key:
            raise ValueError("training example missing candidate_key")
        label = value.get("label")
        if isinstance(label, bool) or label not in (0, 1):
            raise ValueError("label must be 0 or 1")
        item = PreferenceExample(_coerce_snapshot(snapshot), _finite(value.get("baseline_score"), "baseline_score"),
                                 int(label), candidate_key)
    else:
        raise TypeError("invalid training example")
    if item.label not in (0, 1) or not item.candidate_key:
        raise ValueError("invalid training example")
    if not math.isfinite(item.baseline_score):
        raise ValueError("baseline_score must be finite")
    return item


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small positive-definite linear system using pivoted elimination."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(a[row][column]))
        if abs(a[pivot][column]) < 1e-12:
            raise ValueError("singular preference Hessian")
        if pivot != column:
            a[column], a[pivot] = a[pivot], a[column]
        divisor = a[column][column]
        for j in range(column, n + 1):
            a[column][j] /= divisor
        for row in range(n):
            if row == column:
                continue
            factor = a[row][column]
            if factor:
                for j in range(column, n + 1):
                    a[row][j] -= factor * a[column][j]
    result = [a[index][n] for index in range(n)]
    if any(not math.isfinite(item) for item in result):
        raise ValueError("nonfinite linear solve")
    return result


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    """Invert a positive-definite matrix through repeated stable solves."""
    n = len(matrix)
    result = []
    for column in range(n):
        unit = [1.0 if index == column else 0.0 for index in range(n)]
        result.append(_solve(matrix, unit))
    return [[result[column][row] for column in range(n)] for row in range(n)]


def _quadratic(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> float:
    """Evaluate a finite quadratic form for posterior uncertainty."""
    return math.fsum(left * value * right
                     for left, row in zip(vector, matrix, strict=True)
                     for value, right in zip(row, vector, strict=True))


def _fit_calibration(rows: Sequence[tuple[float, int]], scale: float) -> tuple[float, float, int, float]:
    """Fit a stabilised two-parameter baseline logit with nonnegative slope."""
    if not rows:
        return 0.0, 1.0, 0, 0.0
    params = [0.0, 1.0]
    regularizer = 0.25

    def objective(a: float, b: float) -> float:
        return math.fsum(_logistic_loss(a + b * _clip(raw / scale, -4.0, 4.0), label)
                         for raw, label in rows) + regularizer * (a * a + (b - 1.0) ** 2) / 2

    iterations = 0
    for iterations in range(1, MAX_ITERATIONS + 1):
        gradient = [regularizer * params[0], regularizer * (params[1] - 1.0)]
        hessian = [[regularizer, 0.0], [0.0, regularizer]]
        for raw, label in rows:
            x = _clip(raw / scale, -4.0, 4.0)
            p = _sigmoid(params[0] + params[1] * x)
            error = p - label
            gradient[0] += error
            gradient[1] += error * x
            weight = max(p * (1.0 - p), 1e-8)
            hessian[0][0] += weight
            hessian[0][1] += weight * x
            hessian[1][0] += weight * x
            hessian[1][1] += weight * x * x
        step = _solve(hessian, gradient)
        old_loss = objective(params[0], params[1])
        accepted = False
        for damping in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125):
            candidate = [params[0] - damping * step[0], max(0.0, params[1] - damping * step[1])]
            if objective(*candidate) <= old_loss + 1e-12:
                params = candidate
                accepted = True
                break
        if not accepted or max(abs(item) for item in step) < 1e-8:
            break
    loss = objective(*params)
    return params[0], params[1], iterations, loss


def _cold_covariance() -> tuple[tuple[float, ...], ...]:
    return tuple(tuple((1.0 / L2_STRENGTH) if row == col else 0.0 for col in range(len(SELECTED_FEATURES)))
                   for row in range(len(SELECTED_FEATURES)))


def _empty_model(artifact: FrozenBaseline, baseline_scale: float, *, reason: str,
                 snapshot_schema_hash: str, baseline_artifact_hash: str) -> PreferenceModel:
    """Build deterministic zero-residual cold-start state."""
    payload = PreferenceModel(policy_version=PREFERENCE_POLICY_VERSION, schema_hash=PREFERENCE_SCHEMA_HASH,
                              means=artifact.means, scales=artifact.scales,
                              coefficients=(0.0,) * len(SELECTED_FEATURES), covariance=_cold_covariance(),
                              baseline_scale=baseline_scale, baseline_artifact_hash=baseline_artifact_hash,
                              projection_version=PREFERENCE_PROJECTION_VERSION,
                              calibration_intercept=0.0,
                              calibration_slope=1.0, gamma=0.0, trust=0.0,
                              diagnostics={"cold_start": True, "reason": reason, "n_examples": 0,
                                           "n_unique": 0, "iterations": 0, "loss": 0.0,
                                           "class_balance": 0.0, "contradiction_rate": 0.0,
                                           "reliability_snr_heuristic": 0.0,
                                           "trust_kind": "bounded_confidence_heuristic"},
                              compatibility={"snapshot_schema_hash": snapshot_schema_hash,
                                             "source_feature_names": list(artifact.source_feature_names),
                                             "baseline_schema_hash": artifact.source_schema_hash,
                                             "baseline_artifact_hash": baseline_artifact_hash,
                                             "projection_version": PREFERENCE_PROJECTION_VERSION,
                                             "transform": dict(TRANSFORM_POLICY),
                                             "feature_order": list(SELECTED_FEATURES)})
    return payload.model_copy(update={"content_hash": model_hash(payload.model_dump())})


def train(examples: Sequence[Mapping[str, Any] | PreferenceExample], baseline_artifact: object) -> PreferenceModel:
    """Fit a bounded vacancy-specific residual logistic model offline.

    Duplicate candidate keys or hashes are rejected.  Fewer than eight unique
    candidates, a single class, or a schema mismatch yields deterministic zero
    residual cold start.  The caller owns any held-out quality/activation gate.
    """
    if not isinstance(examples, Sequence):
        raise TypeError("examples must be a sequence")
    rows = [_coerce_example(item) for item in examples]
    if rows:
        schema_hash = rows[0].snapshot.schema_hash
        if any(item.snapshot.schema_hash != schema_hash for item in rows):
            raise ValueError("feature_schema_mismatch")
    else:
        schema_hash = PREFERENCE_SCHEMA_HASH
    artifact = _freeze_artifact(baseline_artifact, expected_schema=schema_hash if rows else None)
    if not rows:
        # With no examples the frozen baseline is the only source of the
        # runtime snapshot schema; preserve it for a usable zero-residual model.
        schema_hash = artifact.source_schema_hash
    baseline_artifact_hash = artifact.content_hash
    if len(SELECTED_FEATURES) != len(artifact.means):
        raise ValueError("selected artifact dimensions mismatch")
    if rows and any(item.snapshot.schema_hash != artifact.source_schema_hash
                    or item.snapshot.names != artifact.source_feature_names for item in rows):
        raise ValueError("feature_schema_mismatch")
    baseline_coefficients = artifact.coefficients
    baseline_scale = max(1.0, math.sqrt(math.fsum(value * value for value in baseline_coefficients)))
    keys = set()
    for item in rows:
        if item.candidate_key in keys:
            raise ValueError("duplicate candidate key")
        keys.add(item.candidate_key)
    classes = {item.label for item in rows}
    if len(rows) < 8 or len(classes) < 2:
        return _empty_model(artifact, baseline_scale, reason="insufficient_unique_or_class",
                            snapshot_schema_hash=schema_hash, baseline_artifact_hash=baseline_artifact_hash)

    vectors = [_selected_vector(item.snapshot, artifact.means, artifact.scales) for item in rows]
    baseline_rows = [(item.baseline_score, item.label) for item in rows]
    calibration_intercept, calibration_slope, calibration_iterations, calibration_loss = _fit_calibration(
        baseline_rows, baseline_scale)

    # Contradictions are repeated feature signatures with opposing labels. They
    # reduce trust, but never become an availability/mask feature.
    signatures: dict[tuple[float, ...], set[int]] = {}
    for vector, item in zip(vectors, rows, strict=True):
        signatures.setdefault(vector, set()).add(item.label)
    contradiction_groups = sum(1 for labels in signatures.values() if len(labels) > 1)
    contradiction_rate = contradiction_groups / max(1, len(signatures))
    positive = sum(item.label for item in rows)
    class_balance = 2.0 * min(positive, len(rows) - positive) / len(rows)

    n = len(SELECTED_FEATURES)
    coefficients = [0.0] * n
    regularizer = L2_STRENGTH

    def objective(beta: Sequence[float]) -> float:
        total = regularizer * math.fsum(value * value for value in beta) / 2
        for vector, item in zip(vectors, rows, strict=True):
            offset = calibration_intercept + calibration_slope * _clip(item.baseline_score / baseline_scale, -4.0, 4.0)
            total += _logistic_loss(
                offset + math.fsum(value * feature for value, feature in zip(beta, vector, strict=True)), item.label)
        return total

    iterations = 0
    converged = False
    for iterations in range(1, MAX_ITERATIONS + 1):
        gradient = [regularizer * value for value in coefficients]
        hessian = [[regularizer if row == col else 0.0 for col in range(n)] for row in range(n)]
        for vector, item in zip(vectors, rows, strict=True):
            offset = calibration_intercept + calibration_slope * _clip(item.baseline_score / baseline_scale, -4.0, 4.0)
            logit = offset + math.fsum(value * feature for value, feature in zip(coefficients, vector, strict=True))
            probability = _sigmoid(logit)
            error = probability - item.label
            for row in range(n):
                gradient[row] += error * vector[row]
                for col in range(row, n):
                    hessian[row][col] += max(probability * (1.0 - probability), 1e-8) * vector[row] * vector[col]
        for row in range(n):
            for col in range(row):
                hessian[row][col] = hessian[col][row]
        step = _solve(hessian, gradient)
        old_loss = objective(coefficients)
        accepted = False
        for damping in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125):
            candidate = [value - damping * delta for value, delta in zip(coefficients, step, strict=True)]
            if objective(candidate) <= old_loss + 1e-12:
                coefficients = candidate
                accepted = True
                if max(abs(damping * delta) for delta in step) < 1e-8:
                    converged = True
                break
        if converged or not accepted:
            break
    # Rebuild the final Hessian for the Laplace posterior covariance.
    hessian = [[regularizer if row == col else 0.0 for col in range(n)] for row in range(n)]
    for vector, item in zip(vectors, rows, strict=True):
        offset = calibration_intercept + calibration_slope * _clip(item.baseline_score / baseline_scale, -4.0, 4.0)
        probability = _sigmoid(
            offset + math.fsum(value * feature for value, feature in zip(coefficients, vector, strict=True)))
        weight = max(probability * (1.0 - probability), 1e-8)
        for row in range(n):
            for col in range(row, n):
                hessian[row][col] += weight * vector[row] * vector[col]
    for row in range(n):
        for col in range(row):
            hessian[row][col] = hessian[col][row]
    covariance = _inverse(hessian)
    if any(not math.isfinite(value) for row in covariance for value in row):
        raise ValueError("nonfinite preference covariance")
    posterior_support = math.fsum(1.0 / math.sqrt(1.0 + max(0.0, _quadratic(vector, covariance)))
                                  for vector in vectors) / len(vectors)
    reliability_snr_heuristic = _clip((1.0 - contradiction_rate) * posterior_support, 0.0, 1.0)
    trust = _clip(0.5 * (len(rows) / (len(rows) + 8.0)) * class_balance
                  * reliability_snr_heuristic, 0.0, 0.5)
    payload = PreferenceModel(policy_version=PREFERENCE_POLICY_VERSION, schema_hash=PREFERENCE_SCHEMA_HASH,
                              means=artifact.means, scales=artifact.scales,
                              coefficients=tuple(coefficients), covariance=tuple(tuple(row) for row in covariance),
                              baseline_scale=baseline_scale, baseline_artifact_hash=baseline_artifact_hash,
                              projection_version=PREFERENCE_PROJECTION_VERSION,
                              calibration_intercept=calibration_intercept,
                              calibration_slope=calibration_slope, gamma=trust, trust=trust,
                              diagnostics={"cold_start": False, "reason": None, "n_examples": len(rows),
                                           "n_unique": len(rows), "positive": positive,
                                           "negative": len(rows) - positive,
                                           "class_balance": class_balance, "contradiction_groups": contradiction_groups,
                                           "contradiction_rate": contradiction_rate,
                                           "reliability_snr_heuristic": reliability_snr_heuristic,
                                           "trust_kind": "bounded_confidence_heuristic",
                                           "iterations": iterations, "converged": converged,
                                           "loss": objective(coefficients),
                                           "calibration_iterations": calibration_iterations,
                                           "calibration_loss": calibration_loss},
                              compatibility={"snapshot_schema_hash": schema_hash,
                                             "source_feature_names": list(artifact.source_feature_names),
                                             "baseline_schema_hash": artifact.source_schema_hash,
                                             "baseline_artifact_hash": baseline_artifact_hash,
                                             "projection_version": PREFERENCE_PROJECTION_VERSION,
                                             "transform": dict(TRANSFORM_POLICY),
                                             "feature_order": list(SELECTED_FEATURES)})
    return payload.model_copy(update={"content_hash": model_hash(payload.model_dump())})


def apply(snapshot: FeatureSnapshot | Mapping[str, Any], baseline_score: float,
          model: PreferenceModel | Mapping[str, Any], artifact: object) -> dict[str, Any]:
    """Apply one bounded residual and return an explainable serialisable result."""
    current = model if isinstance(model, PreferenceModel) else PreferenceModel.model_validate(model)
    snap = _coerce_snapshot(snapshot)
    _validate_runtime_artifact(current, artifact, snap)
    raw_score = _finite(baseline_score, "baseline_score")
    vector = current.model_vector(snap)
    raw_delta = math.fsum(coefficient * value for coefficient, value in zip(current.coefficients, vector, strict=True))
    clipped_delta = _clip(raw_delta, -0.5, 0.5)
    adjustment = current.baseline_scale * current.gamma * clipped_delta
    uncertainty_variance = _quadratic(vector, current.covariance)
    uncertainty = current.baseline_scale * current.gamma * math.sqrt(max(0.0, uncertainty_variance))
    raw_contributions = {
        name: current.baseline_scale * current.gamma * coefficient * value
        for name, coefficient, value in zip(SELECTED_FEATURES, current.coefficients, vector, strict=True)
    }
    # Keep opposing feature explanations visible when their raw sum cancels;
    # there is no correction to scale in that case, but no signal is hidden.
    clip_factor = clipped_delta / raw_delta if raw_delta else 1.0
    contributions = {name: value * clip_factor for name, value in raw_contributions.items()}
    return {"score": raw_score + adjustment, "adjustment": adjustment, "contributions": contributions,
            "uncertainty": uncertainty, "confidence": current.trust,
            "raw_delta": raw_delta, "clipped_delta": clipped_delta,
            "raw_contributions": raw_contributions, "clip_factor": clip_factor}


def predict(snapshot: FeatureSnapshot | Mapping[str, Any], baseline_score: float,
            model: PreferenceModel | Mapping[str, Any], *, personalized: bool = True) -> float:
    """Return the internal-only response diagnostic for offline evaluation."""
    current = model if isinstance(model, PreferenceModel) else PreferenceModel.model_validate(model)
    return current.predict(snapshot, baseline_score, personalized=personalized)


def loss(example: Mapping[str, Any] | PreferenceExample, model: PreferenceModel | Mapping[str, Any],
         artifact: object, *, personalized: bool = True) -> float:
    """Return one internal-only logistic loss for offline evaluation."""
    current = model if isinstance(model, PreferenceModel) else PreferenceModel.model_validate(model)
    return current.loss(example, artifact, personalized=personalized)


__all__ = [
    "PREFERENCE_POLICY_VERSION",
    "PREFERENCE_PROJECTION_VERSION",
    "PREFERENCE_SCHEMA_HASH",
    "SELECTED_FEATURES",
    "FeatureSnapshot",
    "FrozenBaseline",
    "PreferenceExample",
    "PreferenceModel",
    "apply",
    "loss",
    "model_hash",
    "predict",
    "train",
]
