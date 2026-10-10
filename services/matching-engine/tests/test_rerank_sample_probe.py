"""Partial sample probes retain split boundaries, provenance and non-activation guards."""

import itertools
import json
from copy import deepcopy

import pytest

import matching_engine.rerank_sample_probe as probe
from matching_engine.rerank_features import FeatureVector, feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import promotion_eligible
from matching_engine.rerank_providers import teacher_identity
from matching_engine.strict import initial_schema


@pytest.fixture
def sample(tmp_path, monkeypatch):
    (tmp_path / "families").mkdir()
    (tmp_path / "provider-cache").mkdir()
    (tmp_path / "provider-cache/usage.json").write_text('{"generation":28,"pair":736,"grade":119}')
    (tmp_path / "semantic-cache.json").write_text('{}')
    manifest = {r["family_id"]: r for r in probe.family_manifest()}
    schema = feature_schema(initial_schema())
    rebuilt = []
    for offset, (family_id, size) in enumerate(probe.EXPECTED_PROFILES.items()):
        record = {"manifest": manifest[family_id], "teacher": teacher_identity(),
                  "traits": [{"grade": "junior", "status": "confirmed", "levels": {"python": index},
                              "description": f"{family_id}/{index}", "salary_min": 50000+offset*1000+index}
                             for index in range(size)],
                  "pairs": [[a, b, "A"] for a, b in itertools.combinations(range(size), 2)],
                  "grades": [2]*size, "swap_audits": [True]*3, "slice_tags": []}
        record["record_hash"] = digest(record)
        (tmp_path / "families" / f"{family_id}.json").write_text(json.dumps(record))

    def verify(_directory, record):
        assert record["record_hash"] == digest({k:v for k,v in record.items() if k != "record_hash"})

    def rebuild(_directory, record):
        family_id = record["manifest"]["family_id"]
        assert record["manifest"]["split"] != "golden", "golden reconstructed for prediction"
        rebuilt.append(family_id)
        size = len(record["traits"])
        main = [1 - index/size for index in range(size)]
        values = [(score, *([0.] * (len(schema.names)-1))) for score in main]
        vectors = [FeatureVector(schema.names, row, (True,)*len(schema.names), schema.schema_hash) for row in values]
        return vectors, main, {"source_record_hash": record["record_hash"], "current_feature_schema_hash": schema.schema_hash}

    monkeypatch.setattr(probe, "verify_cached_sources", verify)
    monkeypatch.setattr(probe, "rebuild_family_features", rebuild)
    return tmp_path, rebuilt


def test_probe_fits_only_train_and_leaves_golden_unpredicted(sample, monkeypatch):
    directory, rebuilt = sample
    report = probe.run_sample_probe(directory)
    assert report["golden_predictions"] == 0 and report["withheld_golden_profiles"] == 20
    assert set(rebuilt) == set(probe.EXPECTED_PROFILES) - {"backend-07", "backend-08"}
    assert report["training"]["independent_training_pairs"] == 351 and report["training"]["training_rows"] == 702
    assert report["training"]["test_only"] and report["training"]["intercept"] is False
    assert [r["C"] for r in report["regularization_candidates"]] == [.01, .1, 1., 10.]
    assert report["selected_C"] == .01  # All candidates tie; fixed validation tie rule chooses lowest C.
    assert report["metrics"]["calibration"]["evaluation_families"] == 1
    assert report["metrics"]["calibration"]["ndcg_delta_ci_low"] is None
    assert not report["activation_allowed"] and not report["production_suitable"]
    model = json.loads((directory / "diagnostic-sample-probe-v2/artifact.json").read_text())
    assert not promotion_eligible(model) and not model["gate"]["passed"] and model["tail_gap"] is None
    before = (directory / "provider-cache/usage.json").read_bytes()
    monkeypatch.setattr(probe, "train", lambda *_: pytest.fail("finished probe refitted"))
    assert probe.run_sample_probe(directory) == report
    assert (directory / "provider-cache/usage.json").read_bytes() == before


def test_cached_probe_rejects_changed_semantic_context_without_refit(sample, monkeypatch):
    directory, _ = sample
    probe.run_sample_probe(directory)
    (directory / "semantic-cache.json").write_text('{"changed":true}')
    monkeypatch.setattr(probe, "train", lambda *_: pytest.fail("changed context refitted"))
    with pytest.raises(ValueError):
        probe.run_sample_probe(directory)


