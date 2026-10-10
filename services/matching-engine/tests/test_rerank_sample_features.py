"""Current-feature reconstruction uses sealed synthetic receipts without external calls."""

import itertools
import json
from copy import deepcopy
from dataclasses import asdict

import pytest

import matching_engine.rerank_sample_features as module
from matching_engine.embeddings import HarrierEncoder, build_semantic_text
from matching_engine.models import GRADE_RANK, confirmed_strength
from matching_engine.rerank_experiment import family_manifest
from matching_engine.rerank_features import feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    PROMPT_VERSION,
    QWEN,
    SYSTEMONE_MODEL,
    SystemOneProviders,
    teacher_identity,
)
from matching_engine.strict import initial_schema


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _seal(record):
    record["record_hash"] = digest({key: value for key, value in record.items() if key != "record_hash"})


@pytest.fixture
def cached_family(tmp_path, monkeypatch):
    provider = SystemOneProviders(tmp_path / "provider-cache")
    item = deepcopy(next(item for item in family_manifest() if item["family_id"] == "backend-02"))
    raw_traits = [{"grade": "junior", "levels": {key: (index + i) % 8 for i, key in enumerate(
        ("python", "react", "sql", "rest_api", "teamwork"))},
        "status": ("confirmed", "partial", "self_reported", "pending")[index % 4],
        "duration_months": index * 3, "production_support": index % 2 == 0,
        "salary_min": 50000 + index * 10000, "description": f"Synthetic candidate {index}"}
        for index in range(10)]
    batches = []
    for batch in (1, 2):
        traits = raw_traits[(batch - 1) * 5:batch * 5]
        payload = {"messages": [{"content": f"Profiles for backend. Scenario: {item['preference']} Batch {batch}."}]}
        request = provider._identity("/v1/chat/completions", payload, "generation")
        response = {"choices": [{"message": {"content": json.dumps({"profiles": traits})}}]}
        key = digest(request)
        _write(provider.directory / f"{key}.json", {"request": request, "request_hash": key,
            "response": response, "response_hash": digest(response)})
        receipt = {"family_id": item["family_id"], "batch": batch,
            "generator": {key: request[key] for key in ("model", "prompt_version")},
            "source_cache_key": key, "source_response_hash": digest(response), "raw_count": 5,
            "traits": traits, "dropped": [], "reuse_policy": "verified_cache_only"}
        receipt["receipt_hash"] = digest(receipt)
        batches.append(receipt)
    transformed = deepcopy(raw_traits)
    for index, trait in enumerate(transformed):
        trait["grade"] = ("intern", "junior", "middle", "senior")[(index + 2) % 4]
        trait["structured_facts_available"] = index % 4 != 0
        if not trait["structured_facts_available"]:
            trait["duration_months"] = trait["production_support"] = None
        if index == 9:
            trait["status"] = "rejected"
    names = feature_schema(initial_schema()).names
    record = {"manifest": item, "teacher": teacher_identity(), "traits": transformed,
        "vacancy": {"class": "backend", "domens": {"sql": 4}, "stacks": {"python": 5, "react": 3},
            "soft_skills": {"teamwork": 3}, "bio": "Разработка API и интерфейсов, SQL, взаимодействие в команде",
            "salary": {"min": 50000, "max": 200000}},
        "vectors": [{"names": list(names), "values": [0.] * len(names), "available": [True] * len(names),
            "schema_hash": "0" * 64} for _ in range(10)], "main_scores": [0.] * 10,
        "grades": [1] * 10, "pairs": [(a, b, "tie") for a, b in itertools.combinations(range(10), 2)],
        "swap_audits": [True] * 3, "generation_batches": batches, "profile_order": []}
    seed = json.loads(module.SEED_PATH.read_text(encoding="utf-8"))[0]["profile"]
    profiles = module._candidate_profiles(record, batches, seed)
    record["profile_order"] = sorted(range(10), key=lambda i: (
        -GRADE_RANK[profiles[i].grade_result.grade], -confirmed_strength(profiles[i]), i))

    def capture(_self, path, payload, kind):
        question = "preference" if kind == "pair" else "suitability"
        options = ("A", "B", "tie", "insufficient") if kind == "pair" else ("0", "1", "2", "3")
        answer = {"confidence": 1., "probabilities": {key: float(key == ("tie" if kind == "pair" else "1"))
                  for key in options}, **({"choice": "tie"} if kind == "pair" else {"score": 1})}
        response = {"model": SYSTEMONE_MODEL, "answers": {question: answer}}
        request = _self._identity(path, payload, kind)
        key = digest(request)
        _write(provider.directory / f"{key}.json", {"request": request, "request_hash": key,
            "response": response, "response_hash": digest(response)})
        return response

    with monkeypatch.context() as patch:
        patch.setattr(SystemOneProviders, "request", capture)
        for a, b, _ in record["pairs"]:
            provider.pair(record["vacancy"], transformed[a], transformed[b], item["preference"])
        for a, b, _ in record["pairs"][:3]:
            provider.pair(record["vacancy"], transformed[b], transformed[a], item["preference"])
        for trait in transformed:
            provider.grade(record["vacancy"], trait, item["preference"])
    semantics = {digest({"model": HarrierEncoder().model_version, "text": build_semantic_text(profile)}):
                 [1.] + [0.] * 639 for profile in profiles}
    semantics[digest({"model": HarrierEncoder("vacancy_query").model_version, "text": record["vacancy"]["bio"]})] = [1.] + [0.] * 639
    _write(tmp_path / "semantic-cache.json", semantics)
    _seal(record)
    vectors, scores, _ = module.rebuild_family_features(tmp_path, record)
    record["vectors"] = [dict(asdict(vector), schema_hash="0" * 64) for vector in vectors]
    record["main_scores"] = scores
    _seal(record)
    _write(tmp_path / "family.json", record)
    return tmp_path, record, profiles


