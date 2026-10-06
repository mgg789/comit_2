"""Audit a cached partial sample and fit a non-activatable R0 without exposing golden predictions."""

import argparse
import fcntl
import json
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from matching_engine.rerank_diagnostics import _seal
from matching_engine.rerank_experiment import family_manifest, initial_schema
from matching_engine.rerank_features import feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import (
    RerankArtifact,
    promotion_eligible,
    scaled_features,
)
from matching_engine.rerank_sample_features import (
    SEED_PATH,
    rebuild_family_features,
    verify_cached_sources,
)
from matching_engine.rerank_training import (
    TrainingFamily,
    evaluate,
    train,
    validate_families,
)

VERSION = "rerank-current-sample-probe-v2"
EXPECTED_PROFILES = {**{f"backend-{i:02d}": 9 if i == 4 else 10 for i in range(1, 9)},
                     **{f"frontend-{i:02d}": 10 for i in range(1, 5)}}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_sample_shape(records: list[dict]) -> None:
    ids = [r["manifest"]["family_id"] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sample family id")
    if set(ids) != set(EXPECTED_PROFILES):
        raise ValueError("sample must contain the frozen twelve family identities")
    if any(len(r["traits"]) != EXPECTED_PROFILES[r["manifest"]["family_id"]] for r in records):
        raise ValueError("sample profile counts differ from the frozen 119 profiles")
    if Counter(r["manifest"]["split"] for r in records) != {"train": 8, "validation": 1, "calibration": 1, "golden": 2}:
        raise ValueError("sample split counts differ from 8/1/1/2")
    source_ids = [r["manifest"]["source_id"] for r in records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate sample source ancestry")


def _cycles(size: int, pairs: list) -> list[list[int]]:
    graph = {i: [] for i in range(size)}
    for a, b, label in pairs:
        if label in ("A", "B"):
            winner, loser = (a, b) if label == "A" else (b, a)
            graph[winner].append(loser)
    components, stack, active, indices, low = [], [], set(), {}, {}

    def visit(node: int) -> None:
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in graph[node]:
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                target = stack.pop()
                active.remove(target)
                component.append(target)
                if target == node:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for node in graph:
        if node not in indices:
            visit(node)
    return sorted(components)


def _ordering_ceiling(size: int, pairs: list) -> dict:
    if not 1 <= size <= 12:
        raise ValueError("ordering ceiling requires a bounded small family")
    edges = set()
    for a, b, label in pairs:
        if label in ("A", "B"):
            edges.add((a, b) if label == "A" else (b, a))
    best = [0] * (1 << size)
    for mask in range(1, 1 << size):
        best[mask] = max(best[mask ^ (1 << last)] + sum((first, last) in edges
                          for first in range(size) if first != last and mask & (1 << first))
                         for last in range(size) if mask & (1 << last))
    # Exact dynamic-programming upper bound for ANY scalar order, independent of features/model.
    correct = best[-1]
    return {"decisive_pairs": len(edges), "maximum_satisfiable_pairs": correct,
            "minimum_unavoidable_disagreements": len(edges) - correct,
            "maximum_scalar_pair_accuracy": correct / len(edges) if edges else None}


def audit_records(records: list[dict]) -> dict:
    """Count independent source families and scalar-label conflicts; never repair or relabel data.

    Exact profile or structured-fact duplication across splits is invalid,
    even when free-text descriptions differ.
    Cycles are reported as noisy supervision incompatible with a perfect scalar
    order; they do not silently remove any training comparison.
    """
    profiles, facts = defaultdict(list), defaultdict(list)
    rows = []
    for record in records:
        manifest = record["manifest"]
        for index, trait in enumerate(record["traits"]):
            location = {"family": manifest["family_id"], "split": manifest["split"], "index": index}
            profiles[digest(trait)].append(location)
            facts[digest({k: v for k, v in trait.items() if k != "description"})].append(location)
        cycles = _cycles(len(record["traits"]), record["pairs"])
        ceiling = _ordering_ceiling(len(record["traits"]), record["pairs"])
        rows.append({"family": manifest["family_id"], "source_id": manifest["source_id"],
                     "direction": manifest["direction"], "split": manifest["split"],
                     "profiles": len(record["traits"]), "pairs": len(record["pairs"]),
                     "labels": dict(Counter(p[2] for p in record["pairs"])),
                     "known_suitability_grades": sum(g is not None for g in record["grades"]),
                     "swap_correct": sum(record["swap_audits"]), "swap_total": len(record["swap_audits"]),
                     "cyclic_components": cycles, "scalar_order_ceiling": ceiling, "teacher": record["teacher"]})
    duplicate_profiles = [locations for locations in profiles.values() if len(locations) > 1]
    cross_split = [locations for locations in duplicate_profiles if len({r["split"] for r in locations}) > 1]
    if cross_split:
        raise ValueError("identical source profiles cross splits")
    if any(len({r["split"] for r in locations}) > 1 for locations in facts.values()):
        raise ValueError("identical structured facts cross splits")
    return {"families": len(records), "profiles": sum(r["profiles"] for r in rows),
            "splits": dict(Counter(r["split"] for r in rows)),
            "directions": dict(Counter(r["direction"] for r in rows)),
            "pairs": sum(r["pairs"] for r in rows),
            "labels": dict(Counter(p[2] for r in records for p in r["pairs"])),
            "grade_labels": dict(Counter("unknown" if g is None else str(g) for r in records for g in r["grades"])),
            "evidence_statuses": dict(Counter(t["status"] for r in records for t in r["traits"])),
            "declared_level_keys": sorted({k for r in records for t in r["traits"] for k in t["levels"]}),
            "duplicate_profile_groups": duplicate_profiles,
            "duplicate_fact_groups_without_description": sum(len(v) > 1 for v in facts.values()),
            "families_with_label_cycles": sum(bool(r["cyclic_components"]) for r in rows),
            "minimum_unavoidable_pair_disagreements": sum(r["scalar_order_ceiling"]["minimum_unavoidable_disagreements"] for r in rows),
            "family_rows": rows, "production_suitable": False,
            "suitability": "diagnostic_only", "label_repair_or_dropping": False,
            "independence_unit": "source family; comparisons sharing profiles are dependent"}


def _feature_coverage(families: list[TrainingFamily]) -> dict:
    vectors = [v for family in families if family.split == "train" for v in family.vectors]
    rows = []
    for index, name in enumerate(vectors[0].names):
        observed = [v.values[index] for v in vectors if v.available[index]]
        rows.append({"feature": name, "observed_profiles": len(observed),
                     "distinct_observed_values": len(set(observed))})
    zero_pairs = []
    for family in families:
        for a, b, label in family.pairs:
            if label in ("A", "B") and family.vectors[a] == family.vectors[b]:
                zero_pairs.append({"family": family.family_id, "a": a, "b": b, "label": label})
    return {"train_profiles": len(vectors), "features": len(rows),
            "observed_varying_features": sum(r["distinct_observed_values"] > 1 for r in rows),
            "never_observed_features": sum(r["observed_profiles"] == 0 for r in rows),
            "observed_constant_features": sum(r["observed_profiles"] > 0 and r["distinct_observed_values"] <= 1 for r in rows),
            "rows": rows, "decisive_identical_feature_pairs": zero_pairs}


def _metrics(families: list[TrainingFamily], scores: dict[str, list[float]], split: str) -> dict:
    report = evaluate(families, scores)
    report["evaluation_families"] = report.pop("golden_families")
    report.pop("passed")
    report["split"] = split
    report["interpretation"] = "C-selection set, optimistic" if split == "validation" else "teacher agreement only"
    report["confidence_interval_supported"] = report["evaluation_families"] >= 2
    if not report["confidence_interval_supported"]:
        report["ndcg_delta_ci_low"] = report["pair_delta_ci_low"] = None
    return report


def _runtime_metrics(families: list[TrainingFamily], scores: dict[str, list[float]], split: str) -> dict:
    ordered, baseline_families = {}, []
    for family in families:
        size = len(family.vectors)
        ids = [str(uuid5(NAMESPACE_URL, f"{family.family_id}/{i}")) for i in range(size)]
        main = sorted(range(size), key=lambda i: (-family.main_scores[i], ids[i]))
        reranked = sorted(range(size), key=lambda i: (-scores[family.family_id][i], -family.main_scores[i], ids[i]))
        baseline, rank_scores = [0.] * size, [0.] * size
        for position, index in enumerate(main):
            baseline[index] = size - position
        for position, index in enumerate(reranked):
            rank_scores[index] = size - position
        baseline_families.append(replace(family, main_scores=baseline))
        ordered[family.family_id] = rank_scores
    report = _metrics(baseline_families, ordered, split)
    report["ordering"] = "Runtime order: R0 score, main score, candidate UUID; main ties by UUID"
    return report


def run_sample_probe(directory: Path) -> dict:
    """Rebuild current features from cached facts and fit once; preserve sources, budgets and golden holdout.

    The model is explicitly test-only, uses legacy uniform teacher labels, and
    never enters the backend artifact path. Golden families have no model scores.
    Finished contexts replay immutable reports; changed inputs/code cannot retune
    a completed experiment under the same identity.
    """
    records = [_read(path) for path in sorted((directory / "families").glob("*.json"))]
    if not records:
        raise ValueError("sample has no sealed families")
    _validate_sample_shape(records)
    expected = {r["family_id"]: r for r in family_manifest()}
    for record in records:
        if record["manifest"] != expected.get(record["manifest"]["family_id"]):
            raise ValueError("sample differs from frozen source assignment")
        verify_cached_sources(directory, record)
    if len({digest(r["teacher"]) for r in records}) != 1:
        raise ValueError("sample mixes teacher identities")
    audit = audit_records(records)
    source = Path(__file__).parent
    code_names = ("rerank_sample_probe.py", "rerank_sample_features.py", "rerank_training.py",
                  "rerank_features.py", "rerank_model.py", "combine_scoring.py", "combine_models.py",
                  "strict.py", "strict_evidence.py", "vacancy_features.py", "models.py", "embeddings.py")
    context = {"version": VERSION, "source_record_hashes": {r["manifest"]["family_id"]: r["record_hash"] for r in records},
               "code_hashes": {name: digest((source / name).read_bytes().hex()) for name in code_names},
               "seed_hash": digest(SEED_PATH.read_bytes().hex()),
               "semantic_cache_hash": digest((directory / "semantic-cache.json").read_bytes().hex()),
               "feature_schema_hash": feature_schema(initial_schema()).schema_hash,
               "teacher": records[0]["teacher"], "golden_policy": "No fitting, prediction, evaluation or C selection",
               "regularization_grid": [.01, .1, 1, 10], "tuning_cycles": 2,
               "revision": "Guard-only reproducibility run; fixed data, features, grid and selection rule",
               "policy": "Invalid source/hash/split/dedup aborts; cycles and missing signals reported without label repair",
               "activation_allowed": False}
    context["context_hash"] = digest(context)
    target = directory / "diagnostic-sample-probe-v2"
    _seal(target / "manifest.json", context)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            report = _read(target / "report.json")
            if (report["context_hash"] != context["context_hash"] or report["report_hash"] != digest({k: v for k, v in report.items() if k != "report_hash"})
                    or report["artifact_hash"] != _read(target / "artifact.json")["content_hash"]):
                raise ValueError("sample probe report integrity failed")
            RerankArtifact.model_validate(_read(target / "artifact.json"))
            return report
        if (target / "started.json").exists():
            raise ValueError("interrupted sample probe requires receipt review")
        ledger_path = directory / "provider-cache/usage.json"
        ledger_before = ledger_path.read_bytes()
        source_before = {path: path.read_bytes() for path in (directory / "families").glob("*.json")}
        cache_before = {path: digest(path.read_bytes().hex()) for path in (directory / "provider-cache").glob("*.json")}
        families, derivations = [], {}
        for record in records:
            if record["manifest"]["split"] == "golden":
                continue
            vectors, main_scores, provenance = rebuild_family_features(directory, record)
            m = record["manifest"]
            family = TrainingFamily(m["family_id"], m["direction"], m["split"], vectors, main_scores,
                                    record["grades"], [tuple(p) for p in record["pairs"]], m["source_id"], tuple(record["slice_tags"]))
            families.append(family)
            derivations[family.family_id] = provenance
        schema = feature_schema(initial_schema())
        validate_families(families, schema)
        coverage = _feature_coverage(families)
        derived_hash = digest([asdict(f) for f in families])
        _seal(target / "derived-families.json", {"context_hash": context["context_hash"], "dataset_hash": derived_hash,
                                              "families": [asdict(f) for f in families], "provenance": derivations})
        _seal(target / "started.json", {"context_hash": context["context_hash"], "dataset_hash": derived_hash, "tuning_cycles": 2})
        artifact = train(families, schema, {"require_protocol": False, "dataset_hash": derived_hash,
                         "model_version": "r0-current-sample-probe-v2", "tuning_cycles": 2})
        if not artifact["training"]["test_only"] or artifact["gate"]["passed"] or promotion_eligible(artifact):
            raise ValueError("sample artifact isolation guard failed")
        model = RerankArtifact.model_validate(artifact)
        scores = {f.family_id: [sum(w*x for w, x in zip(model.coefficients, scaled_features(v, model), strict=True)) for v in f.vectors] for f in families}
        metrics = {split: _metrics([f for f in families if f.split == split], scores, split)
                   for split in ("train", "validation", "calibration")}
        runtime_metrics = {split: _runtime_metrics([f for f in families if f.split == split], scores, split)
                           for split in ("train", "validation", "calibration")}
        scorecards = []
        for family in families:
            identifiers = [str(uuid5(NAMESPACE_URL, f"{family.family_id}/{i}")) for i in range(len(family.vectors))]
            main_order = sorted(range(len(identifiers)), key=lambda i: (-family.main_scores[i], identifiers[i]))
            rerank_order = sorted(range(len(identifiers)), key=lambda i: (-scores[family.family_id][i], -family.main_scores[i], identifiers[i]))
            scorecards.append({"family": family.family_id, "split": family.split,
                               "main_order": main_order, "rerank_order": rerank_order,
                               "main_scores": family.main_scores, "rerank_scores": scores[family.family_id], "grades": family.grades})
        if (ledger_path.read_bytes() != ledger_before or any(path.read_bytes() != value for path, value in source_before.items())
                or set((directory / "provider-cache").glob("*.json")) != set(cache_before)
                or any(not path.exists() or digest(path.read_bytes().hex()) != value for path, value in cache_before.items())):
            raise ValueError("sample source bytes or usage changed during the probe")
        report = {"context_hash": context["context_hash"], "dataset_hash": derived_hash,
                  "artifact_hash": artifact["content_hash"], "state": "diagnostic_complete",
                  "audit": audit, "feature_coverage": coverage, "derivations": derivations,
                  "teacher": records[0]["teacher"], "grounded_diagnostics_used_for_training": False,
                  "metrics": metrics, "runtime_order_metrics": runtime_metrics, "scorecards": scorecards,
                  "regularization_candidates": artifact["training"]["regularization_candidates"],
                  "selected_C": artifact["training"]["C"], "training": artifact["training"],
                  "golden_predictions": 0, "withheld_golden_profiles": sum(len(r["traits"]) for r in records if r["manifest"]["split"] == "golden"),
                  "http_calls": 0, "key_reads": 0, "source_bytes_preserved": True, "ledger_unchanged": True,
                  "provider_cache_bytes_preserved": True,
                  "ledger": json.loads(ledger_before), "production_suitable": False, "activation_allowed": False,
                  "tail_gap": None, "quality_claim": "Partial-sample implementation and legacy-teacher agreement only",
                  "metric_ties": "Trainer metrics use source-index ties; displayed runtime order uses main score then candidate ID"}
        report["report_hash"] = digest(report)
        _seal(target / "artifact.json", artifact)
        _seal(target / "report.json", report)
        return report


def main() -> None:
    """Run or replay the cached partial-sample diagnostic without external calls or model activation."""
    parser = argparse.ArgumentParser(description="Audit cached sample and fit an isolated test-only R0")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    report = run_sample_probe(parser.parse_args().directory)
    print(json.dumps({"state": report["state"], "selected_C": report["selected_C"],
                      "metrics": report["metrics"], "activation_allowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
