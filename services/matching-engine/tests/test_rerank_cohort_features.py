"""Cohort snapshots retain typed evidence and run the actual matching feature pipeline."""

import json
from copy import deepcopy

import pytest
from test_rerank_sample_features import (
    cached_family as source_cached_family,  # noqa: F401 - shared sealed receipt fixture
)

import matching_engine.rerank_cohort_features as module
from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import policy_hash, score_candidate
from matching_engine.embeddings import HarrierEncoder, build_semantic_text
from matching_engine.models import GRADE_RANK, confirmed_strength
from matching_engine.rerank_features import feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.strict import build_strict_vector, initial_schema


def _trait(index=0):
    return {"grade": ("intern", "junior", "middle", "senior")[index % 4],
        "status": "confirmed", "levels": {"python": 5, "sql": 4, "teamwork": 3},
        "skill_statuses": {"python": "partial", "sql": "confirmed", "teamwork": "pending"},
        "salary_min": 50000 + index * 10000, "description": f"Синтетический проект API {index}",
        "structured_facts_available": True, "duration_months": 24, "duration_status": "partial",
        "production_support": False, "production_status": "confirmed", "role": "lead", "role_status": "pending",
        "project_domains": [{"key": "sql", "status": "self_reported"}],
        "project_stacks": [{"key": "python", "status": "partial"}]}


def _unknown(trait):
    trait.update(structured_facts_available=False, duration_months=None, duration_status=None,
                 production_support=None, production_status=None, role=None, role_status=None,
                 project_domains=[], project_stacks=[])
    return trait


@pytest.fixture
def encoder_calls(monkeypatch):
    calls = []

    def encode(self, text):
        calls.append((self.role, self.model_version, text))
        # Only embedding inference is substituted; filters, evidence and scores are real.
        vector = [0.] * 640
        vector[int(digest({"model": self.model_version, "text": text})[:6], 16) % 640] = 1.
        return vector

    monkeypatch.setattr(HarrierEncoder, "encode", encode)
    return calls


def _inputs():
    return ({"family_id": "backend-cohort-01", "direction": "backend"},
            {"class": "backend", "domens": {"sql": 4}, "stacks": {"python": 5},
             "bio": " API и SQL "}, [_trait(i) for i in range(7)])


def _feature(vector, name):
    index = vector.names.index(name)
    return vector.values[index], vector.available[index]


def test_profile_replaces_all_seed_skills_and_preserves_independent_facts():
    trait = _trait()
    trait["skill_statuses"]["python"] = "rejected"
    trait["status"] = "rejected"
    profile = module.candidate_profile(trait, "backend")
    assert {s.key for s in (*profile.domains, *profile.stacks, *profile.soft_skills)} == set(trait["levels"])
    assert profile.projects[0].status == "rejected"
    facts = profile.projects[0].facts
    assert facts.duration_months.value == 24 and facts.duration_months.status == "partial"
    assert facts.production_support.value is False and facts.production_support.status == "confirmed"
    assert facts.role.value == "lead" and facts.role.status == "pending"
    assert facts.domains[0].status == "self_reported"
    assert profile.experience == profile.achievements == profile.preferences == [] and profile.metadata == {}
    assert profile.city == "Москва" and profile.work_types == ["remote"]
    schema = initial_schema()
    python = next(c.index for c in schema.coordinates if c.key == "python")
    assert build_strict_vector(profile, schema)[python] == 0


def test_unknowns_not_inferred_from_description_and_null_statuses_are_required():
    trait = _unknown(_trait())
    trait["description"] = "Ведущий инженер, 24 месяца, поддержка production Python SQL"
    assert module.candidate_profile(trait, "backend").projects[0].facts is None
    trait["duration_status"] = "confirmed"
    with pytest.raises(ValueError):
        module.candidate_profile(trait, "backend")


