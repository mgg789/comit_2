"""Explicit train derivations preserve source facts and pass actual runtime drift guards."""

import json
import math
from copy import deepcopy

import pytest
from test_rerank_cohort_features import _trait

import matching_engine.rerank_description_repair as module
from matching_engine.embeddings import HarrierEncoder
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import expected_families


def _seal(record):
    record["record_hash"] = digest({key: value for key, value in record.items() if key != "record_hash"})


@pytest.fixture
def sources(tmp_path, monkeypatch):
    old = {}
    for family_index, allocation in enumerate(expected_families()):
        item = {**allocation, "cohort_id": "old", "vacancy": {
            "class": allocation["direction"], "domens": {"sql": 4}, "stacks": {"python": 5},
            "soft_skills": {}, "bio": "Разработка SQL и Python", "salary": {"min": 50000, "max": 200000}},
            "policy": {"id": "test", "ordered_priorities": ["Prefer stated technical fit"]}}
        traits = [_trait(index) for index in range(7)]
        for index, trait in enumerate(traits):
            trait["salary_min"] = 50000 + (family_index * 7 + index) * 200
            trait["description"] = f"Синтетический профиль {allocation['family_id']} вариант {index}"
        record = {"manifest": item, "traits": traits, "source_receipts": "original-generation-seal"}
        old[allocation["family_id"]] = record
    for family, repairs in module.DESCRIPTION_REPAIRS.items():
        for repair in repairs:
            old[family]["traits"][repair["index"]]["description"] = repair["old_text"]
    for family, index, other, other_index in module.ORIGINAL_COLLISIONS:
        old[other]["traits"][other_index]["description"] = old[family]["traits"][index]["description"]
    for record in old.values():
        _seal(record)
    new = deepcopy(old)
    for family, record in new.items():
        record["manifest"]["cohort_id"] = "derived"
        record["traits"] = module.apply_description_repairs(
            record["traits"], module.DESCRIPTION_REPAIRS.get(family, []), record["manifest"]["split"])
        record["source_receipts"] = {"original_record_hash": old[family]["record_hash"]}
        _seal(record)
    calls = []

    def encode(self, text):
        calls.append((self.role, text))
        # Only local model inference is replaced; embeddings remain valid and
        # text-sensitive while all class ranking/filter/scoring/features are real.
        small = int(digest(text)[:5], 16) % 10000 / 100000
        norm = math.sqrt(1 + small * small)
        return [1 / norm, small / norm] + [0.] * 638

    monkeypatch.setattr(HarrierEncoder, "encode", encode)
    (tmp_path / "semantic-cache.json").write_text("{}")
    (tmp_path / "derived").mkdir()
    (tmp_path / "derived" / "semantic-cache.json").write_text("{}")
    return tmp_path, old, new, calls


def test_explicit_train_variants_preserve_facts_are_idempotent_and_do_not_mutate_input():
    traits = [_trait(index) for index in range(7)]
    repair = module.DESCRIPTION_REPAIRS["data-03"][0]
    traits[0]["description"] = repair["old_text"]
    original = deepcopy(traits)
    repaired = module.apply_description_repairs(traits, [repair], "train")
    assert traits == original and repaired[0]["description"] == repair["new_text"]
    assert {key: value for key, value in repaired[0].items() if key != "description"} == {
        key: value for key, value in original[0].items() if key != "description"}
    assert module.apply_description_repairs(repaired, [repair], "train") == repaired
    repaired[0]["levels"]["python"] = 1
    assert traits[0]["levels"]["python"] == 5


@pytest.mark.parametrize("split", ["validation", "calibration", "golden"])
def test_holdouts_reject_any_nonempty_manual_derivation(split):
    with pytest.raises(ValueError, match="train-only"):
        module.apply_description_repairs([_trait()], module.DESCRIPTION_REPAIRS["data-03"][:1], split)
    trait = _trait()
    assert module.apply_description_repairs([trait], [], split) == [trait]


@pytest.mark.parametrize("alteration", ["quantity", "claim", "unsupported_text", "wrong_source"])
def test_unapproved_facts_or_text_are_not_accepted(alteration):
    repair = deepcopy(module.DESCRIPTION_REPAIRS["data-03"][0])
    trait = _trait()
    trait["description"] = repair["old_text"]
    if alteration == "quantity":
        repair["new_text"] += " Опыт 24 месяца."
    elif alteration == "claim":
        repair["claim_set"].append("production support")
    elif alteration == "unsupported_text":
        repair["new_text"] = "Руководил командой и поддерживал production."
    else:
        trait["description"] = "Другой неподтверждённый источник"
    with pytest.raises(ValueError):
        module.apply_description_repairs([trait], [repair], "train")


