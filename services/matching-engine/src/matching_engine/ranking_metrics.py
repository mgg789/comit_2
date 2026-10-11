"""Offline retrieval metrics on sealed independent labels; never hiring-outcome claims."""

import argparse
import json
import math
import random
from pathlib import Path

from matching_engine.rerank_hash import digest


def ranking_metrics(grades: list[int | None], scores: list[float], *, k: int,
                    relevant_grade: int = 2) -> dict[str, float | int | None]:
    """Evaluate grade>=2 relevance; unknown labels are not silently negative.

    Precision uses exactly k slots (short lists cannot inflate it). Binary and
    graded metrics are undefined with any unjudged candidate; judged coverage
    stays available. AP@k uses min(k,total relevant) as its denominator.
    """
    if len(grades) != len(scores) or k < 1 or relevant_grade not in (1, 2, 3):
        raise ValueError("invalid metric dimensions or threshold")
    if any(g is not None and (type(g) is not int or not 0 <= g <= 3) for g in grades):
        raise ValueError("grades must be 0..3 or unjudged")
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("scores must be finite")
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    top = order[:k]
    result: dict[str, float | int | None] = {"precision": None, "recall": None, "hit_rate": None,
        "reciprocal_rank": None, "average_precision": None, "ndcg": None,
        "judged_coverage": sum(grades[i] is not None for i in top) / len(top) if top else 0.,
        "returned": len(top), "relevant_total": sum(g is not None and g >= relevant_grade for g in grades)}
    if not grades or any(g is None for g in grades):
        return result
    binary = [grades[i] >= relevant_grade for i in top]
    count = sum(binary)
    total = result["relevant_total"]
    result.update(precision=count / k, recall=count / total if total else None,
                  hit_rate=float(count > 0), reciprocal_rank=next((1 / i for i, hit in enumerate(binary, 1) if hit), 0.))
    hits, ap = 0, 0.
    for position, hit in enumerate(binary, 1):
        if hit:
            hits += 1
            ap += hits / position
    result["average_precision"] = ap / min(k, total) if total else None
    def dcg(indices: list[int]) -> float:
        return math.fsum((2 ** grades[i] - 1) / math.log2(position + 2) for position, i in enumerate(indices[:k]))
    ideal = dcg(sorted(order, key=lambda i: -grades[i]))
    result["ndcg"] = dcg(order) / ideal if ideal else None
    return result


def _summary(values: list[float], *, seed: int = 42) -> dict:
    if not values:
        return {"mean": None, "families": 0, "ci95": None}
    if len(values) == 1:
        return {"mean": values[0], "families": 1, "ci95": None}
    rng = random.Random(seed)
    draws = sorted(math.fsum(rng.choices(values, k=len(values))) / len(values) for _ in range(2000))
    return {"mean": math.fsum(values) / len(values), "families": len(values),
            "ci95": [draws[49], draws[1949]]}