@pytest.mark.parametrize("mutation", ["unknown_key", "wrong_skill_group", "mismatched_statuses", "unknown_role"])
def test_incompatible_traits_fail_before_embedding(mutation):
    trait = _trait()
    if mutation == "unknown_key":
        trait["levels"]["unknown_stack"] = 7
        trait["skill_statuses"]["unknown_stack"] = "confirmed"
    elif mutation == "wrong_skill_group":
        trait["project_domains"] = [{"key": "python", "status": "confirmed"}]
    elif mutation == "mismatched_statuses":
        del trait["skill_statuses"]["python"]
    else:
        trait["role"] = "manager"
    with pytest.raises(ValueError):
        module.candidate_profile(trait, "backend")


def test_builder_uses_real_scores_ranks_and_current_contract(tmp_path, monkeypatch, encoder_calls):
    item, vacancy, traits = _inputs()
    traits[0] = _unknown(traits[0])
    traits[1]["status"] = "rejected"
    traits[2]["duration_months"] = None
    traits[2]["duration_status"] = None
    captured = []
    original = module.select_candidates

    def selected(vacancy, candidates, policy, size):
        captured.append((vacancy, candidates))
        return original(vacancy, candidates, policy, size)

    monkeypatch.setattr(module, "select_candidates", selected)
    vectors, scores, order, provenance = module.build_family_features(item, vacancy, traits, tmp_path / "cache.json", "train")
    ready, candidates = captured[0]
    assert len(captured) == 1 and len(vectors) == len(scores) == provenance["passed_profiles"] == 7
    assert encoder_calls[0][0] == "vacancy_query" and encoder_calls[0][2] == "Описание вакансии: API и SQL"
    assert [entry[0] for entry in encoder_calls[1:]] == ["document"] * 7
    assert provenance["current_feature_schema_hash"] == feature_schema(initial_schema()).schema_hash
    assert provenance["current_policy_hash"] == policy_hash(ScoringPolicy())
    profiles = [module.candidate_profile(trait, "backend") for trait in traits]
    assert order == sorted(range(7), key=lambda i: (-GRADE_RANK[profiles[i].grade_result.grade], -confirmed_strength(profiles[i]), i))
    assert scores == [score_candidate(ready, candidate, ScoringPolicy()).score for candidate in candidates]
    for index, (candidate, vector) in enumerate(zip(candidates, vectors, strict=True)):
        assert _feature(vector, "main_score")[0] == scores[index]
        assert _feature(vector, "grade")[0] == GRADE_RANK[traits[index]["grade"]] / 3
        assert _feature(vector, "rank_percentile")[0] == 1 - (candidate.rank - 1) / 6
        assert candidate.class_size == 7
    for index in (0, 1):
        assert _feature(vectors[index], "project.duration") == (0., False)
        assert _feature(vectors[index], "project.production") == (0., False)
    assert _feature(vectors[2], "project.duration") == (0., False)
    assert _feature(vectors[2], "project.production") == (0., True)
    assert _feature(vectors[2], "project.lead") == (.5, True)


def test_cache_reuse_is_role_specific_preserves_old_entries_and_provenance(tmp_path, encoder_calls):
    item, vacancy, traits = _inputs()
    path = tmp_path / "semantic-cache.json"
    old_key = digest({"model": "historical", "text": "retained"})
    path.write_text(json.dumps({old_key: [1.] + [0.] * 639}))
    first = module.build_family_features(item, vacancy, traits, path, "train")
    first_calls = list(encoder_calls)
    assert len(first_calls) == 8
    encoder_calls.clear()
    assert module.build_family_features(item, vacancy, traits, path, "train") == first
    assert encoder_calls == []
    contents = json.loads(path.read_text())
    assert contents[old_key] == [1.] + [0.] * 639
    assert len(contents) == 9
    for _, model, text in first_calls:
        assert digest({"model": model, "text": text}) in contents
    golden = module.build_family_features(item, vacancy, traits, path, "golden")
    assert golden[:3] == first[:3] and golden[3]["candidate_snapshot_hashes"] != first[3]["candidate_snapshot_hashes"]


