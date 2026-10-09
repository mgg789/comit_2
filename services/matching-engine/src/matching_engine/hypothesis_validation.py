"""Pre-registered validation of the hypothesis exploration layer.

Three frozen instruments, evaluation-only, no runtime policy changes:

1. Prevalence: how many comparable research pairs the real
   hypothesis_policy.comparable_pairs() finds on the sealed candidate pools
   (hard benchmark v7, 84 candidates; grounded cohort v9, 420 candidates) -
   under the live policy and under clearly labeled offline relaxation
   scenarios. Module constants are patched only inside the probe and always
   restored; the live policy hash never changes.
2. Decisive funnel: research pairs become decisive evidence only when the
   employer likes one side and dislikes the other. With an explicit assumed
   decisive rate, the funnel reports the pool regenerations needed for one
   frozen 20-decisive batch per family - the honest demo-scale feasibility
   number.
3. Verdict power: Monte Carlo over seeded 20-decisive batches through the real
   hypothesis_policy.summarize(): P(supported) / P(not_supported) / P(unclear)
   for true support probabilities 0.5 / 0.65 / 0.8 / 0.95 under the
   Bonferroni-corrected Wilson interval.

The v9 dataset hash and the hard benchmark manifest hash are verified before
any measurement; the packaged artifact is not involved (hypothesis comparisons
use feature snapshots, not the R0 artifact).
"""

import argparse
import json
import math
from pathlib import Path

import matching_engine.hypothesis_policy as policy
from matching_engine.rerank_diagnostics import _read, _seal
from matching_engine.rerank_hash import digest

VERSION = "hypothesis-validation-v1"
HARD_SOURCE = "rerank-hard-benchmark-v7"
V9_SOURCE = "rerank-grounded-cohort-v9"
SEED = 42
POWER_SEEDS = 2000
DECISIVE_BATCH = policy.MIN_DECISIVE_PAIRS
P_GRID = (0.5, 0.65, 0.8, 0.95)
ASSUMED_DECISIVE_RATE = 0.25
SCENARIOS = (
    {"id": "live", "contrast": None, "nuisance_distance": None,
     "nuisance_component": None, "utility": None},
    {"id": "contrast_010", "contrast": 0.10, "nuisance_distance": None,
     "nuisance_component": None, "utility": None},
    {"id": "contrast_010_nuisance_030", "contrast": 0.10, "nuisance_distance": 0.30,
     "nuisance_component": 0.40, "utility": None},
    {"id": "relaxed_all", "contrast": 0.10, "nuisance_distance": 0.30,
     "nuisance_component": 0.40, "utility": 0.60},
)
_TUNABLES = ("MIN_CONTRAST", "MAX_NUISANCE_DISTANCE", "MAX_NUISANCE_COMPONENT",
             "MAX_UTILITY_LOSS")