def test_cached_probe_rejects_artifact_tampering(sample):
    directory, _ = sample
    probe.run_sample_probe(directory)
    path = directory / "diagnostic-sample-probe-v2/artifact.json"
    model = json.loads(path.read_text())
    model["coefficients"][0] += 1
    path.write_text(json.dumps(model))
    with pytest.raises(ValueError, match="integrity"):
        probe.run_sample_probe(directory)


def test_cycles_are_preserved_and_cross_split_duplicates_rejected(sample):
    directory, _ = sample
    records = [json.loads(path.read_text()) for path in sorted((directory / "families").glob('*.json'))]
    record = deepcopy(records[0])
    record["traits"].append({**record["traits"][0], "description": "third"})
    record["grades"].append(1)
    record["pairs"] = [[0, 1, "A"], [1, 2, "A"], [0, 2, "B"]]
    audit = probe.audit_records([record])
    assert audit["family_rows"][0]["cyclic_components"] == [[0, 1, 2]]
    assert not audit["label_repair_or_dropping"] and not audit["production_suitable"]
    assert audit["family_rows"][0]["scalar_order_ceiling"]["maximum_satisfiable_pairs"] == 2
    assert audit["minimum_unavoidable_pair_disagreements"] == 1
    validation = next(r for r in records if r["manifest"]["split"] == "validation")
    validation["traits"][0] = deepcopy(records[0]["traits"][0])
    with pytest.raises(ValueError, match="cross splits"):
        probe.audit_records(records)


@pytest.mark.parametrize("split", ["validation", "calibration", "golden"])
def test_structured_fact_duplicates_with_new_description_cross_splits_rejected(sample, split):
    directory, _ = sample
    records = [json.loads(path.read_text()) for path in sorted((directory / "families").glob('*.json'))]
    source = next(r for r in records if r["manifest"]["split"] == "train")
    target = next(r for r in records if r["manifest"]["split"] == split)
    target["traits"][0] = {**deepcopy(source["traits"][0]), "description": "different free text"}
    with pytest.raises(ValueError, match="structured facts cross splits"):
        probe.audit_records(records)


def test_started_probe_never_retrains_implicitly(sample, monkeypatch):
    directory, _ = sample
    def interrupted(*_):
        raise RuntimeError("simulated training interruption")
    monkeypatch.setattr(probe, "train", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        probe.run_sample_probe(directory)
    with pytest.raises(ValueError, match="interrupted sample"):
        probe.run_sample_probe(directory)


@pytest.mark.parametrize("change", ["missing", "duplicate_golden", "wrong_count", "wrong_split", "extra"])
def test_frozen_sample_membership_and_counts_are_enforced(sample, monkeypatch, change):
    directory, _ = sample
    path = directory / "families/backend-07.json"
    record = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    elif change == "duplicate_golden":
        (directory / "families/duplicate.json").write_text(json.dumps(record))
    elif change == "extra":
        record["manifest"]["family_id"] = "mobile-01"
        (directory / "families/extra.json").write_text(json.dumps(record))
    else:
        if change == "wrong_count":
            record["traits"].pop()
        else:
            record["manifest"]["split"] = "train"
        path.write_text(json.dumps(record))
    monkeypatch.setattr(probe, "train", lambda *_: pytest.fail("invalid sample fitted"))
    with pytest.raises(ValueError):
        probe.run_sample_probe(directory)


@pytest.mark.parametrize("kind", ["artifact", "ledger", "source", "new_cache_receipt"])
def test_isolation_guards_reject_mutation_explicitly(sample, monkeypatch, kind):
    directory, _ = sample
    original = probe.train

    def mutated(*args):
        result = original(*args)
        if kind == "artifact":
            result["training"]["test_only"] = False
        elif kind == "ledger":
            (directory / "provider-cache/usage.json").write_text('{"pair":737}')
        elif kind == "new_cache_receipt":
            (directory / "provider-cache/unexpected.failure-1.json").write_text('{}')
        else:
            (directory / "families/backend-01.json").write_text('{}')
        return result

    monkeypatch.setattr(probe, "train", mutated)
    with pytest.raises(ValueError, match="guard|source bytes|usage"):
        probe.run_sample_probe(directory)