def test_atomic_merge_keeps_entry_written_during_inference(tmp_path, monkeypatch):
    path = tmp_path / "semantic-cache.json"
    other_key = digest({"model": "other", "text": "concurrent"})

    def encode(_self, _text):
        current = json.loads(path.read_text()) if path.exists() else {}
        current[other_key] = [1.] + [0.] * 639
        path.write_text(json.dumps(current))
        return [1.] + [0.] * 639

    monkeypatch.setattr(HarrierEncoder, "encode", encode)
    item, vacancy, traits = _inputs()
    module.build_family_features(item, vacancy, traits, path, "train")
    assert other_key in json.loads(path.read_text())
    assert not list(tmp_path.glob(".semantic-cache.json.*"))


def _changed_unit_vector(vector):
    result = [0.] * 640
    result[(next(index for index, value in enumerate(vector) if value == 1.) + 1) % 640] = 1.
    return result


def test_new_cached_unit_vector_mutation_fails_against_immutable_receipt(tmp_path, monkeypatch, encoder_calls):
    item, vacancy, traits = _inputs()
    path = tmp_path / "semantic-cache.json"
    result = module.build_family_features(item, vacancy, traits, path, "train")
    key = result[3]["semantic_keys"][0]
    receipt_path = tmp_path / "semantic-cache.entries" / digest("train") / f"{key}.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["key"] == key and receipt["model"] == HarrierEncoder("vacancy_query").model_version
    assert receipt["text_hash"] == digest("Описание вакансии: API и SQL")
    before_receipt = receipt_path.read_bytes()
    cache = json.loads(path.read_text())
    cache[key] = _changed_unit_vector(cache[key])
    path.write_text(json.dumps(cache))
    before_cache = path.read_bytes()
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("sealed mutation attempted inference"))
    with pytest.raises(ValueError, match="immutable entry receipt"):
        module.build_family_features(item, vacancy, traits, path, "train")
    assert path.read_bytes() == before_cache and receipt_path.read_bytes() == before_receipt


def test_seed_vector_mutation_fails_without_touching_original_cache(tmp_path, monkeypatch, encoder_calls):
    item, vacancy, traits = _inputs()
    text = "Описание вакансии: API и SQL"
    encoder = HarrierEncoder("vacancy_query")
    key = digest({"model": encoder.model_version, "text": text})
    seed = {key: encoder.encode(text)}
    seed_snapshot = deepcopy(seed)
    original_path = tmp_path / "old-semantic-cache.json"
    original_path.write_text(json.dumps(seed))
    original_bytes = original_path.read_bytes()
    path = tmp_path / "semantic-cache.json"
    path.write_text(json.dumps(seed))
    result = module.build_family_features(item, vacancy, traits, path, "train", semantic_seed=seed)
    assert result[3]["semantic_entry_receipts"][0]["source"] == "verified_seed"
    assert result[3]["semantic_seed_hash"] == digest(seed)
    assert not (tmp_path / "semantic-cache.entries" / digest("train") / f"{key}.json").exists()
    cache = json.loads(path.read_text())
    cache[key] = _changed_unit_vector(cache[key])
    path.write_text(json.dumps(cache))
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("seed mutation attempted inference"))
    with pytest.raises(ValueError, match="seed vector differs"):
        module.build_family_features(item, vacancy, traits, path, "train", semantic_seed=seed)
    assert seed == seed_snapshot and original_path.read_bytes() == original_bytes