def test_prelabel_audit_rebuilds_real_global_features_and_seals_replay(sources, monkeypatch):
    directory, old, new, calls = sources
    snapshots = deepcopy((old, new))
    captured = []
    original = module.build_family_features

    def built(*args, **kwargs):
        captured.append((args[0]["family_id"], args[4], len(kwargs["class_profiles"]), kwargs["class_source_lineage"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "build_family_features", built)
    report = module.audit_description_repairs(directory, "derived", new, old)
    assert report["status"] == "passed" and report["source_profiles"] == 420
    assert report["derived_variants"] == 3 and report["reviewer_kind"] == "assistant"
    assert not report["independence_established"] and not report["independent_human_review"]
    assert report["typed_facts_byte_equal"] and report["pre_label_audit"]
    assert len(report["original_collision_pairs"]) == 3
    assert all(not pair["correlation_eliminated"] for pair in report["original_collision_pairs"])
    assert report["near_templates"]["old"]["cross_split_exact_count"] == 3
    assert report["near_templates"]["new"]["cross_split_exact_count"] == 0
    assert report["near_templates"]["new"]["cross_split_near_count"] > 0
    assert len(report["near_templates"]["new"]["top20"]) == 20
    assert [(family, namespace, size) for family, namespace, size, _ in captured] == [
        ("data-03", "derived", 56), ("data-03", "derived", 56),
        ("fullstack-03", "derived", 49), ("fullstack-03", "derived", 49)]
    assert all(len(lineage) == size for _, _, size, lineage in captured)
    assert all(row["availability_unchanged"] and row["nonsemantic_features_unchanged"] for row in report["repairs"])
    assert all(row["document_cosine_distance"] <= .20 and row["semantic_cosine_delta"] <= .25 for row in report["repairs"])
    assert any(row["main_score_delta"] > 0 for row in report["repairs"])
    assert all(row["old_document_key"] != row["new_document_key"] for row in report["repairs"])
    assert (old, new) == snapshots and (directory / "semantic-cache.json").read_text() == "{}"
    path = directory / "derived" / "description-repair-report.json"
    before = path.read_bytes()
    calls.clear()
    assert module.audit_description_repairs(directory, "derived", list(new.values()), list(old.values())) == report
    assert calls == [] and path.read_bytes() == before
    damaged = json.loads(path.read_text())
    damaged["status"] = "forged"
    path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match="report identity changed"):
        module.audit_description_repairs(directory, "derived", new, old)


@pytest.mark.parametrize("mutation", ["source_hash", "typed_fact", "holdout_text", "missing_family"])
def test_source_errors_fail_before_any_local_inference(sources, mutation):
    directory, old, new, calls = sources
    broken = deepcopy(new)
    if mutation == "source_hash":
        broken["data-03"]["record_hash"] = "0" * 64
    elif mutation == "typed_fact":
        broken["data-03"]["traits"][0]["duration_months"] += 1
        _seal(broken["data-03"])
    elif mutation == "holdout_text":
        broken["data-07"]["traits"][0]["description"] = "Неавторизованное изменение golden-профиля"
        _seal(broken["data-07"])
    else:
        del broken["data-03"]
    with pytest.raises(ValueError):
        module.audit_description_repairs(directory, "derived", broken, old)
    assert calls == [] and not (directory / "derived" / "description-repair-report.json").exists()


def test_actual_document_cosine_drift_bound_rejects_before_publishing(sources, monkeypatch):
    directory, old, new, _ = sources
    new_texts = [repair["new_text"] for repairs in module.DESCRIPTION_REPAIRS.values() for repair in repairs]

    def encode(_self, text):
        changed = any(value in text for value in new_texts)
        return ([0., 1.] if changed else [1., 0.]) + [0.] * 638

    monkeypatch.setattr(HarrierEncoder, "encode", encode)
    with pytest.raises(ValueError, match="semantic drift bounds"):
        module.audit_description_repairs(directory, "derived", new, old)
    assert not (directory / "derived" / "description-repair-report.json").exists()


def test_semantic_cosine_guard_rejects_even_when_document_distance_passes(sources, monkeypatch):
    directory, old, new, _ = sources
    new_texts = [repair["new_text"] for repairs in module.DESCRIPTION_REPAIRS.values() for repair in repairs]

    def encode(self, text):
        if self.role == "vacancy_query":
            return [0., 1.] + [0.] * 638
        changed = any(value in text for value in new_texts)
        # Distance is .1, inside the .2 document guard. Against the orthogonal
        # vacancy query, cosine still moves sqrt(.19), above the .25 guard.
        return ([.9, math.sqrt(.19)] if changed else [1., 0.]) + [0.] * 638

    monkeypatch.setattr(HarrierEncoder, "encode", encode)
    with pytest.raises(ValueError, match="semantic drift bounds"):
        module.audit_description_repairs(directory, "derived", new, old)
    assert not (directory / "derived" / "description-repair-report.json").exists()