def _tree_bytes(directory):
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_rebuild_computes_current_policy_once_and_keeps_every_source_byte(cached_family, monkeypatch):
    directory, record, _ = cached_family
    before, source = _tree_bytes(directory), deepcopy(record)
    calls = []
    original = module.select_candidates

    def selected(vacancy, candidates, policy, size):
        calls.append(policy.version)
        return original(vacancy, candidates, policy, size)

    monkeypatch.setattr(module, "select_candidates", selected)
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("Harrier inference attempted"))
    monkeypatch.setattr(SystemOneProviders, "_api_key", lambda *_: pytest.fail("credential read attempted"))
    monkeypatch.setattr(SystemOneProviders, "_open", lambda *_: pytest.fail("network attempted"))
    vectors, scores, receipt = module.rebuild_family_features(directory, record)
    assert calls == ["main-v2"]
    assert receipt["current_feature_schema_hash"] == feature_schema(initial_schema()).schema_hash
    assert receipt["source_feature_schema_hash"] == "0" * 64
    assert {vector.schema_hash for vector in vectors} == {receipt["current_feature_schema_hash"]}
    assert receipt["changed_feature_values"] == receipt["changed_main_scores"] == 0
    assert scores == record["main_scores"]
    assert _tree_bytes(directory) == before and record == source
    assert module.rebuild_family_features(directory, record) == (vectors, scores, receipt)


def test_historical_values_are_recomputed_not_relabelled(cached_family):
    directory, record, _ = cached_family
    record["vectors"][0]["values"] = list(record["vectors"][0]["values"])
    record["vectors"][0]["values"][0] = record["main_scores"][0] = .123
    _seal(record)
    vectors, scores, receipt = module.rebuild_family_features(directory, record)
    assert scores[0] != .123 and vectors[0].values[0] == scores[0]
    assert receipt["changed_main_scores"] == receipt["changed_feature_values"] == 1
    assert record["vectors"][0]["values"][0] == .123