def test_missing_receipt_recovers_only_after_exact_local_reencoding(tmp_path, encoder_calls):
    item, vacancy, traits = _inputs()
    path = tmp_path / "semantic-cache.json"
    initial = module.build_family_features(item, vacancy, traits, path, "train")
    key = initial[3]["semantic_keys"][0]
    receipt_path = tmp_path / "semantic-cache.entries" / digest("train") / f"{key}.json"
    original_receipt = receipt_path.read_bytes()
    receipt_path.unlink()
    encoder_calls.clear()
    assert module.build_family_features(item, vacancy, traits, path, "train") == initial
    assert len(encoder_calls) == 1 and encoder_calls[0][0] == "vacancy_query"
    assert receipt_path.read_bytes() == original_receipt
    receipt_path.unlink()
    cache = json.loads(path.read_text())
    cache[key] = _changed_unit_vector(cache[key])
    path.write_text(json.dumps(cache))
    with pytest.raises(ValueError, match="unsealed semantic cache vector differs"):
        module.build_family_features(item, vacancy, traits, path, "train")
    assert not receipt_path.exists()


@pytest.mark.parametrize("invalid", [[1.] * 640, [1.] + [0.] * 638, [True] + [0.] * 639])
def test_invalid_pinned_cache_never_replaced_with_inference(tmp_path, monkeypatch, invalid):
    item, vacancy, traits = _inputs()
    key = digest({"model": HarrierEncoder("vacancy_query").model_version, "text": "Описание вакансии: API и SQL"})
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({key: invalid}))
    before = path.read_bytes()
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("incompatible cache attempted inference"))
    with pytest.raises(ValueError, match="invalid.*Harrier"):
        module.build_family_features(item, vacancy, traits, path, "train")
    assert path.read_bytes() == before


def test_explicit_vacancy_fields_are_preserved_and_missing_card_fails(tmp_path, encoder_calls):
    item, vacancy, traits = _inputs()
    vacancy.update(city="Санкт-Петербург", type="onsite", zp_min=150000, zp_max=180000)
    with pytest.raises(ValueError, match="strict filter excluded cards"):
        module.build_family_features(item, vacancy, traits, tmp_path / "cache.json", "train")
    with pytest.raises(ValueError, match="seven traits"):
        module.build_family_features(item, vacancy, traits[:6], tmp_path / "cache.json", "train")


def test_global_class_rank_uses_other_family_grade_and_strength_once(tmp_path, monkeypatch, encoder_calls):
    item, vacancy, first = _inputs()
    for trait in first:
        trait["grade"] = "junior"
    second = [_trait(i) for i in range(7)]
    for index, trait in enumerate(second):
        trait["description"] = f"Другой проект направления {index}"
        trait["grade"] = "senior" if index else "junior"
    # The other junior outranks the first family on confirmed skill strength;
    # the six seniors outrank both juniors on grade.
    second[0]["levels"]["sql"] = 7
    pool = [module.candidate_profile(trait, "backend") for trait in (*first, *second)]
    captured = []
    original = module.select_candidates

    def selected(vacancy, candidates, policy, size):
        captured.append(candidates)
        return original(vacancy, candidates, policy, size)

    monkeypatch.setattr(module, "select_candidates", selected)
    path = tmp_path / "cache.json"
    first_result = module.build_family_features(item, vacancy, first, path, "train", class_profiles=pool)
    first_vectors, first_scores, first_order, provenance = first_result
    assert provenance["class_size"] == 14 and provenance["class_rank_scope"] == "global_direction_pool"
    assert len(provenance["class_profile_snapshot_hashes"]) == 14
    assert first_order == list(range(7))
    assert {candidate.class_size for candidate in captured[0]} == {14}
    assert {candidate.rank for candidate in captured[0]} == {8}
    assert all(_feature(vector, "rank_percentile") == (1 - 7 / 13, True) for vector in first_vectors)
    assert all(_feature(vector, "rating") == (1 - 7 / 13, True) for vector in first_vectors)
    second_result = module.build_family_features({**item, "family_id": "backend-cohort-02"},
                                               vacancy, second, path, "train", class_profiles=pool)
    assert captured[1][0].rank == 7 and all(candidate.rank == 1 for candidate in captured[1][1:])
    assert _feature(second_result[0][0], "rating") == (1 - 6 / 13, True)
    assert all(_feature(vector, "rating") == (1., True) for vector in second_result[0][1:])
    assert second_result[1][1] > first_scores[0]
    # Pool ordering is immaterial to ranks and immutable provenance.
    assert module.build_family_features(item, vacancy, first, path, "train", class_profiles=pool[::-1]) == first_result


