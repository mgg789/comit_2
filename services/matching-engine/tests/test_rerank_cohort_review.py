"""Offline review packet isolation, immutable provenance and strict manual answers."""

import itertools
import json
from copy import deepcopy

import pytest

from matching_engine import rerank_cohort_review as review
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import DIRECTIONS, expected_families


def _write(path, value, hash_field=None):
    value = deepcopy(value)
    if hash_field:
        value.pop(hash_field, None)
        value[hash_field] = digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


@pytest.fixture
def cohort(tmp_path):
    target = tmp_path / review.DEFAULT_COHORT
    rows, sources = [], {}
    for family_index, item in enumerate(expected_families()):
        row = {**item, "cohort_id": review.DEFAULT_COHORT, "keys": ["python"],
               "vacancy": {"class": item["direction"], "domens": {}, "stacks": {"python": 4},
                           "soft_skills": {}, "bio": "Разработка", "salary": {"min": 50000, "max": 200000}},
               "policy": {"id": "declared-python", "ordered_priorities": ["Сначала заявленный Python."]}}
        traits = [{"grade": "middle", "status": "partial", "levels": {"python": index},
                   "skill_statuses": {"python": "confirmed" if index % 2 else "pending"},
                   "salary_min": 50000 + (family_index * 7 + index) * 100,
                   "description": f"Проект {item['family_id']}, задача {index}.",
                   "structured_facts_available": False, "duration_months": None, "duration_status": None,
                   "production_support": None, "production_status": None, "role": None, "role_status": None,
                   "project_domains": [], "project_stacks": []} for index in range(7)]
        source = _write(target / "sources" / f"{item['family_id']}.json",
                        {"manifest": row, "traits": traits, "source_receipts": "synthetic-test"}, "record_hash")
        rows.append(row)
        sources[item["family_id"]] = source
    manifest = _write(target / "manifest.json", {"version": review.DEFAULT_COHORT, "families": rows}, "manifest_hash")
    return tmp_path, target, manifest, sources


def _dataset(cohort, label="A"):
    _, target, manifest, sources = cohort
    families = [_write(target / "families" / f"{identifier}.json", {
        "manifest": source["manifest"], "traits": source["traits"], "source_hash": source["record_hash"],
        "vacancy": source["manifest"]["vacancy"], "pairs": [[a, b, label] for a, b in itertools.combinations(range(7), 2)],
        "main_scores": [999] * 7, "model_predictions": [777] * 7,
    }, "record_hash") for identifier, source in sources.items()]
    return _write(target / "dataset.json", {"version": review.DEFAULT_COHORT,
                  "manifest_hash": manifest["manifest_hash"], "families": families}, "dataset_hash")


def _manual(packet, label="A"):
    return {"packet_hash": packet["packet_hash"], "reviewer_kind": packet["reviewer_kind"],
            "labels": [{"pair_id": case["pair_id"], "label": label, "reason": "Проверены исходные факты."}
                       for case in packet["cases"]]}


def test_prepare_is_score_blind_disjoint_complete_deterministic_and_immutable(cohort, monkeypatch):
    directory, target, _, sources = cohort
    (target / "dataset.json").write_text("DO NOT READ TEACHER LABELS", encoding="utf-8")
    (target / "features").mkdir()
    (target / "features" / "sentinel.json").write_text("DO NOT READ SCORES", encoding="utf-8")
    original_read = review._read

    def guarded_read(path):
        assert path.name != "dataset.json" and path.parent.name != "features"
        return original_read(path)

    monkeypatch.setattr(review, "_read", guarded_read)
    packets = review.prepare_reviews(directory)
    assert packets == review.prepare_reviews(directory)
    ids = []
    indices = []
    for reviewer, packet in packets.items():
        assert len(packet["cases"]) == 20 and len(packet["source_hashes"]) == 60
        assert packet["independent_human_evaluation"] is False
        assert {case["direction"] for case in packet["cases"]} == set(DIRECTIONS)
        assert {case["split"] for case in packet["cases"]} == {"train", "validation", "calibration", "golden"}
        assert len({case["family_id"] for case in packet["cases"]}) == 20
        for case in packet["cases"]:
            source = sources[case["family_id"]]
            assert case["A"] == source["traits"][case["a_index"]]
            assert case["B"] == source["traits"][case["b_index"]]
            assert set(case) == {"pair_id", "family_id", "a_index", "b_index", "direction", "split", "vacancy", "policy", "A", "B"}
        ids.append({case["pair_id"] for case in packet["cases"]})
        indices.append({(case["family_id"], case["a_index"], case["b_index"]) for case in packet["cases"]})
        text = json.dumps(packet)
        assert all(forbidden not in text for forbidden in ("main_scores", "features", "teacher_label", "model_predictions"))
        markdown = (target / "reviews" / f"{reviewer}-packet.md").read_text(encoding="utf-8")
        assert markdown.count("## Пара ") == 20 and "Навыки (уровень/статус)" in markdown
        assert "duration_status" in markdown and "не является независимой человеческой" in markdown
    assert not ids[0] & ids[1] and not indices[0] & indices[1]


