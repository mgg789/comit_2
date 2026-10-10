"""Bounded pairwise training, family-held-out evaluation and conservative tail calibration."""

import math
import warnings
from dataclasses import asdict, dataclass
from typing import Literal, get_args

from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import policy_hash
from matching_engine.models import Direction
from matching_engine.rerank_features import FeatureSchema, FeatureVector
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import validate_artifact
from matching_engine.rerank_protocol import expected_families

PairLabel = Literal["A", "B", "tie", "insufficient"]
Split = Literal["train", "validation", "calibration", "golden"]
SEED = 42
C_GRID = (.01, .1, 1., 10.)


@dataclass(frozen=True)
class TrainingFamily:
    """One independent scenario and all related profiles, zero-based pair indices.

    Grades 0..3 are independent suitability labels; None means unjudged.
    source_id identifies common ancestry and must never cross split boundaries.
    """

    family_id: str
    direction: Direction
    split: Split
    vectors: list[FeatureVector]
    main_scores: list[float]
    grades: list[int | None]
    pairs: list[tuple[int, int, PairLabel]]
    source_id: str | None = None
    slice_tags: tuple[str, ...] = ()


def validate_families(families: list[TrainingFamily], schema: FeatureSchema) -> None:
    """Reject source leakage, duplicate identities, corrupt vectors and resource expansion."""
    if not families or len(families) > 60 or sum(len(f.vectors) for f in families) > 600:
        raise ValueError("dataset exceeds family/profile budget or is empty")
    if sum(len(f.pairs) for f in families) > 3000:
        raise ValueError("dataset exceeds pair budget")
    ids: set[str] = set()
    ancestors: dict[str, str] = {}
    for family in families:
        if not family.family_id or family.family_id in ids:
            raise ValueError("duplicate or empty family id")
        ids.add(family.family_id)
        if family.split not in ("train", "validation", "calibration", "golden"):
            raise ValueError("unknown split")
        if family.direction not in get_args(Direction):
            raise ValueError("unknown specialization direction")
        source = family.source_id or family.family_id
        if source in ancestors and ancestors[source] != family.split:
            raise ValueError("source ancestry crosses splits")
        if source in ancestors:
            raise ValueError("all variants of one source belong to one independent family")
        ancestors[source] = family.split
        n = len(family.vectors)
        if not n or len(family.main_scores) != n or len(family.grades) != n:
            raise ValueError("family candidate dimensions invalid")
        if any(grade is not None and (type(grade) is not int or not 0 <= grade <= 3) for grade in family.grades):
            raise ValueError("grades must be independently labeled 0..3 or unknown")
        if not all(math.isfinite(score) for score in family.main_scores):
            raise ValueError("nonfinite main score")
        for vector in family.vectors:
            if (vector.schema_hash != schema.schema_hash or vector.names != schema.names
                    or len(vector.values) != len(schema.names) or len(vector.available) != len(schema.names)
                    or any(type(value) is not bool for value in vector.available)
                    or not all(math.isfinite(value) for value in vector.values)):
                raise ValueError("feature schema/dimensions mismatch")
        unordered: set[tuple[int, int]] = set()
        for a, b, label in family.pairs:
            if type(a) is not int or type(b) is not int or not 0 <= a < n or not 0 <= b < n or a == b:
                raise ValueError("invalid pair indices")
            key = (min(a, b), max(a, b))
            if key in unordered:
                raise ValueError("duplicate independent pair; swap audits are not training rows")
            unordered.add(key)
            if label not in ("A", "B", "tie", "insufficient"):
                raise ValueError("unknown pair label")
    if not any(f.split == "train" for f in families) or not any(f.split == "validation" for f in families):
        raise ValueError("train and validation splits are required")