def test_global_class_pool_must_contain_family_and_only_eligible_direction(tmp_path, encoder_calls):
    item, vacancy, traits = _inputs()
    profiles = [module.candidate_profile(trait, "backend") for trait in traits]
    with pytest.raises(ValueError, match="already contain"):
        module.build_family_features(item, vacancy, traits, tmp_path / "cache.json", "train", class_profiles=profiles[:-1])
    other = module.candidate_profile(_trait(), "frontend")
    with pytest.raises(ValueError, match="same-direction"):
        module.build_family_features(item, vacancy, traits, tmp_path / "cache.json", "train", class_profiles=[*profiles, other])
    assert encoder_calls == []


def test_global_class_duplicates_and_frozen_lineage_extras_fail_before_inference(tmp_path, encoder_calls):
    item, vacancy, traits = _inputs()
    profiles = [module.candidate_profile(trait, "backend") for trait in traits]
    lineage = {f"{item['family_id']}/{index}": digest(profile.model_dump(mode="json"))
               for index, profile in enumerate(profiles)}
    path = tmp_path / "cache.json"
    with pytest.raises(ValueError, match="duplicate class profile"):
        module.build_family_features(item, vacancy, traits, path, "train", class_profiles=[*profiles, profiles[0]])
    extra_trait = _trait()
    extra_trait["description"] = "Профиль вне утверждённых источников"
    extra = module.candidate_profile(extra_trait, "backend")
    with pytest.raises(ValueError, match="lineage must exactly match"):
        module.build_family_features(item, vacancy, traits, path, "train",
                                     class_profiles=[*profiles, extra], class_source_lineage=lineage)
    with pytest.raises(ValueError, match="lineage must exactly match"):
        module.build_family_features(item, vacancy, traits, path, "train",
                                     class_profiles=profiles, class_source_lineage=dict(list(lineage.items())[:-1]))
    assert encoder_calls == [] and not path.exists()
    result = module.build_family_features(item, vacancy, traits, path, "train",
                                         class_profiles=profiles, class_source_lineage=dict(reversed(list(lineage.items()))))
    assert result[3]["class_source_lineage"] == dict(sorted(lineage.items()))
    assert list(result[3]["class_source_lineage"]) == sorted(lineage)


def test_legacy_projection_verifies_sources_and_keeps_selected_nested_statuses(request, monkeypatch):
    directory, record, profiles = request.getfixturevalue("source_cached_family")
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("legacy projection attempted inference"))
    indices = [0, 1, 2, 3, 4, 5, 9]
    traits = module.legacy_traits(directory, record, indices)
    assert [trait["description"] for trait in traits] == [profiles[index].description for index in indices]
    assert traits[-1]["status"] == "rejected" and traits[-1]["duration_status"] == "partial"
    assert traits[-1]["project_domains"] == [{"key": "sql", "status": "partial"}]
    assert traits[0]["duration_months"] is traits[0]["duration_status"] is None
    for trait, index in zip(traits, indices, strict=True):
        profile = module.candidate_profile(trait, "backend")
        assert profile.projects == profiles[index].projects
        assert build_semantic_text(profile) == build_semantic_text(profiles[index])
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    corrupt = deepcopy(record)
    corrupt["traits"][0]["description"] = "changed"
    with pytest.raises(ValueError, match="integrity"):
        module.legacy_traits(directory, corrupt, indices)
    with pytest.raises(ValueError, match="unique"):
        module.legacy_traits(directory, record, [0, 0])
