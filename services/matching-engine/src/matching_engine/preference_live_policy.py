"""Frozen validation policy for activating bounded vacancy-local preference models.

This module evaluates an already produced evaluator report.  It does not fit a
model, infer causality, write a feed, or mutate an active model.  All decisions
are deterministic and retain a descriptive uncertainty record for operators.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, model_validator

from matching_engine.models import Contract
from matching_engine.preference_model import (
    PREFERENCE_POLICY_VERSION,
    PREFERENCE_PROJECTION_VERSION,
    PreferenceModel,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import RerankArtifact, validate_artifact

# Operator decision (Mike, 2026-10-08): first fit lowered 40 -> 30 reactions.
# Synthetic-employer validation (preference-validation-v5) shows every coherent
# policy converges by ~21 reactions; the activation gate still requires positive
# validation/golden gains and forbids regression against the active model.
LIVE_POLICY_VERSION = "vacancy-preference-live-v2"
MIN_TRAIN_ROWS = 14
MIN_HOLDOUT_ROWS = 8
MIN_CLASS_COUNT = 2
MAX_LOGLOSS_REGRESSION = 0.01
INITIAL_ACTION_THRESHOLD = 30
FOLLOWUP_ACTION_THRESHOLD = 16


class LiveActivationDecision(Contract):
    """JSON-safe immutable outcome of one candidate activation assessment."""

    policy_version: str = LIVE_POLICY_VERSION
    status: Literal["eligible", "needs_more_data", "rejected", "fallback"]
    activate: bool = False
    reasons: list[str] = Field(default_factory=list)
    source: str | None = None
    candidate_model_hash: str | None = None
    current_model_hash: str | None = None
    baseline_artifact_hash: str | None = None
    context_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    decision_hash: str = ""

    @model_validator(mode="after")
    def integrity(self) -> LiveActivationDecision:
        """Reject a tampered persisted decision payload."""
        if self.decision_hash and self.decision_hash != _hash_decision(self.model_dump()):
            raise ValueError("live decision integrity mismatch")
        return self


class ScheduleDecision(Contract):
    """Deterministic trigger decision based on unique new explicit actions."""

    policy_version: str = LIVE_POLICY_VERSION
    trigger: bool
    threshold: int
    unique_new_count: int
    unique_new_action_keys: list[str]
    reset: bool = False
    reason: str
    next_attempt: int
    state_hash: str = ""

    @model_validator(mode="after")
    def integrity(self) -> ScheduleDecision:
        """Reject a tampered persisted scheduling payload."""
        if self.state_hash and self.state_hash != _hash_schedule(self.model_dump()):
            raise ValueError("schedule decision integrity mismatch")
        return self


def _hash_decision(value: Mapping[str, Any]) -> str:
    """Hash a decision while excluding its recursive decision hash."""
    return digest({key: item for key, item in value.items() if key != "decision_hash"})


def _hash_schedule(value: Mapping[str, Any]) -> str:
    """Hash a schedule decision while excluding its recursive state hash."""
    return digest({key: item for key, item in value.items() if key != "state_hash"})


def policy_hash() -> str:
    """Return the immutable identity of this activation and scheduling policy."""
    return digest(
        {
            "version": LIVE_POLICY_VERSION,
            "minimums": {"train": MIN_TRAIN_ROWS, "holdout": MIN_HOLDOUT_ROWS, "class": MIN_CLASS_COUNT},
            "max_logloss_regression": MAX_LOGLOSS_REGRESSION,
            "initial_actions": INITIAL_ACTION_THRESHOLD,
            "followup_actions": FOLLOWUP_ACTION_THRESHOLD,
            "model_policy": PREFERENCE_POLICY_VERSION,
            "projection": PREFERENCE_PROJECTION_VERSION,
        }
    )


def _baseline(value: object) -> RerankArtifact:
    """Validate the exact full R0 artifact and require its sealed content hash."""
    if isinstance(value, RerankArtifact):
        artifact = value
    elif isinstance(value, Mapping):
        artifact = RerankArtifact.model_validate(value)
    elif hasattr(value, "model_dump"):
        artifact = RerankArtifact.model_validate(value.model_dump())
    else:
        raise ValueError("invalid baseline artifact")
    if not artifact.content_hash:
        raise ValueError("baseline artifact content hash required")
    # Exercise the project's canonical validator as an explicit compatibility
    # check; it also catches a tampered content hash before comparison.
    validate_artifact(artifact.model_dump())
    return artifact


def _candidate(value: object, baseline: RerankArtifact) -> PreferenceModel:
    """Validate a model and pin it to the exact baseline and source schema."""
    model = value if isinstance(value, PreferenceModel) else PreferenceModel.model_validate(value)
    if model.baseline_artifact_hash != baseline.content_hash:
        raise ValueError("model_baseline_mismatch")
    if model.feature_schema_hash != baseline.feature_schema_hash:
        raise ValueError("model_schema_mismatch")
    if tuple(model.compatibility.get("source_feature_names", ())) != tuple(baseline.feature_names):
        raise ValueError("model_feature_order_mismatch")
    if model.projection_version != PREFERENCE_PROJECTION_VERSION:
        raise ValueError("model_projection_mismatch")
    if not model.content_hash:
        raise ValueError("model content hash required")
    return model


def _models_by_context(candidate: object, context_keys: Sequence[str],
                       baseline: RerankArtifact) -> dict[str, PreferenceModel]:
    """Accept one model for one context or a context-keyed model mapping."""
    if isinstance(candidate, Mapping) and set(context_keys).issubset(candidate):
        result = {key: _candidate(candidate[key], baseline) for key in context_keys}
        return result
    if len(context_keys) != 1:
        raise ValueError("one candidate model cannot cover multiple contexts")
    return {context_keys[0]: _candidate(candidate, baseline)}


def _classes(part: Mapping[str, Any]) -> tuple[int, int]:
    """Read strict two-class holdout counts from an evaluator partition."""
    classes = part.get("classes")
    if not isinstance(classes, Mapping):
        return 0, 0
    try:
        return int(classes.get("0", 0)), int(classes.get("1", 0))
    except (TypeError, ValueError):
        return 0, 0


def _metric_number(metrics: Mapping[str, Any], path: str) -> float | None:
    """Read a finite nested metric, returning ``None`` for unavailable data."""
    current: Any = metrics
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if current is None:
        return None
    try:
        result = float(current)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _uncertainty(report: Mapping[str, Any], context_keys: Sequence[str]) -> dict[str, Any]:
    """Copy candidate/bootstrap uncertainty as descriptive, non-causal metadata."""
    contexts = report.get("contexts", {})
    per_context: dict[str, Any] = {}
    for key in context_keys:
        context = contexts.get(key, {}) if isinstance(contexts, Mapping) else {}
        per_context[key] = {
            split: context.get(split, {}).get("bootstrap") if isinstance(context.get(split, {}), Mapping) else None
            for split in ("validation", "golden")
        }
    return {
        "candidate": per_context,
        "bootstrap": report.get("bootstrap", {}),
        "interpretation": "descriptive_uncertainty_only; deterministic_feedback_is_not_causal_evidence",
    }


def _decision(payload: dict[str, Any]) -> LiveActivationDecision:
    """Seal one typed decision payload with a deterministic hash."""
    result = LiveActivationDecision.model_validate(payload)
    return result.model_copy(update={"decision_hash": _hash_decision(result.model_dump())})


def assess_live_model(
    report: Mapping[str, Any],
    candidate_model: object,
    baseline_artifact: object,
    current_model: object | None = None,
    *,
    allow_synthetic: bool = False,
) -> LiveActivationDecision:
    """Apply the frozen validation gate to a candidate model and report.

    Every context must meet the evaluator's train/validation/golden minimums,
    have two classes, a converged positive-trust model, no validation or golden
    ranking regression, and no material log-loss regression.  At least one
    strict ranking gain is required across the two holdouts.  Synthetic reports
    are rejected unless an explicit test contour opts in.
    """
    reasons: list[str] = []
    source = report.get("source") if isinstance(report, Mapping) else None
    if source not in ("operational", "synthetic"):
        reasons.append("source_invalid")
    elif source == "synthetic" and not allow_synthetic:
        reasons.append("synthetic_source_not_allowed")
    try:
        baseline = _baseline(baseline_artifact)
    except (TypeError, ValueError) as exc:
        return _decision({"status": "rejected", "activate": False, "reasons": [str(exc)],
                          "source": source, "compatibility": {"policy_version": LIVE_POLICY_VERSION}})
    contexts = report.get("contexts") if isinstance(report, Mapping) else None
    if not isinstance(contexts, Mapping) or not contexts:
        reasons.append("contexts_missing")
        return _decision({"status": "needs_more_data", "reasons": reasons, "source": source,
                          "baseline_artifact_hash": baseline.content_hash})
    if report.get("baseline_hash") != baseline.content_hash:
        reasons.append("report_baseline_mismatch")
    try:
        models = _models_by_context(candidate_model, list(contexts), baseline)
        current_hash = None
        if current_model is not None:
            current_hash = _candidate(current_model, baseline).content_hash
    except (TypeError, ValueError) as exc:
        reasons.append(str(exc))
        return _decision({"status": "rejected", "activate": False, "reasons": reasons,
                          "source": source, "baseline_artifact_hash": baseline.content_hash})
    positive_ranking_gains = 0
    metrics: dict[str, Any] = {}
    for context_key, context in contexts.items():
        if not isinstance(context, Mapping):
            reasons.append(f"{context_key}:context_invalid")
            continue
        model = models.get(context_key)
        if model is None:
            reasons.append(f"{context_key}:model_missing")
            continue
        report_models = report.get("model_full_json", {})
        report_model = report_models.get(context_key) if isinstance(report_models, Mapping) else None
        if not isinstance(report_model, Mapping) or report_model.get("content_hash") != model.content_hash:
            reasons.append(f"{context_key}:model_report_mismatch")
        fit = context.get("fit", {})
        fit_diagnostics = fit.get("diagnostics", {}) if isinstance(fit, Mapping) else {}
        diagnostics = {**model.diagnostics, **fit_diagnostics}
        train_rows = _metric_number(fit, "train_rows")
        if train_rows is None or train_rows < MIN_TRAIN_ROWS:
            reasons.append(f"{context_key}:train_rows<{MIN_TRAIN_ROWS}")
        try:
            train_positive = int(diagnostics.get("positive", 0))
            train_negative = int(diagnostics.get("negative", 0))
        except (TypeError, ValueError):
            train_positive = train_negative = 0
        if train_positive < MIN_CLASS_COUNT or train_negative < MIN_CLASS_COUNT:
            reasons.append(f"{context_key}:train_two_classes_required")
        if diagnostics.get("converged") is not True:
            reasons.append(f"{context_key}:not_converged")
        if model.trust <= 0:
            reasons.append(f"{context_key}:trust_not_positive")
        context_metrics: dict[str, Any] = {}
        for split in ("validation", "golden"):
            part = context.get(split)
            if not isinstance(part, Mapping):
                reasons.append(f"{context_key}:{split}_missing")
                continue
            n = _metric_number(part, "n")
            class_0, class_1 = _classes(part)
            if n is None or n < MIN_HOLDOUT_ROWS:
                reasons.append(f"{context_key}:{split}_rows<{MIN_HOLDOUT_ROWS}")
            if class_0 < MIN_CLASS_COUNT or class_1 < MIN_CLASS_COUNT:
                reasons.append(f"{context_key}:{split}_two_classes_required")
            base_pair = _metric_number(part, "baseline.pair_accuracy")
            learned_key = "personalized" if isinstance(part.get("personalized"), Mapping) else "learned"
            learned_pair = _metric_number(part, f"{learned_key}.pair_accuracy")
            base_ndcg = _metric_number(part, "baseline.ndcg_at_10")
            learned_ndcg = _metric_number(part, f"{learned_key}.ndcg_at_10")
            base_loss = _metric_number(part, "baseline.logloss")
            learned_loss = _metric_number(part, f"{learned_key}.logloss")
            if None in (base_pair, learned_pair, base_ndcg, learned_ndcg, base_loss, learned_loss):
                reasons.append(f"{context_key}:{split}_metrics_missing")
                continue
            pair_count = _metric_number(part, "baseline.pair_count")
            if pair_count is None or pair_count <= 0:
                reasons.append(f"{context_key}:{split}_ranking_pairs_missing")
            pair_gain = learned_pair - base_pair
            ndcg_gain = learned_ndcg - base_ndcg
            loss_delta = learned_loss - base_loss
            if learned_pair < base_pair or learned_ndcg < base_ndcg:
                reasons.append(f"{context_key}:{split}_ranking_regression")
            if loss_delta > MAX_LOGLOSS_REGRESSION:
                reasons.append(f"{context_key}:{split}_logloss_regression")
            if pair_gain > 0 or ndcg_gain > 0:
                positive_ranking_gains += 1
            context_metrics[split] = {"pair_gain": pair_gain, "ndcg_gain": ndcg_gain,
                                      "logloss_delta": loss_delta, "pair_count": pair_count}
        metrics[context_key] = context_metrics
    if positive_ranking_gains == 0:
        reasons.append("no_strict_positive_ranking_gain")
    status: Literal["eligible", "needs_more_data", "rejected", "fallback"] = "eligible" if not reasons else "fallback"
    needs_data = any(
        reason == "contexts_missing"
        or "_rows<" in reason
        or "_two_classes_required" in reason
        for reason in reasons
    )
    if reasons and needs_data:
        status = "needs_more_data"
    model_hashes = [model.content_hash for model in models.values()]
    return _decision({"status": status, "activate": status == "eligible", "reasons": reasons,
                      "source": source, "candidate_model_hash": model_hashes[0] if len(model_hashes) == 1 else None,
                      "current_model_hash": current_hash, "baseline_artifact_hash": baseline.content_hash,
                      "context_count": len(contexts), "metrics": metrics,
                      "uncertainty": _uncertainty(report, list(contexts)),
                      "compatibility": {"policy_version": LIVE_POLICY_VERSION,
                                         "model_policy_version": PREFERENCE_POLICY_VERSION,
                                         "projection_version": PREFERENCE_PROJECTION_VERSION,
                                         "source": source}})


def activation_gate(
    outcome: Mapping[str, Any],
    model_json: Mapping[str, Any] | None,
    artifact: object,
    *,
    source: str,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """Adapt one evaluator context into the parent store's activation contract.

    ``preference_evaluation.evaluate`` persists a report with the context
    partition and model payload separately, while the store intentionally
    passes only those two pieces here.  Rebuild the minimal report envelope so
    all normal baseline, model-content, schema, holdout, and source checks are
    still applied by :func:`assess_live_model`.
    """
    if not isinstance(outcome, Mapping):
        return {"passed": False, "reasons": ["outcome_invalid"], "policy_hash": policy_hash()}
    if not isinstance(model_json, Mapping):
        return {"passed": False, "reasons": ["model_missing"], "policy_hash": policy_hash()}
    try:
        baseline = _baseline(artifact)
        report: dict[str, Any] = {
            "source": source,
            "baseline_hash": baseline.content_hash,
            "contexts": {"context": dict(outcome)},
            "model_full_json": {"context": dict(model_json)},
        }
        decision = assess_live_model(
            report,
            model_json,
            baseline,
            allow_synthetic=allow_synthetic,
        )
    except (TypeError, ValueError) as exc:
        return {"passed": False, "reasons": [str(exc)], "policy_hash": policy_hash()}
    return {"passed": decision.activate, "reasons": decision.reasons, "policy_hash": policy_hash()}


def schedule_live_training(
    action_keys: Sequence[str],
    previous_action_keys: Sequence[str] = (),
    attempt: int = 0,
    *,
    vacancy_revision: int | None = None,
    previous_vacancy_revision: int | None = None,
    baseline_hash: str | None = None,
    previous_baseline_hash: str | None = None,
) -> ScheduleDecision:
    """Trigger only after unique actions, resetting on revision or R0 change."""
    if attempt < 0:
        raise ValueError("attempt must be nonnegative")
    if any(not isinstance(key, str) or not key for key in (*action_keys, *previous_action_keys)):
        raise ValueError("action keys must be non-empty strings")
    current = set(action_keys)
    previous = set(previous_action_keys)
    reset = ((vacancy_revision is not None and previous_vacancy_revision is not None
              and vacancy_revision != previous_vacancy_revision)
             or (baseline_hash is not None and previous_baseline_hash is not None
                 and baseline_hash != previous_baseline_hash))
    if reset:
        previous = set()
        attempt = 0
    new = sorted(current - previous)
    threshold = INITIAL_ACTION_THRESHOLD if attempt == 0 else FOLLOWUP_ACTION_THRESHOLD
    trigger = len(new) >= threshold
    reason = "reset_context" if reset else ("threshold_reached" if trigger else "below_threshold")
    payload = {"trigger": trigger, "threshold": threshold, "unique_new_count": len(new),
               "unique_new_action_keys": new, "reset": reset, "reason": reason,
               "next_attempt": attempt + (1 if trigger else 0)}
    result = ScheduleDecision.model_validate(payload)
    return result.model_copy(update={"state_hash": _hash_schedule(result.model_dump())})


__all__ = [
    "FOLLOWUP_ACTION_THRESHOLD",
    "INITIAL_ACTION_THRESHOLD",
    "LIVE_POLICY_VERSION",
    "LiveActivationDecision",
    "ScheduleDecision",
    "activation_gate",
    "assess_live_model",
    "policy_hash",
    "schedule_live_training",
]
