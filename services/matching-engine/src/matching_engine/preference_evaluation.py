"""Offline evaluation for the vacancy preference model.

The evaluator is deliberately separate from the model implementation and from
the adapter which builds preference observations.  It only consumes sealed
train/validation/golden rows, calls the local model worker for a train-only fit,
and emits a SHADOW report.  Nothing in this module can promote or activate a
model.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from matching_engine.preference_model import (
    PREFERENCE_PROJECTION_VERSION,
    PREFERENCE_SCHEMA_HASH,
    SELECTED_FEATURES,
)
from matching_engine.preference_model import apply as apply_preference
from matching_engine.preference_model import train as fit_preference_model
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import validate_artifact

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 2000
MINIMUMS = {"train": 24, "validation": 8, "golden": 8}
MINIMUM_CLASS_COUNT = 2

# This is an intentionally small, frozen worker contract.  The model worker
# may use these values for its own bounded optimizer, but validation must be
# independent of the worker's implementation details.
FIXED_MODEL_CONFIG: dict[str, Any] = {
    "trainer": "matching_engine.preference_model.train",
    "max_iterations": 50,
    "l2_strength": 4.0,
    "bounded_residual": True,
}


def _finite(value: Any) -> bool:
    """Return whether a nested feature value contains only finite scalars."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _finite(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return False


def _sha(value: Any) -> str:
    """Hash a validated JSON-like value with the project digest helper."""
    return digest(value)


def _require_row(row: Mapping[str, Any], context: str, split: str, index: int) -> None:
    """Validate one sealed observation without changing or enriching it."""
    required = {"features", "baseline_score", "label", "candidate_key", "decision_at"}
    missing = required.difference(row)
    if missing:
        raise ValueError(f"{context}/{split}/{index}: missing fields {sorted(missing)}")
    if not isinstance(row["candidate_key"], str) or not row["candidate_key"]:
        raise ValueError(f"{context}/{split}/{index}: candidate_key must be non-empty")
    if type(row["label"]) is not int or row["label"] not in (0, 1):
        raise ValueError(f"{context}/{split}/{index}: label must be 0 or 1")
    if isinstance(row["baseline_score"], bool) or not isinstance(row["baseline_score"], (int, float)):
        raise TypeError(f"{context}/{split}/{index}: baseline_score must be numeric")
    if not math.isfinite(float(row["baseline_score"])) or not _finite(row["features"]):
        raise ValueError(f"{context}/{split}/{index}: nonfinite or unsupported features")
    if _decision_time(row["decision_at"]) is None:
        raise ValueError(f"{context}/{split}/{index}: invalid decision_at")


def _decision_time(value: Any) -> datetime | None:
    """Parse one timezone-aware decision timestamp from the sealed export."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _validate_contexts(contexts: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Validate context partitions and return a normalized shallow copy."""
    if not isinstance(contexts, Mapping):
        raise TypeError("contexts must be a mapping")
    normalized: dict[str, dict[str, Any]] = {}
    counts = {"contexts": 0, "train": 0, "validation": 0, "golden": 0, "label_0": 0, "label_1": 0}
    for context_key, context in contexts.items():
        if not isinstance(context_key, str) or not context_key or not isinstance(context, Mapping):
            raise ValueError("context keys and values must be non-empty mappings")
        if not isinstance(context.get("identity", {}), Mapping):
            raise TypeError(f"{context_key}: identity must be a mapping")
        rows: dict[str, list[Mapping[str, Any]]] = {}
        seen: dict[str, str] = {}
        for split in ("train", "validation", "golden"):
            values = context.get(split)
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise TypeError(f"{context_key}/{split}: rows must be a sequence")
            rows[split] = []
            for index, row in enumerate(values):
                if not isinstance(row, Mapping):
                    raise TypeError(f"{context_key}/{split}/{index}: row must be a mapping")
                _require_row(row, context_key, split, index)
                candidate = row["candidate_key"]
                if candidate in seen:
                    raise ValueError(f"candidate_key crosses {seen[candidate]} and {split}: {candidate}")
                seen[candidate] = split
                rows[split].append(row)
                counts[split] += 1
                counts[f"label_{row['label']}"] += 1
        for earlier, later in (("train", "validation"), ("validation", "golden")):
            if (rows[earlier] and rows[later]
                    and max(_decision_time(row["decision_at"]) for row in rows[earlier]) >= min(
                    _decision_time(row["decision_at"]) for row in rows[later]
                )):
                raise ValueError(f"{context_key}: decision_at order crosses {earlier}/{later}")
        normalized[context_key] = {"identity": dict(context["identity"]), **rows}
    counts["contexts"] = len(normalized)
    return normalized, counts