def test_selection_does_not_depend_on_teacher_labels_or_model_scores(cohort):
    directory, _, _, _ = cohort
    first = review.prepare_reviews(directory)
    _dataset(cohort, "A")
    assert review.prepare_reviews(directory) == first
    _dataset(cohort, "B")
    assert review.prepare_reviews(directory) == first


def test_prepare_requires_all_sixty_sources_and_unchanged_hashes(cohort):
    directory, target, _, sources = cohort
    identifier = next(iter(sources))
    source_path = target / "sources" / f"{identifier}.json"
    original = source_path.read_bytes()
    source_path.unlink()
    with pytest.raises(ValueError, match="all 60 accepted sources"):
        review.prepare_reviews(directory)
    source_path.write_bytes(original)
    source = json.loads(original)
    source["traits"][0]["salary_min"] += 100
    _write(source_path, source)
    with pytest.raises(ValueError, match="hash mismatch"):
        review.prepare_reviews(directory)


def test_existing_packets_and_markdown_cannot_be_overwritten(cohort):
    directory, target, _, _ = cohort
    packets = review.prepare_reviews(directory)
    path = target / "reviews/assistant-packet.json"
    changed = deepcopy(packets["assistant"])
    changed["cases"][0]["A"]["salary_min"] += 1
    _write(path, changed, "packet_hash")
    with pytest.raises(ValueError, match="immutable"):
        review.prepare_reviews(directory)
    _write(path, packets["assistant"])
    (target / "reviews/assistant-packet.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Markdown changed"):
        review.prepare_reviews(directory)


def test_compare_reports_categories_factual_blocks_without_relabeling(cohort):
    directory, target, _, _ = cohort
    packets = review.prepare_reviews(directory)
    _dataset(cohort)
    protected = (target / "dataset.json").read_bytes()
    manual = _manual(packets["assistant"])
    for index, label in enumerate(("B", "tie", "insufficient")):
        manual["labels"][index]["label"] = label
    manual["labels"][3]["factual_issue"] = True
    path = target / "assistant-manual.json"
    _write(path, manual)
    report = review.compare_review(directory, path)
    assert report["category_counts"] == {"agreement": 17, "preference_reversal": 1,
                                         "decisive_vs_tie": 1, "decisive_vs_insufficient": 1}
    assert report["disagreements"] == 3 and report["unresolved_factual_issues"] == 1
    assert report["factual_issues_block_activation"] is True
    assert report["activation_allowed"] is False and report["labels_changed"] is False
    assert report["claimed_human_review"] is False and report["independent_human_evaluation"] is False
    assert report == review.compare_review(directory, path)
    assert (target / "dataset.json").read_bytes() == protected


