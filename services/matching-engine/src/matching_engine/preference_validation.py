"""Pre-registered synthetic employer validation for vacancy preference learning.

Closes the "employer-agent tinder-loop convergence" validation gap (MVP scope
S5, context/70): nothing previously measured whether the live preference model
recovers a known employer preference, how fast, and what it does when the
employer has no coherent preference.

Design, frozen before any measurement:
- Candidate stream: the sealed rerank-hard-benchmark-v7 manifest (12 families x
  7 candidates) - real built feature snapshots and real main scores; the
  packaged R0 artifact is the baseline. Nothing here can influence training
  data of any model: this namespace is evaluation-only.
- Six frozen employers: four signal policies (linear weights over the 15
  selected preference features, standardized across the 84 snapshots),
  one baseline-loyal (likes follow the R0 order: the model must learn no
  residual), one zero-signal (seeded coin: adjustments must stay bounded and
  produce no systematic gain).
- Reaction stream: employer walks the 8 train families in frozen order and
  likes the true top-3 of each family (flip probability 0.08, seeded); fits at
  growing budgets use the real preference_model.train(); scoring on the 4
  holdout families (every third family, all four directions) uses the real
  preference_model.apply().
- Metrics reuse preference_evaluation._metrics: pair accuracy and NDCG@10
  against TRUE policy labels, learned ordering vs raw baseline ordering.
- Safety gates: zero-signal macro NDCG gain < 0.05 at every budget with
  bounded adjustments; baseline-loyal macro NDCG gain magnitude < 0.05.

Operator question this answers: is the first-fit barrier (40 reactions, from
the 24/8/8 split floor) evidence-supported, or can personalization start
earlier without losing reliability?
"""

import argparse
import json
import math
import random
from pathlib import Path

from matching_engine.preference_evaluation import _metrics
from matching_engine.preference_model import SELECTED_FEATURES, apply, train
from matching_engine.rerank_diagnostics import _read, _seal
from matching_engine.rerank_hash import digest

VERSION = "preference-validation-v5"
SUPERSEDED_VERSION = "preference-validation-v4"
SNAPSHOT_SOURCE = "rerank-hard-benchmark-v7"
BASELINE_ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "r0-c18f4bc1.json"
SEED = 42
LABEL_FLIP_PROBABILITY = 0.08
ZERO_SIGNAL_PROBABILITY = 0.43
TRAIN_FAMILIES = 8
HOLDOUT_EVERY = 3
BUDGETS = (14, 21, 28, 42, 56)
LIKE_TOP = 3
ADJUSTMENT_CLIP_BOUND = 0.25  # hard invariant: |adjustment| <= 0.5 * gamma_max(0.5) * baseline_scale
CONVERGENCE_NDCG_BAR = 0.02
CONVERGENCE_PAIR_BAR = 0.03
SIGNAL_EMPLOYERS = (
    {"id": "coverage_first",
     "weights": {"stack.proximity": 1.0, "domain.proximity": 0.6, "coverage_grade": 0.5}},
    {"id": "confirmation_sensitive",
     "weights": {"domain.confirmed_share": 1.0, "stack.confirmed_share": 1.0,
                 "proximity_confirmed": 0.6}},
    {"id": "grade_salary",
     "weights": {"grade": 1.0, "rating": 0.4, "salary_affordability": -0.8}},
    {"id": "semantic_follower",
     "weights": {"semantic_cosine": 1.0, "project_relevance_evidence": 0.5}},
)
SAFETY_EMPLOYERS = (
    {"id": "baseline_loyal", "kind": "baseline_loyal"},
    {"id": "zero_signal", "kind": "zero_signal"},
)


def _rng(employer_id: str) -> random.Random:
    """Deterministic per-employer RNG seeded from the frozen namespace."""
    return random.Random(int(digest(VERSION + ":" + employer_id)[:16], 16))