def test_all_grades_and_evidence_states_survive_with_unknowns_absent(cached_family):
    directory, record, profiles = cached_family
    assert {profile.grade_result.grade for profile in profiles} == {"intern", "junior", "middle", "senior"}
    assert {profile.projects[0].status for profile in profiles} == {
        "confirmed", "partial", "self_reported", "pending", "rejected"}
    for index, profile in enumerate(profiles):
        assert profile.grade_result.grade == record["traits"][index]["grade"]
        assert profile.projects[0].status == record["traits"][index]["status"]
        assert {skill.status for skill in profile.stacks} == {record["traits"][index]["status"]}
        if index % 4 == 0:
            assert profile.projects[0].facts is None
    assert profiles[-1].projects[0].facts.domains[0].status == "partial"
    vectors, _, _ = module.rebuild_family_features(directory, record)
    for index in (0, 4, 8, 9):
        for name in ("project.duration", "project.production", "project.duration_evidence"):
            assert not vectors[index].available[vectors[index].names.index(name)]
    assert vectors[1].available[vectors[1].names.index("project.production")]
    assert vectors[1].values[vectors[1].names.index("project.production")] == 0


@pytest.mark.parametrize("missing", ["file", "document", "query"])
def test_missing_semantics_never_attempts_inference_or_cache_write(cached_family, monkeypatch, missing):
    directory, record, profiles = cached_family
    path = directory / "semantic-cache.json"
    if missing == "file":
        path.unlink()
    else:
        semantics = json.loads(path.read_text())
        encoder = HarrierEncoder("vacancy_query") if missing == "query" else HarrierEncoder()
        text = record["vacancy"]["bio"] if missing == "query" else build_semantic_text(profiles[0])
        del semantics[digest({"model": encoder.model_version, "text": text})]
        _write(path, semantics)
    before = _tree_bytes(directory)
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("cache miss attempted inference"))
    with pytest.raises(ValueError, match="missing.*semantic"):
        module.rebuild_family_features(directory, record)
    assert _tree_bytes(directory) == before


def test_unknown_production_and_duration_are_not_invented():
    raw = {"grade": "junior", "levels": {key: 1 for key in ("python", "react", "sql", "rest_api", "teamwork")},
        "status": "partial", "duration_months": None, "production_support": None,
        "salary_min": 50000, "description": "Synthetic", "structured_facts_available": True}
    seed = json.loads(module.SEED_PATH.read_text())[0]["profile"]
    profiles = module._candidate_profiles({"manifest": {"direction": "backend"}, "traits": [raw]},
        [{"traits": [raw]}], seed)
    assert profiles[0].projects[0].facts.duration_months is None
    assert profiles[0].projects[0].facts.production_support is None


def test_mutated_source_receipt_and_mismatched_profile_fail(cached_family):
    directory, record, _ = cached_family
    record["traits"][1]["levels"]["sql"] = 7 - record["traits"][1]["levels"]["sql"]
    _seal(record)
    with pytest.raises(ValueError):
        module.rebuild_family_features(directory, record)
    path = directory / "provider-cache" / f"{record['generation_batches'][0]['source_cache_key']}.json"
    cached = json.loads(path.read_text())
    cached["response"]["choices"][0]["message"]["content"] = "[]"
    _write(path, cached)
    with pytest.raises(ValueError):
        module.rebuild_family_features(directory, record)


def test_unrelated_malformed_probe_receipt_is_not_a_family_source(cached_family):
    directory, record, _ = cached_family
    _write(directory / "provider-cache" / "unrelated-malformed.json", {"response": {"content": "{"}})
    vectors, _, _ = module.rebuild_family_features(directory, record)
    assert len(vectors) == 10


def test_wrong_vacancy_recipe_is_rejected_before_teacher_cache_access(cached_family):
    directory, record, _ = cached_family
    record["vacancy"]["stacks"]["python"] = 4
    _seal(record)
    with pytest.raises(ValueError, match="source vacancy differs"):
        module.verify_cached_sources(directory, record)


def test_missing_provider_cache_is_rejected_without_creating_directories(tmp_path):
    with pytest.raises(ValueError, match="missing provider source cache"):
        module.verify_cached_sources(tmp_path, {})
    assert list(tmp_path.iterdir()) == []


def _replace_source_request(directory, record, request, *, malformed_response=False):
    batch = record["generation_batches"][0]
    cached = json.loads((directory / "provider-cache" / f"{batch['source_cache_key']}.json").read_text())
    cached["request"] = request
    key = cached["request_hash"] = digest(request)
    if malformed_response:
        cached["response"]["choices"][0]["message"]["content"] = "{"
        cached["response_hash"] = digest(cached["response"])
    _write(directory / "provider-cache" / f"{key}.json", cached)
    batch["source_cache_key"] = key
    batch["source_response_hash"] = cached["response_hash"]
    batch["generator"] = {field: request[field] for field in ("model", "prompt_version")}
    batch["receipt_hash"] = digest({field: value for field, value in batch.items() if field != "receipt_hash"})
    _seal(record)