def _load_hard(directory: Path) -> list[dict]:
    """Snapshots from the sealed hard benchmark manifest."""
    manifest = _read(directory / HARD_SOURCE / "manifest.json")
    if manifest.get("manifest_hash") != digest(
            {k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("hard benchmark manifest hash mismatch")
    pool = []
    for item in manifest["families"]:
        record = item["feature_record"]
        for index, vector in enumerate(record["vectors"]):
            pool.append({"candidate_id": item["family_id"] + "/" + str(index),
                         "features": {"names": vector["names"], "values": vector["values"],
                                      "available": vector["available"],
                                      "schema_hash": vector["schema_hash"]},
                         "score": record["main_scores"][index], "snapshot": {}})
    return pool


def _load_v9(directory: Path) -> list[dict]:
    """Snapshots from the sealed grounded cohort dataset."""
    dataset = _read(directory / V9_SOURCE / "dataset.json")
    if dataset.get("dataset_hash") != digest(
            {k: v for k, v in dataset.items() if k != "dataset_hash"}):
        raise ValueError("v9 dataset hash mismatch")
    pool = []
    for family in dataset["families"]:
        family_id = family["manifest"]["family_id"]
        for index, vector in enumerate(family["vectors"]):
            pool.append({"candidate_id": family_id + "/" + str(index),
                         "features": {"names": vector["names"], "values": vector["values"],
                                      "available": vector["available"],
                                      "schema_hash": vector["schema_hash"]},
                         "score": family["main_scores"][index], "snapshot": {}})
    return pool


def _counts(pairs: list[dict]) -> dict:
    counts = {family: 0 for family in policy.FAMILIES}
    for pair in pairs:
        counts[pair["hypothesis_id"]] += 1
    return {family: count for family, count in counts.items() if count}


def _scenario_pairs(pool: list[dict], scenario: dict) -> dict:
    """Run comparable_pairs under a relaxation scenario; always restore constants."""
    saved = {name: getattr(policy, name) for name in _TUNABLES}
    try:
        if scenario["contrast"] is not None:
            policy.MIN_CONTRAST = scenario["contrast"]
        if scenario["nuisance_distance"] is not None:
            policy.MAX_NUISANCE_DISTANCE = scenario["nuisance_distance"]
        if scenario["nuisance_component"] is not None:
            policy.MAX_NUISANCE_COMPONENT = scenario["nuisance_component"]
        if scenario["utility"] is not None:
            policy.MAX_UTILITY_LOSS = scenario["utility"]
        pairs = policy.comparable_pairs(pool, None)
    finally:
        for name, value in saved.items():
            setattr(policy, name, value)
    return {"total": len(pairs), "by_family": _counts(pairs),
            "scenario": {key: value for key, value in scenario.items() if key != "id"}}


def funnel(scenario_totals: dict, families: int) -> dict:
    """Pool regenerations needed for one frozen batch, per the explicit assumption."""
    result = {}
    for scenario_id, total in scenario_totals.items():
        pairs_per_family = total / max(families, 1)
        decisive_per_pass = pairs_per_family * ASSUMED_DECISIVE_RATE
        needed = math.ceil(DECISIVE_BATCH / decisive_per_pass) if decisive_per_pass > 0 else None
        result[scenario_id] = {"pairs_per_pass": round(pairs_per_family, 2),
                               "assumed_decisive_rate": ASSUMED_DECISIVE_RATE,
                               "pool_passes_for_batch": needed}
    return result


def power(directory: Path) -> dict:
    """Monte Carlo power of the frozen verdict rule through real summarize()."""
    import random

    rng = random.Random(int(digest(VERSION + ":power")[:16], 16))
    result = {}
    for probability in P_GRID:
        counts = {"supported": 0, "not_supported": 0, "unclear": 0, "not_testable": 0}
        for seed in range(POWER_SEEDS):
            rows = []
            for index in range(DECISIVE_BATCH):
                supports = rng.random() < probability
                rows.append({"hypothesis_id": "H2",
                             "comparison_id": "s" + str(seed) + "-" + str(index),
                             "test_action": "like" if supports else "dislike",
                             "control_action": "dislike" if supports else "like"})
            verdict = policy.summarize(rows)
            status = next(row["status"] for row in verdict if row["hypothesis_id"] == "H2")
            counts[status] = counts.get(status, 0) + 1
        result[str(probability)] = {key: round(value / POWER_SEEDS, 4)
                                    for key, value in counts.items()}
    _ = directory
    return result


def run(directory: Path) -> dict:
    """Seal the frozen manifest, compute and seal the validation report."""
    target = directory / VERSION
    target.mkdir(parents=True, exist_ok=True)
    hard_manifest = _read(directory / HARD_SOURCE / "manifest.json")
    v9_dataset = _read(directory / V9_SOURCE / "dataset.json")
    manifest = {
        "version": VERSION,
        "purpose": ("Pre-registered validation of hypothesis exploration: pair "
                    "prevalence on sealed pools, decisive funnel assumption, "
                    "verdict power Monte Carlo; evaluation-only"),
        "pools": {HARD_SOURCE: {"manifest_hash": hard_manifest["manifest_hash"],
                                "candidates": 84},
                  V9_SOURCE: {"dataset_hash": v9_dataset["dataset_hash"],
                              "candidates": 420}},
        "scenarios": SCENARIOS,
        "decisive_batch": DECISIVE_BATCH,
        "assumed_decisive_rate": ASSUMED_DECISIVE_RATE,
        "assumption_note": ("decisive rate is an explicit demo-scale assumption, "
                            "not a measured quantity; no reaction telemetry exists"),
        "power_seeds": POWER_SEEDS,
        "p_grid": list(P_GRID),
        "seed": SEED,
        "snapshot_limits": ("sealed pools carry no narrative_facts/category/"
                            "semantic_space_id, so H5/H6/H10 are structurally "
                            "unmeasurable here; the probe covers H1-H4, H7, H8"),
        "code_hashes": {name: digest((Path(__file__).parent / name).read_bytes().hex())
                        for name in ("hypothesis_policy.py",)},
    }
    manifest["manifest_hash"] = digest(manifest)
    _seal(target / "manifest.json", manifest)
    report_path = target / "report.json"
    if report_path.exists():
        report = _read(report_path)
        if report.get("report_hash") != digest(
                {k: v for k, v in report.items() if k != "report_hash"}):
            raise ValueError("sealed hypothesis validation report changed")
        return report
    pools = {HARD_SOURCE: _load_hard(directory), V9_SOURCE: _load_v9(directory)}
    prevalence = {}
    totals = {}
    for source, pool in pools.items():
        for scenario in SCENARIOS:
            outcome = _scenario_pairs(pool, scenario)
            prevalence.setdefault(source, {})[scenario["id"]] = outcome
            totals[(source, scenario["id"])] = outcome["total"]
    report = {
        "version": VERSION, "manifest_hash": manifest["manifest_hash"],
        "prevalence": prevalence,
        "funnel": {source: funnel({scenario["id"]: totals[(source, scenario["id"])]
                                   for scenario in SCENARIOS}, len(pool))
                   for source, pool in pools.items()},
        "power": power(directory),
        "live_policy_hash": policy.policy_hash(),
        "runtime_changed": False,
        "evaluation_only": True,
    }
    report["report_hash"] = digest({k: v for k, v in report.items() if k != "report_hash"})
    _seal(report_path, report)
    return report


def main() -> None:
    """Run or re-verify the sealed hypothesis validation; offline, no credentials."""
    parser = argparse.ArgumentParser(description="Validation for hypothesis exploration")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    args = parser.parse_args()
    report = run(args.directory)
    summary = {"version": report["version"], "report_hash": report["report_hash"],
               "live_policy_hash": report["live_policy_hash"]}
    if args.command == "verify":
        again = run(args.directory)
        if again != report:
            raise SystemExit("hypothesis validation report is not reproducible")
        summary["verified"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for source, scenarios in report["prevalence"].items():
        print(source, json.dumps({sid: row["total"] for sid, row in scenarios.items()}))
    print("power", json.dumps(report["power"]))


if __name__ == "__main__":
    main()