def _ndcg(grades: list[int | None], scores: list[float]) -> float | None:
    known = [i for i, grade in enumerate(grades) if grade is not None]
    if not known or not any(grades[i] for i in known):
        return None
    observed_grades = [grade if grade is not None else 0 for grade in grades]

    def dcg(indices: list[int]) -> float:
        return math.fsum((2 ** observed_grades[i] - 1) / math.log2(position + 2)
                         for position, i in enumerate(indices[:10]))

    actual = sorted(known, key=lambda i: (-scores[i], i))
    ideal = sorted(known, key=lambda i: (-observed_grades[i], i))
    return dcg(actual) / dcg(ideal)


def _family_metrics(family: TrainingFamily, scores: list[float]) -> dict:
    decisive = [(a, b, label) for a, b, label in family.pairs if label in ("A", "B")]
    correct = sum((scores[a] > scores[b] if label == "A" else scores[b] > scores[a]) for a, b, label in decisive)
    return {"family_id": family.family_id, "direction": family.direction, "slice_tags": list(family.slice_tags),
            "ndcg": _ndcg(family.grades, scores), "pair_accuracy": correct / len(decisive) if decisive else None,
            "decisive_pairs": len(decisive), "correct_pairs": correct,
            "ties": sum(pair[2] == "tie" for pair in family.pairs),
            "insufficient": sum(pair[2] == "insufficient" for pair in family.pairs)}