def _classes(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Return class counts as JSON-friendly strings."""
    return {"0": sum(row["label"] == 0 for row in rows), "1": sum(row["label"] == 1 for row in rows)}


def _needs_more_data(context: Mapping[str, Any]) -> list[str]:
    """Describe all minimum-data violations for one context."""
    reasons: list[str] = []
    for split, minimum in MINIMUMS.items():
        rows = context[split]
        if len(rows) < minimum:
            reasons.append(f"{split}_rows<{minimum}")
        classes = _classes(rows)
        if classes["0"] < MINIMUM_CLASS_COUNT:
            reasons.append(f"{split}_class_0<{MINIMUM_CLASS_COUNT}")
        if classes["1"] < MINIMUM_CLASS_COUNT:
            reasons.append(f"{split}_class_1<{MINIMUM_CLASS_COUNT}")
    return reasons


def _probability(logit: float) -> float:
    """Apply a numerically stable sigmoid."""
    if logit >= 0:
        z = math.exp(-min(logit, 40.0))
        return 1.0 / (1.0 + z)
    z = math.exp(min(logit, 40.0))
    return z / (1.0 + z)


def _loss(logit: float, label: int) -> float:
    """Compute Bernoulli logloss from a logit."""
    probability = min(max(_probability(logit), 1e-7), 1.0 - 1e-7)
    return -(label * math.log(probability) + (1 - label) * math.log1p(-probability))


def _metrics(rows: Sequence[Mapping[str, Any]], logits: Sequence[float], rank_scores: Sequence[float]) -> dict[str, Any]:
    """Compute logloss, all opposite-label pair accuracy, and NDCG@10."""
    if len(rows) != len(logits) or len(rows) != len(rank_scores) or not rows:
        raise ValueError("metric dimensions are invalid")
    if any(not math.isfinite(float(score)) for score in (*logits, *rank_scores)):
        raise ValueError("nonfinite model metric score")
    losses = [_loss(score, row["label"]) for row, score in zip(rows, logits, strict=True)]
    pairs = 0
    correct = 0.0
    for left, row_left in enumerate(rows):
        for right in range(left + 1, len(rows)):
            row_right = rows[right]
            if row_left["label"] == row_right["label"]:
                continue
            pairs += 1
            direction = rank_scores[left] - rank_scores[right]
            expected = 1 if row_left["label"] > row_right["label"] else -1
            if direction * expected > 0:
                correct += 1.0
            elif direction == 0:
                correct += 0.5
    ranked = sorted(range(len(rows)), key=lambda index: (-rank_scores[index], index))[:10]
    observed = sum((2 ** rows[index]["label"] - 1) / math.log2(position + 2) for position, index in enumerate(ranked))
    ideal_labels = sorted((row["label"] for row in rows), reverse=True)[:10]
    ideal = sum((2 ** label - 1) / math.log2(position + 2) for position, label in enumerate(ideal_labels))
    return {
        "logloss": math.fsum(losses) / len(losses),
        "pair_accuracy": correct / pairs if pairs else None,
        "pair_count": pairs,
        "ndcg_at_10": observed / ideal if ideal else None,
        "n": len(rows),
    }


def _bootstrap(values: Sequence[float], seed: int = BOOTSTRAP_SEED) -> dict[str, Any] | None:
    """Return a deterministic percentile CI for a paired mean delta."""
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = [math.fsum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_ITERATIONS)]
    means.sort()
    def quantile(q: float) -> float:
        position = (len(means) - 1) * q
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return means[lower]
        return means[lower] + (means[upper] - means[lower]) * (position - lower)
    return {"mean": math.fsum(values) / n, "ci95_low": quantile(0.025), "ci95_high": quantile(0.975),
            "n": n, "iterations": BOOTSTRAP_ITERATIONS, "seed": seed}


def _part_report(rows: Sequence[Mapping[str, Any]], baseline_logits: Sequence[float], learned_logits: Sequence[float],
                 baseline_rank_scores: Sequence[float], learned_rank_scores: Sequence[float]) -> dict[str, Any]:
    """Build metrics and paired per-candidate loss deltas for one holdout."""
    baseline = _metrics(rows, baseline_logits, baseline_rank_scores)
    learned = _metrics(rows, learned_logits, learned_rank_scores)
    deltas = [_loss(base, row["label"]) - _loss(fitted, row["label"]) for row, base, fitted in zip(rows, baseline_logits, learned_logits, strict=True)]
    return {"status": "evaluated", "n": len(rows), "classes": _classes(rows), "baseline": baseline,
            "personalized": learned, "learned": learned,
            "delta": {"logloss": learned["logloss"] - baseline["logloss"],
                      "loss_improvement": baseline["logloss"] - learned["logloss"],
                      "pair_accuracy": (learned["pair_accuracy"] - baseline["pair_accuracy"])
                      if learned["pair_accuracy"] is not None and baseline["pair_accuracy"] is not None else None,
                      "ndcg_at_10": (learned["ndcg_at_10"] - baseline["ndcg_at_10"])
                      if learned["ndcg_at_10"] is not None and baseline["ndcg_at_10"] is not None else None},
            "candidate_loss_improvements": deltas, "bootstrap": _bootstrap(deltas)}


def evaluate(
    contexts: Mapping[str, Any],
    artifact: Any,
    source: str,
    dataset_hash: str,
    split_hash: str,
) -> dict[str, Any]:
    """Evaluate a sealed vacancy preference dataset in SHADOW mode.

    ``contexts`` contains ``identity`` and disjoint ``train``, ``validation``
    and ``golden`` row lists.  Only train rows reach ``fit``.  The returned
    report is deterministic and explicitly test-only even when all metrics
    improve; activation decisions belong to the parent adapter.
    """
    if source not in ("operational", "synthetic"):
        raise ValueError("source must be operational or synthetic")
    baseline_payload = artifact.model_dump() if hasattr(artifact, "model_dump") else artifact
    baseline = validate_artifact(baseline_payload)
    normalized, source_counts = _validate_contexts(contexts)
    input_sha = _sha(normalized)
    reconstructed_plan_hash = _sha({key: {"identity": value["identity"], **{
        split: [row["candidate_key"] for row in value[split]]
        for split in ("train", "validation", "golden")}}
        for key, value in normalized.items()})
    dataset_plan_hash = split_hash or reconstructed_plan_hash
    report: dict[str, Any] = {
        "status": "shadow_test_only",
        "state": "shadow_test_only",
        "mode": "SHADOW",
        "activation": "test_only",
        "test_only": True,
        "live_promotion_allowed": False,
        "live_activation": False,
        "propensity": "unknown",
        "deterministic": True,
        "source": source,
        "dataset_hash": dataset_hash,
        "split_hash": split_hash,
        "input_sha": input_sha,
        "dataset_plan_hash": dataset_plan_hash,
        "source_counts": source_counts,
        "validated_diagnostics": {"source_counts": source_counts, "input_sha": input_sha,
                                   "dataset_plan_hash": dataset_plan_hash,
                                   "reconstructed_plan_hash": reconstructed_plan_hash,
                                   "no_label_leakage": True},
        "baseline_hash": baseline["content_hash"],
        "baseline_parameters": {"feature_count": len(baseline["feature_names"]),
                                "feature_schema_hash": baseline["feature_schema_hash"],
                                "strict_schema_hash": baseline["strict_schema_hash"],
                                "main_policy_hash": baseline["main_policy_hash"]},
        "contexts": {},
        "model_full_json": {},
        "model_count": 0,
        "fit_performed": False,
        "operational_source": source == "operational",
        "projection_hash": digest({"version": PREFERENCE_PROJECTION_VERSION,
                                    "schema_hash": PREFERENCE_SCHEMA_HASH,
                                    "features": SELECTED_FEATURES,
                                    "transforms": {"center": True, "scale": True,
                                                    "clip": [-3.0, 3.0], "residual_clip": [-0.5, 0.5]}}),
        "bootstrap": {"iterations": BOOTSTRAP_ITERATIONS, "seed": BOOTSTRAP_SEED,
                       "resampling_unit": "unique_candidate_key_within_context",
                       "contexts": {}, "macro_contexts": None, "global_ci": None,
                       "correlation_note": "candidate rows are resampled only within context; macro CI resamples contexts"},
    }
    context_reports: list[dict[str, Any]] = []
    for context_key, context in normalized.items():
        reasons = _needs_more_data(context)
        if reasons:
            report["contexts"][context_key] = {"status": "needs_more_data", "needs_more_data": True,
                                                "reasons": reasons, "fit": None, "validation": None, "golden": None}
            continue
        try:
            model = fit_preference_model(context["train"], baseline)
            baseline_logits = {split: [model.response_logit(row["features"], row["baseline_score"], personalized=False)
                                       for row in context[split]]
                               for split in ("validation", "golden")}
            learned_logits = {split: [model.response_logit(row["features"], row["baseline_score"], personalized=True)
                                      for row in context[split]]
                              for split in ("validation", "golden")}
            baseline_rank_scores = {split: [float(row["baseline_score"]) for row in context[split]]
                                    for split in ("validation", "golden")}
            learned_rank_scores = {split: [float(apply_preference(row["features"], row["baseline_score"], model, baseline)["score"])
                                           for row in context[split]]
                                   for split in ("validation", "golden")}
        except (RuntimeError, ValueError, TypeError, KeyError) as error:
            report["contexts"][context_key] = {"status": "fit_failed", "needs_more_data": False,
                                                "reasons": [str(error)], "fit": None, "validation": None, "golden": None}
            continue
        model_json = model.model_dump()
        diagnostics = model_json.get("diagnostics", {})
        if diagnostics.get("converged") is False:
            report["contexts"][context_key] = {"status": "fit_failed", "needs_more_data": False,
                                                "reasons": ["model_not_converged"], "fit": None,
                                                "validation": None, "golden": None}
            continue
        report["fit_performed"] = True
        report["contexts"][context_key] = {
            "status": "evaluated", "needs_more_data": False,
            "fit": {"optimizer_ran": True, "train_rows": len(context["train"]), "config": dict(FIXED_MODEL_CONFIG),
                     "baseline_offset": model_json.get("calibration_intercept") if isinstance(model_json, Mapping) else None,
                     "baseline_calibration_source": "train_only",
                     "baseline_calibration": {"intercept": model_json.get("calibration_intercept"),
                                               "slope": model_json.get("calibration_slope")}
                     if isinstance(model_json, Mapping) else None,
                     "diagnostics": diagnostics},
        }
        report["model_full_json"][context_key] = model_json
        report["model_count"] += 1
        for split in ("validation", "golden"):
            part = _part_report(context[split], baseline_logits[split], learned_logits[split],
                                baseline_rank_scores[split], learned_rank_scores[split])
            report["contexts"][context_key][split] = part
        context_summary = {
            "context_key": context_key,
            **{split: math.fsum(report["contexts"][context_key][split]["candidate_loss_improvements"])
               / len(context[split]) for split in ("validation", "golden")},
        }
        context_reports.append(context_summary)
        report["bootstrap"]["contexts"][context_key] = {
            split: _bootstrap(report["contexts"][context_key][split]["candidate_loss_improvements"], BOOTSTRAP_SEED)
            for split in ("validation", "golden")
        }
    identities = [normalized[item["context_key"]]["identity"] for item in context_reports]
    candidates = [row["candidate_key"] for item in context_reports
                  for split in ("train", "validation", "golden")
                  for row in normalized[item["context_key"]][split]]
    independent_contexts = (len({identity.get("owner_group") for identity in identities}) == len(identities)
                            and len({identity.get("vacancy_group") for identity in identities}) == len(identities)
                            and len(set(candidates)) == len(candidates))
    report["bootstrap"]["independent_context_groups"] = independent_contexts
    if len(context_reports) >= 6 and independent_contexts:
        report["bootstrap"]["macro_contexts"] = {
            split: _bootstrap([item[split] for item in context_reports], BOOTSTRAP_SEED)
            for split in ("validation", "golden")
        }
    if report["model_count"] == len(normalized) and normalized:
        state = "shadow_test_only"
    elif report["model_count"]:
        state = "partial_shadow_fit"
    elif any(value["status"] == "needs_more_data" for value in report["contexts"].values()):
        state = "needs_more_data"
    else:
        state = "fit_failed"
    report["status"] = report["state"] = state
    report["validated_diagnostics"].update({"fit_contexts": len(context_reports),
                                             "needs_more_data_contexts": sum(value["status"] == "needs_more_data" for value in report["contexts"].values()),
                                             "model_count": report["model_count"],
                                             "projection_hash": report["projection_hash"]})
    if not normalized:
        report["status"] = report["state"] = "needs_more_data"
    report["report_hash"] = digest({key: value for key, value in report.items() if key != "report_hash"})
    return report