def evaluate_rankings(families: list[dict], *, cutoffs: tuple[int, ...] = (1, 3, 5, 10)) -> dict:
    """Macro family metrics and paired-delta uncertainty, with explicit sparse slices.

    Each family supplies independently collected grades, baseline/model scores,
    direction and slice tags. Bootstrap resamples whole families, never rows.
    """
    if not families or len({f["family_id"] for f in families}) != len(families):
        raise ValueError("empty or duplicate evaluation families")
    rows = []
    for family in families:
        row = {"family_id": family["family_id"], "slices": [family["direction"], *family.get("slice_tags", [])], "cutoffs": {}}
        for k in cutoffs:
            row["cutoffs"][str(k)] = {name: ranking_metrics(family["grades"], family[name], k=k)
                                     for name in ("baseline", "model")}
        rows.append(row)
    def aggregate(subset: list[dict]) -> dict:
        output = {}
        for k in cutoffs:
            metrics = {}
            for name in ("precision", "recall", "hit_rate", "reciprocal_rank", "average_precision", "ndcg", "judged_coverage"):
                pairs = [(r["cutoffs"][str(k)]["baseline"][name], r["cutoffs"][str(k)]["model"][name]) for r in subset]
                known = [(a, b) for a, b in pairs if a is not None and b is not None]
                metrics[name] = {"baseline": _summary([a for a, _ in known]), "model": _summary([b for _, b in known]),
                                 "paired_delta": _summary([b - a for a, b in known])}
            output[str(k)] = metrics
        return output
    return {"protocol": {"version": "retrieval-metrics-v1", "label_source": "sealed synthetic teacher labels",
             "relevant_grade": 2, "precision_denominator": "k", "unjudged_policy": "undefined; report coverage",
             "uncertainty": "2000 paired family bootstrap draws; descriptive, no multiple-testing claim",
             "real_hiring_outcomes": False, "training_or_activation": False},
            "family_count": len(rows), "aggregate": aggregate(rows),
            "slices": {tag: {"families": len(subset := [r for r in rows if tag in r["slices"]]),
                             "supported": len(subset) >= 3, "metrics": aggregate(subset)}
                       for tag in sorted({tag for row in rows for tag in row["slices"]})}, "families": rows}


def evaluate_sealed(dataset_path: Path, artifact_path: Path) -> dict:
    """Verify sealed evaluation-only dataset and artifact, then evaluate without rewriting either."""
    from matching_engine.rerank_features import FeatureVector
    from matching_engine.rerank_hard_benchmark import _artifact_scores
    from matching_engine.rerank_model import validate_artifact
    dataset = json.loads(dataset_path.read_text())
    if dataset.get("dataset_hash") != digest({k: v for k, v in dataset.items() if k != "dataset_hash"}):
        raise ValueError("dataset seal mismatch")
    if dataset.get("evaluation_only") is not True or dataset.get("training_started") is not False:
        raise ValueError("dataset is not a qualified independent evaluation cohort")
    artifact = json.loads(artifact_path.read_text())
    validate_artifact(artifact)
    manifest = json.loads(dataset_path.with_name("manifest.json").read_text())
    if (manifest.get("manifest_hash") != digest({k: v for k, v in manifest.items() if k != "manifest_hash"})
            or dataset["manifest_hash"] != manifest["manifest_hash"]
            or artifact["content_hash"] != manifest["artifact_under_test"]["content_hash"]):
        raise ValueError("manifest/artifact mismatch")
    families = []
    for record in dataset["families"]:
        if record["record_hash"] != digest({k: v for k, v in record.items() if k != "record_hash"}):
            raise ValueError("family seal mismatch")
        if record.get("quarantined") is True:
            continue
        vectors = [FeatureVector(**entry) for entry in record["feature_record"]["vectors"]]
        if any(v.schema_hash != artifact["feature_schema_hash"] for v in vectors):
            raise ValueError("feature schema mismatch")
        families.append({"family_id": record["manifest"]["family_id"], "direction": record["manifest"]["direction"],
            "slice_tags": [record["manifest"]["branch"]], "grades": record["grades"],
            "baseline": record["feature_record"]["main_scores"], "model": _artifact_scores(artifact, vectors)})
    report = evaluate_rankings(families)
    report["label_quality"] = {**dataset["quality"], "input_families": len(dataset["families"]),
                               "policy": "quarantined families excluded; no activation claim"}
    report["provenance"] = {"dataset_hash": dataset["dataset_hash"], "manifest_hash": manifest["manifest_hash"],
                             "artifact_hash": artifact["content_hash"], "dataset_version": dataset["version"]}
    report["report_hash"] = digest(report)
    return report


def main() -> None:
    """Write a reproducible additional report without modifying sealed benchmark artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_sealed(args.dataset, args.artifact)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"families": report["family_count"], "report_hash": report["report_hash"]}))


if __name__ == "__main__":
    main()