def test_user_authorship_is_only_claimed_not_verified(cohort):
    directory, target, _, _ = cohort
    packet = review.prepare_reviews(directory)["user"]
    _dataset(cohort, "tie")
    manual = _manual(packet, "insufficient")
    manual["claimed_human_review"] = True
    path = target / "user-manual.json"
    _write(path, manual)
    report = review.compare_review(directory, path)
    assert report["category_counts"] == {"tie_vs_insufficient": 20}
    assert report["claimed_human_review"] is True
    assert report["human_authorship_verified"] is report["independent_human_evaluation"] is False
    assert report["activation_allowed"] is False


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(packet_hash="0" * 64),
    lambda value: value.update(reviewer_kind="user"),
    lambda value: value.update(claimed_human_review=True),
    lambda value: value.update(labels=value["labels"][:-1]),
    lambda value: value["labels"].append(value["labels"][0]),
    lambda value: value["labels"][0].update(pair_id="unknown"),
    lambda value: value["labels"][0].update(pair_id=value["labels"][1]["pair_id"]),
    lambda value: value["labels"][0].update(label="C"),
    lambda value: value["labels"][0].update(reason=" "),
    lambda value: value["labels"][0].update(factual_issue="false"),
    lambda value: value["labels"][0].update(confidence=.9),
    lambda value: value.update(model_predictions=[]),
])
def test_review_requires_exact_twenty_ids_fields_and_valid_labels(cohort, mutation):
    directory, target, _, _ = cohort
    packet = review.prepare_reviews(directory)["assistant"]
    _dataset(cohort)
    manual = _manual(packet)
    mutation(manual)
    path = target / "manual.json"
    _write(path, manual)
    with pytest.raises(ValueError):
        review.compare_review(directory, path)


def test_compare_rejects_rehashed_packet_or_changed_dataset_source(cohort):
    directory, target, _, _ = cohort
    packet = review.prepare_reviews(directory)["assistant"]
    dataset = _dataset(cohort)
    path = target / "manual.json"
    _write(path, _manual(packet))
    packet_path = target / "reviews/assistant-packet.json"
    altered = deepcopy(packet)
    altered["cases"][0]["a_index"] = (altered["cases"][0]["a_index"] + 1) % 7
    _write(packet_path, altered, "packet_hash")
    with pytest.raises(ValueError, match="deterministic"):
        review.compare_review(directory, path)
    _write(packet_path, packet)
    dataset["families"][0]["traits"][0]["salary_min"] += 1
    _write(target / "dataset.json", dataset, "dataset_hash")
    with pytest.raises(ValueError, match="family hash mismatch"):
        review.compare_review(directory, path)
    family = dataset["families"][0]
    family.pop("record_hash")
    family["record_hash"] = digest(family)
    _write(target / "dataset.json", dataset, "dataset_hash")
    with pytest.raises(ValueError, match="raw facts"):
        review.compare_review(directory, path)


def test_compare_requires_completed_sealed_dataset(cohort):
    directory, target, _, _ = cohort
    packet = review.prepare_reviews(directory)["assistant"]
    path = target / "manual.json"
    _write(path, _manual(packet))
    with pytest.raises(FileNotFoundError):
        review.compare_review(directory, path)
    dataset = _dataset(cohort)
    dataset["families"] = dataset["families"][:-1]
    _write(target / "dataset.json", dataset, "dataset_hash")
    with pytest.raises(ValueError, match="completed dataset"):
        review.compare_review(directory, path)


def test_review_and_dataset_reject_duplicate_json_and_invalid_outer_hash(cohort):
    directory, target, _, _ = cohort
    packet = review.prepare_reviews(directory)["assistant"]
    dataset = _dataset(cohort)
    path = target / "manual.json"
    path.write_text('{"reviewer_kind":"assistant","reviewer_kind":"user"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON"):
        review.compare_review(directory, path)
    _write(path, _manual(packet))
    dataset["dataset_hash"] = "0" * 64
    _write(target / "dataset.json", dataset)
    with pytest.raises(ValueError, match="hash mismatch"):
        review.compare_review(directory, path)


def test_cli_prepare_and_compare_use_only_local_artifacts(cohort, monkeypatch, capsys):
    directory, target, _, _ = cohort
    monkeypatch.setattr("sys.argv", ["review", "prepare", "--directory", str(directory)])
    review.main()
    assert json.loads(capsys.readouterr().out)["state"] == "packets_prepared"
    packet = json.loads((target / "reviews/assistant-packet.json").read_text())
    path = target / "manual.json"
    _write(path, _manual(packet))
    _dataset(cohort)
    monkeypatch.setattr("sys.argv", ["review", "compare", "--directory", str(directory), "--review-file", str(path)])
    review.main()
    output = json.loads(capsys.readouterr().out)
    assert output["reviewed_pairs"] == 20 and output["activation_allowed"] is False