def _average(values: list[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


def _bootstrap_low(values: list[float]) -> float:
    import numpy as np

    if len(values) < 2:
        return 0.0
    draws = np.random.default_rng(SEED).choice(values, size=(2000, len(values)), replace=True).mean(axis=1)
    return float(np.quantile(draws, .025))


def evaluate(families: list[TrainingFamily], scores: dict[str, list[float]]) -> dict:
    """Compare a fixed family cohort with the main baseline using paired family bootstrap."""
    rows = []
    for family in families:
        if len(scores[family.family_id]) != len(family.vectors) or not all(math.isfinite(x) for x in scores[family.family_id]):
            raise ValueError("invalid evaluation score dimensions/values")
        learned = _family_metrics(family, scores[family.family_id])
        baseline = _family_metrics(family, family.main_scores)
        learned["baseline_ndcg"] = baseline["ndcg"]
        learned["baseline_pair_accuracy"] = baseline["pair_accuracy"]
        rows.append(learned)
    ndcg = [r for r in rows if r["ndcg"] is not None and r["baseline_ndcg"] is not None]
    pairs = [r for r in rows if r["pair_accuracy"] is not None]
    ndcg_deltas = [r["ndcg"] - r["baseline_ndcg"] for r in ndcg]
    pair_deltas = [r["pair_accuracy"] - r["baseline_pair_accuracy"] for r in pairs]
    total_pairs = sum(r["decisive_pairs"] for r in rows)
    supported_slices = []
    for tag in sorted({tag for r in rows for tag in [r["direction"], *r["slice_tags"]]}):
        subset = [r for r in rows if tag == r["direction"] or tag in r["slice_tags"]]
        if len(subset) >= 3:
            supported_slices.append({"slice": tag, "families": len(subset),
                "ndcg_delta": _average([r["ndcg"] - r["baseline_ndcg"] for r in subset if r["ndcg"] is not None]),
                "pair_delta": _average([r["pair_accuracy"] - r["baseline_pair_accuracy"] for r in subset if r["pair_accuracy"] is not None])})
    result = {"golden_families": len(rows), "ndcg_families": len(ndcg), "pair_families": len(pairs),
              "minimum_decisive_pairs_per_family": min((r["decisive_pairs"] for r in rows), default=0),
              "macro_ndcg": _average([r["ndcg"] for r in ndcg]), "macro_ndcg_delta": _average(ndcg_deltas),
              "ndcg_delta_ci_low": _bootstrap_low(ndcg_deltas),
              "macro_pair_accuracy": _average([r["pair_accuracy"] for r in pairs]),
              "macro_pair_delta": _average(pair_deltas), "pair_delta_ci_low": _bootstrap_low(pair_deltas),
              "micro_pair_accuracy": sum(r["correct_pairs"] for r in rows) / max(1, total_pairs),
              "decisive_pairs": total_pairs, "supported_slices": supported_slices, "families": rows}
    result["passed"] = (len(rows) >= 12 and len(ndcg) == len(rows) and len(pairs) == len(rows)
                        and result["minimum_decisive_pairs_per_family"] >= 6
                        and result["macro_ndcg_delta"] >= .02 and result["ndcg_delta_ci_low"] > 0
                        and result["macro_pair_accuracy"] >= .65 and result["macro_pair_delta"] >= .03
                        and result["pair_delta_ci_low"] > 0
                        and not any(s["ndcg_delta"] < -.03 or s["pair_delta"] < -.03 for s in supported_slices))
    return result


def calibrate_tail(calibration: list[TrainingFamily], golden: list[TrainingFamily],
                   scores: dict[str, list[float]]) -> dict:
    """Retain 95% relevant calibration top-k and require supported golden Wilson >=90%."""
    import numpy as np

    def gaps(families: list[TrainingFamily]) -> list[float]:
        return [max(scores[f.family_id]) - score for f in families
                for score, grade in zip(scores[f.family_id], f.grades, strict=True)
                if grade is not None and grade >= 2]

    calibration_gaps, golden_gaps = gaps(calibration), gaps(golden)
    report = {"gap": None, "calibration_relevant": len(calibration_gaps), "golden_relevant": len(golden_gaps),
              "cohort": "reranked_main_top_k", "reason": "insufficient_relevant_support"}
    if len(calibration_gaps) < 50 or len(golden_gaps) < 50:
        return report
    threshold = float(np.quantile(calibration_gaps, .95, method="higher"))
    retained = sum(gap <= threshold for gap in golden_gaps)
    n, z = len(golden_gaps), 1.959963984540054
    p = retained / n
    lower = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    report.update({"golden_recall": p, "golden_wilson_low": lower,
                   "calibration_recall": sum(gap <= threshold for gap in calibration_gaps) / len(calibration_gaps)})
    if p >= .95 and lower >= .90:
        report.update({"gap": threshold, "reason": None})
    else:
        report["reason"] = "golden_tail_safety_failed"
    return report


def train(families: list[TrainingFamily], schema: FeatureSchema, metadata: dict) -> dict:
    """Train one linear target with frozen neighbors; return unapproved portable JSON.

    Optional sklearn is imported only here. Provider calls and data generation
    are never performed by this function. Golden metrics cannot select C.
    """
    import numpy as np
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression

    validate_families(families, schema)
    split_manifest = [{"family_id": f.family_id, "source_id": f.source_id or f.family_id,
                       "direction": f.direction, "split": f.split} for f in families]
    protocol_valid = sorted(split_manifest, key=lambda row: row["family_id"]) == sorted(expected_families(), key=lambda row: row["family_id"])
    test_only = metadata.get("require_protocol", True) is False
    if not protocol_valid and not test_only:
        raise ValueError("dataset does not match the sealed family protocol")
    if not 0 <= metadata.get("tuning_cycles", 0) <= 2:
        raise ValueError("tuning cycle budget exceeded")
    train_families = [f for f in families if f.split == "train"]
    train_vectors = [vector for family in train_families for vector in family.vectors]
    observed = np.asarray([vector.available for vector in train_vectors], dtype=bool)
    raw = np.asarray([vector.values for vector in train_vectors], dtype=float)
    count = observed.sum(axis=0)
    means = np.where(count > 0, np.where(observed, raw, 0).sum(axis=0) / np.maximum(count, 1), 0)
    variance = np.where(observed, (raw - means) ** 2, 0).sum(axis=0) / np.maximum(count, 1)
    scales = np.sqrt(variance)
    scales[scales == 0] = 1
    scaled = {f.family_id: np.where(np.asarray([v.available for v in f.vectors], dtype=bool),
                                    (np.asarray([v.values for v in f.vectors]) - means) / scales, 0)
              for f in families}
    differences, labels, weights = [], [], []
    for family in train_families:
        for a, b, label in family.pairs:
            if label not in ("A", "B"):
                continue
            difference = scaled[family.family_id][a] - scaled[family.family_id][b]
            differences.extend([difference, -difference])
            labels.extend([int(label == "A"), int(label == "B")])
            weights.extend([.5, .5])
    if not differences:
        raise ValueError("no decisive training comparisons")
    validation = [f for f in families if f.split == "validation"]
    candidates = []
    for c in C_GRID:
        learner = LogisticRegression(C=c, fit_intercept=False, solver="lbfgs", max_iter=2000, random_state=SEED)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            learner.fit(np.asarray(differences), np.asarray(labels), sample_weight=np.asarray(weights))
        coeff = learner.coef_[0]
        scores = {f.family_id: (scaled[f.family_id] @ coeff).tolist() for f in validation}
        metrics = evaluate(validation, scores)
        if metrics["ndcg_families"] != len(validation):
            raise ValueError("validation requires independent nonzero suitability grades in each family")
        candidates.append((metrics["macro_ndcg"], c, coeff, metrics))
    _, selected_c, coefficients, validation_report = max(candidates, key=lambda item: (item[0], -item[1]))
    if not np.isfinite(coefficients).all():
        raise ValueError("nonfinite fitted coefficients")
    scores = {f.family_id: (scaled[f.family_id] @ coefficients).tolist() for f in families}
    golden = [f for f in families if f.split == "golden"]
    gate = evaluate(golden, scores)
    receipt = metadata.get("label_validation", {})
    receipt_valid = (receipt.get("passed") is True and metadata.get("label_validation_hash") == digest(receipt))
    gate["label_validation_passed"] = receipt_valid
    gate["protocol_valid"] = protocol_valid
    gate["passed"] = gate["passed"] and receipt_valid and protocol_valid and not test_only
    tail = calibrate_tail([f for f in families if f.split == "calibration"], golden, scores)
    gate["tail"] = {"calibration_recall": tail.get("calibration_recall", 0),
                    "golden_relevant_count": tail["golden_relevant"],
                    "golden_recall": tail.get("golden_recall", 0),
                    "golden_wilson_low": tail.get("golden_wilson_low", 0),
                    "validated": tail["gap"] is not None}
    report = {"validation": validation_report, "golden": gate, "tail": tail}
    return validate_artifact({
        "model_version": metadata.get("model_version", "linear-pairwise-r0-experimental-v1"),
        "feature_names": list(schema.names), "feature_schema_hash": schema.schema_hash,
        "strict_schema_hash": schema.strict_schema_hash, "main_policy_hash": policy_hash(ScoringPolicy()),
        "coefficients": coefficients.tolist(), "means": means.tolist(), "scales": scales.tolist(),
        "tail_gap": tail["gap"], "dataset_hash": metadata.get("dataset_hash") or digest([asdict(f) for f in families]),
        "split_hash": digest(split_manifest),
        "label_cache_hash": metadata.get("label_cache_hash") or digest([{ "id": f.family_id, "pairs": f.pairs, "grades": f.grades} for f in families]),
        "evaluation_hash": digest(report), "gate": gate,
        "training": {"learner": "L2-pairwise-logistic", "C": selected_c, "C_grid": list(C_GRID),
                     "seed": SEED, "intercept": False, "mirror_weight": .5,
                     "independent_training_pairs": len(labels) // 2, "training_rows": len(labels),
                     "regularization_candidates": [{"C": c, "validation": metrics}
                                                   for _, c, _, metrics in candidates],
                     "scaling": "train-observed-mean-plus-availability:v1", "sklearn_version": sklearn.__version__,
                     "numpy_version": np.__version__, "split_manifest": split_manifest, "report": report,
                     "test_only": test_only, "label_validation_hash": metadata.get("label_validation_hash"),
                     "tuning_cycles": metadata.get("tuning_cycles", 0)},
    })