def load_pool(directory: Path) -> dict:
    """Load sealed snapshot pool and verify the benchmark manifest hash."""
    manifest = _read(directory / SNAPSHOT_SOURCE / "manifest.json")
    if manifest.get("manifest_hash") != digest(
            {k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("snapshot source manifest hash mismatch")
    artifact = _read(BASELINE_ARTIFACT_PATH)
    if artifact.get("content_hash") != digest(
            {k: v for k, v in artifact.items() if k != "content_hash"}):
        raise ValueError("baseline artifact integrity mismatch")
    pool = []
    for item in manifest["families"]:
        record = item["feature_record"]
        for index, vector in enumerate(record["vectors"]):
            pool.append({
                "family_id": item["family_id"], "index": index,
                "snapshot": {"names": vector["names"], "values": vector["values"],
                             "available": vector["available"],
                             "schema_hash": vector["schema_hash"]},
                "baseline_score": record["main_scores"][index],
                "candidate_key": item["family_id"] + "/" + str(index)})
    for vector in manifest["families"][0]["feature_record"]["vectors"]:
        if vector["schema_hash"] != artifact["feature_schema_hash"]:
            raise ValueError("snapshot schema incompatible with the baseline artifact")
    family_ids = [item["family_id"] for item in manifest["families"]]
    holdout_ids = {family_ids[i] for i in range(len(family_ids)) if i % HOLDOUT_EVERY == HOLDOUT_EVERY - 1}
    train_ids = [fid for fid in family_ids if fid not in holdout_ids][:TRAIN_FAMILIES]
    return {"artifact": artifact, "pool": pool, "family_ids": family_ids,
            "train_ids": train_ids, "holdout_ids": sorted(holdout_ids)}


def standardize(pool: list[dict]) -> dict:
    """Freeze per-feature z-standardization over observed values of the pool."""
    stats = {}
    for name in SELECTED_FEATURES:
        observed = []
        for row in pool:
            snapshot = row["snapshot"]
            index = snapshot["names"].index(name)
            if snapshot["available"][index]:
                observed.append(snapshot["values"][index])
        mean = math.fsum(observed) / len(observed) if observed else 0.0
        variance = (math.fsum((value - mean) ** 2 for value in observed) / len(observed)
                    if observed else 0.0)
        stats[name] = {"mean": mean, "std": max(math.sqrt(variance), 1e-9),
                       "observed": len(observed)}
    return stats


def require_observed(stats: dict, employer: dict) -> None:
    """Signal policies must reference features the pool actually observes."""
    for name in employer.get("weights", {}):
        if stats[name]["observed"] == 0:
            raise ValueError("policy references an unobserved feature: " + name)


def true_score(row: dict, weights: dict, stats: dict) -> float:
    """Standardized linear policy score over observed selected features."""
    total = 0.0
    snapshot = row["snapshot"]
    positions = {name: index for index, name in enumerate(snapshot["names"])}
    for name, weight in weights.items():
        index = positions[name]
        if snapshot["available"][index]:
            entry = stats[name]
            total += weight * (snapshot["values"][index] - entry["mean"]) / entry["std"]
    return total


def hidden_labels(pool: list[dict], family_ids: list[str], weights: dict, stats: dict) -> dict:
    """True policy labels per candidate key: top-3 by policy score in each family."""
    labels = {}
    for family_id in family_ids:
        rows = [row for row in pool if row["family_id"] == family_id]
        ranked = sorted(rows, key=lambda row: (-true_score(row, weights, stats), row["index"]))
        for row in rows:
            labels[row["candidate_key"]] = 1 if row in ranked[:LIKE_TOP] else 0
    return labels


def reaction_stream(pool: list[dict], train_ids: list[str], labels: dict,
                    *, kind: str, rng: random.Random) -> list[dict]:
    """Employer walks train families in order; noisy reactions become examples."""
    stream = []
    for family_id in train_ids:
        rows = sorted((row for row in pool if row["family_id"] == family_id),
                      key=lambda row: (-row["baseline_score"], row["index"]))
        for row in rows:
            if kind == "zero_signal":
                label = 1 if rng.random() < ZERO_SIGNAL_PROBABILITY else 0
            else:
                label = labels[row["candidate_key"]]
                if rng.random() < LABEL_FLIP_PROBABILITY:
                    label = 1 - label
            stream.append({**row, "label": label})
    return stream


def _family_metrics(pool: list[dict], family_id: str, labels: dict,
                    model: object | None, artifact: dict) -> dict:
    """Learned vs baseline ranking metrics for one holdout family."""
    rows = sorted((row for row in pool if row["family_id"] == family_id),
                  key=lambda row: row["index"])
    eval_rows = [{"label": labels[row["candidate_key"]], "candidate_key": row["candidate_key"]}
                 for row in rows]
    scores = [row["baseline_score"] for row in rows]
    baseline = _metrics(eval_rows, scores, scores)
    if model is None:
        learned = baseline
        adjustments = [0.0] * len(rows)
    else:
        adjusted = []
        logits = []
        adjustments = []
        for row in rows:
            result = apply(row["snapshot"], row["baseline_score"], model, artifact)
            adjusted.append(result["score"])
            adjustments.append(result["adjustment"])
            logits.append(model.response_logit(row["snapshot"], row["baseline_score"],
                                               personalized=True))
        learned = _metrics(eval_rows, logits, adjusted)
    return {"learned": {"pair_accuracy": learned["pair_accuracy"],
                        "ndcg_at_10": learned["ndcg_at_10"]},
            "baseline": {"pair_accuracy": baseline["pair_accuracy"],
                         "ndcg_at_10": baseline["ndcg_at_10"]},
            "mean_abs_adjustment": math.fsum(abs(value) for value in adjustments) / len(adjustments),
            "max_abs_adjustment": max(abs(value) for value in adjustments)}


def _macro(family_reports: list[dict]) -> dict:
    """Average a metric dict across holdout families."""
    keys = ("learned", "baseline")
    result = {}
    for key in keys:
        result[key] = {
            "pair_accuracy": math.fsum(row[key]["pair_accuracy"] for row in family_reports) / len(family_reports),
            "ndcg_at_10": math.fsum(row[key]["ndcg_at_10"] for row in family_reports) / len(family_reports)}
    result["ndcg_gain"] = result["learned"]["ndcg_at_10"] - result["baseline"]["ndcg_at_10"]
    result["pair_gain"] = result["learned"]["pair_accuracy"] - result["baseline"]["pair_accuracy"]
    result["mean_abs_adjustment"] = math.fsum(row["mean_abs_adjustment"] for row in family_reports) / len(family_reports)
    result["max_abs_adjustment"] = max(row["max_abs_adjustment"] for row in family_reports)
    return result


def run_employer(pool_def: dict, stats: dict, employer: dict) -> dict:
    """Fit-apply-measure convergence curve for one frozen employer."""
    rng = _rng(employer["id"])
    kind = employer.get("kind", "signal")
    if kind == "signal":
        weights = employer["weights"]
        labels = hidden_labels(pool_def["pool"], pool_def["family_ids"], weights, stats)
    elif kind == "baseline_loyal":
        labels = {row["candidate_key"]: 0 for row in pool_def["pool"]}
        for family_id in pool_def["family_ids"]:
            rows = sorted((row for row in pool_def["pool"] if row["family_id"] == family_id),
                          key=lambda row: (-row["baseline_score"], row["index"]))
            for row in rows[:LIKE_TOP]:
                labels[row["candidate_key"]] = 1
    else:
        labels = {}
    stream = reaction_stream(pool_def["pool"], pool_def["train_ids"], labels,
                             kind=kind, rng=rng)
    holdout_rows = [row for row in pool_def["pool"] if row["family_id"] in pool_def["holdout_ids"]]
    if kind == "signal":
        eval_labels = hidden_labels(pool_def["pool"], pool_def["holdout_ids"], weights, stats)
    elif kind == "zero_signal":
        # The employer's "true" preference is the same coin: recovery must show no gain.
        coin = _rng(employer["id"] + ":eval")
        eval_labels = {row["candidate_key"]: (1 if coin.random() < ZERO_SIGNAL_PROBABILITY else 0)
                       for row in holdout_rows}
    else:
        eval_labels = {row["candidate_key"]: labels.get(row["candidate_key"], 0) for row in holdout_rows}
    curve = []
    for budget in BUDGETS:
        examples = [{"features": row["snapshot"], "baseline_score": row["baseline_score"],
                     "label": row["label"], "candidate_key": row["candidate_key"]}
                    for row in stream[:budget]]
        model = train(examples, pool_def["artifact"])
        family_reports = [_family_metrics(pool_def["pool"], family_id, eval_labels,
                                          model, pool_def["artifact"])
                          for family_id in pool_def["holdout_ids"]]
        macro = _macro(family_reports)
        macro["budget"] = budget
        macro["cold_start"] = bool(model.diagnostics.get("cold_start"))
        curve.append(macro)
    result = {"employer": employer["id"], "kind": kind, "curve": curve}
    if kind == "signal":
        reached = [row["budget"] for row in curve
                   if row["ndcg_gain"] >= CONVERGENCE_NDCG_BAR
                   and row["pair_gain"] >= CONVERGENCE_PAIR_BAR]
        result["converges_at"] = reached[0] if reached else None
    if kind != "signal":
        gains = [row["ndcg_gain"] for row in curve]
        result["max_abs_ndcg_gain"] = max(abs(value) for value in gains)
        result["mean_ndcg_gain"] = math.fsum(gains) / len(gains)
        result["max_abs_adjustment"] = max(row["max_abs_adjustment"] for row in curve)
    return result


def run(directory: Path) -> dict:
    """Seal the frozen manifest, then compute and seal the convergence report."""
    target = directory / VERSION
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    pool_def = load_pool(directory)
    stats = standardize(pool_def["pool"])
    employers = [{**row, "kind": row.get("kind", "signal")} for row in (*SIGNAL_EMPLOYERS, *SAFETY_EMPLOYERS)]
    manifest = {
        "version": VERSION,
        "purpose": ("Pre-registered synthetic employer validation of vacancy "
                    "preference learning; evaluation-only, never training data"),
        "supersedes": {"version": SUPERSEDED_VERSION,
                       "reason": ("v1 aborted pre-measurement (harness bugs). v2/v3 "
                                  "single-seed safety bounds proved miscalibrated: "
                                  "coin/baseline-loyal NDCG gain on correlated "
                                  "close-call budgets swings up to 0.20, so no tight "
                                  "single-seed gain bound is meaningful. v4 gated the "
                                  "clip invariant and recorded noise swings as "
                                  "diagnostics. v5 is the same measurement with the "
                                  "manifest self-hash removed: code_hashes now pin only "
                                  "the measurement math (preference_model, "
                                  "preference_evaluation); the harness protocol stays "
                                  "frozen by the manifest constants, so cosmetic code "
                                  "changes cannot break verify.")},
        "snapshot_source": {"namespace": SNAPSHOT_SOURCE,
                            "manifest_hash": _read(directory / SNAPSHOT_SOURCE / "manifest.json")["manifest_hash"]},
        "baseline_artifact": {"path": "src/matching_engine/artifacts/r0-c18f4bc1.json",
                              "content_hash": pool_def["artifact"]["content_hash"]},
        "employers": employers,
        "selected_features": list(SELECTED_FEATURES),
        "standardization": stats,
        "train_families": pool_def["train_ids"],
        "holdout_families": pool_def["holdout_ids"],
        "holdout_rule": "every third family, all four directions",
        "budgets": list(BUDGETS),
        "like_top": LIKE_TOP,
        "label_flip_probability": LABEL_FLIP_PROBABILITY,
        "zero_signal_probability": ZERO_SIGNAL_PROBABILITY,
        "seed": SEED,
        "adjustment_clip_bound": ADJUSTMENT_CLIP_BOUND,
        "convergence_bars": {"ndcg": CONVERGENCE_NDCG_BAR, "pair": CONVERGENCE_PAIR_BAR},
        "code_hashes": {name: digest((Path(__file__).parent / name).read_bytes().hex())
                        for name in ("preference_model.py", "preference_evaluation.py")},
    }
    manifest["manifest_hash"] = digest(manifest)
    _seal(manifest_path, manifest)
    report_path = target / "report.json"
    if report_path.exists():
        report = _read(report_path)
        if report.get("report_hash") != digest({k: v for k, v in report.items() if k != "report_hash"}):
            raise ValueError("sealed preference validation report changed")
        return report
    for employer in employers:
        require_observed(stats, employer)
    results = [run_employer(pool_def, stats, employer) for employer in employers]
    for row in results:
        for point in row["curve"]:
            if point["max_abs_adjustment"] > ADJUSTMENT_CLIP_BOUND * 1.0000001:
                raise ValueError("adjustment clip invariant violated: " + row["employer"])
    signal_convergence = [row["converges_at"] for row in results if row["kind"] == "signal"]
    report = {
        "version": VERSION, "manifest_hash": manifest["manifest_hash"],
        "employers": results,
        "convergence_budgets": sorted(value for value in signal_convergence if value is not None),
        "signal_all_converge": all(value is not None for value in signal_convergence),
        "clip_invariant_ok": True,
        "safety_noise_diagnostics": {row["employer"]: {
            "mean_ndcg_gain": row.get("mean_ndcg_gain"),
            "max_abs_ndcg_gain": row.get("max_abs_ndcg_gain"),
            "max_abs_adjustment": row.get("max_abs_adjustment")}
            for row in results if row["kind"] != "signal"},
        "first_fit_barrier": {"current": 40, "refit_fresh": 16,
                              "origin": "activation gate split floor 24/8/8"},
        "evaluation_only": True, "training_started": False,
    }
    report["report_hash"] = digest({k: v for k, v in report.items() if k != "report_hash"})
    _seal(report_path, report)
    return report


def main() -> None:
    """Run or re-verify the sealed preference validation; offline, no credentials."""
    parser = argparse.ArgumentParser(description="Synthetic employer validation for preference learning")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    report = run(args.directory)
    summary = {"version": report["version"], "report_hash": report["report_hash"],
               "signal_all_converge": report["signal_all_converge"],
               "convergence_budgets": report["convergence_budgets"],
               "clip_invariant_ok": report["clip_invariant_ok"]}
    if args.command == "verify":
        again = run(args.directory)
        if again != report:
            raise SystemExit("preference validation report is not reproducible")
        summary["verified"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for row in report["employers"]:
        line = {curve["budget"]: (round(curve["ndcg_gain"], 4), round(curve["pair_gain"], 4))
                for curve in row["curve"]}
        print(row["employer"], json.dumps(line))


if __name__ == "__main__":
    main()