@pytest.mark.parametrize("field,value", [
    ("prompt_version", "unapproved-generation-prompt"),
    ("path", "/unapproved/chat/completions"),
    ("endpoint", "https://unapproved.example.test/v1/chat/completions"),
    ("kind", "pair"),
])
def test_self_consistent_unapproved_remote_source_identity_fails_before_decode(cached_family, field, value):
    directory, record, _ = cached_family
    batch = record["generation_batches"][0]
    request = json.loads((directory / "provider-cache" / f"{batch['source_cache_key']}.json").read_text())["request"]
    request[field] = value
    _replace_source_request(directory, record, request, malformed_response=True)
    with pytest.raises(ValueError, match="unapproved generation source identity"):
        module.verify_cached_sources(directory, record)


def _legacy_source_request(directory, record):
    batch = record["generation_batches"][0]
    request = json.loads((directory / "provider-cache" / f"{batch['source_cache_key']}.json").read_text())["request"]
    return {"path": "/api/v1/chat/completions", "payload": request["payload"],
            "model": QWEN, "prompt_version": PROMPT_VERSION}


def test_approved_legacy_local_source_identity_remains_readable(cached_family):
    directory, record, _ = cached_family
    _replace_source_request(directory, record, _legacy_source_request(directory, record))
    before = _tree_bytes(directory)
    batches = module.verify_cached_sources(directory, record)
    assert batches[0]["generator"] == {"model": QWEN, "prompt_version": PROMPT_VERSION}
    assert _tree_bytes(directory) == before


@pytest.mark.parametrize("field,value", [
    ("prompt_version", "unapproved-legacy-prompt"),
    ("path", "/v1/chat/completions"),
    ("endpoint", "https://unapproved.example.test/v1/chat/completions"),
    ("kind", "generation"),
])
def test_self_consistent_unapproved_legacy_source_identity_fails_before_decode(cached_family, field, value):
    directory, record, _ = cached_family
    request = _legacy_source_request(directory, record)
    request[field] = value
    _replace_source_request(directory, record, request, malformed_response=True)
    with pytest.raises(ValueError, match="unapproved generation source identity"):
        module.verify_cached_sources(directory, record)


def test_exact_historical_qwen_v1_identity_is_read_only_and_explicit_in_provenance(cached_family):
    directory, record, _ = cached_family
    batch = record["generation_batches"][0]
    request = json.loads((directory / "provider-cache" / f"{batch['source_cache_key']}.json").read_text())["request"]
    request["prompt_version"] = "rerank-sverk-generation-v1"
    _replace_source_request(directory, record, request)
    before, original = _tree_bytes(directory), deepcopy(record)
    _, _, provenance = module.rebuild_family_features(directory, record)
    assert provenance["generation_source_identities"][0]["approved_source_identity"] == "historical_qwen_v1_cache_only"
    assert provenance["generation_source_identities"][1]["approved_source_identity"] == "current_remote_generation_identity"
    assert record == original and _tree_bytes(directory) == before


@pytest.mark.parametrize("field,value", [
    ("model", GEMMA_GENERATION_MODEL),
    ("endpoint", "https://unapproved.example.test/v1/chat/completions"),
    ("path", "/api/v1/chat/completions"),
    ("kind", "pair"),
    ("prompt_version", "rerank-sverk-generation-v999"),
])
def test_historical_qwen_exception_cannot_expand_to_other_identities(cached_family, field, value):
    directory, record, _ = cached_family
    batch = record["generation_batches"][0]
    request = json.loads((directory / "provider-cache" / f"{batch['source_cache_key']}.json").read_text())["request"]
    request["prompt_version"] = "rerank-sverk-generation-v1"
    request[field] = value
    _replace_source_request(directory, record, request, malformed_response=True)
    with pytest.raises(ValueError, match="unapproved generation source identity"):
        module.verify_cached_sources(directory, record)
