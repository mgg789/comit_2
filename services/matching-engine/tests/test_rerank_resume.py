"""Exercise cache-only recovery and variable family sizes without live providers."""

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import matching_engine.rerank_experiment as experiment
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    SystemOneProviders,
    generation_identity,
    teacher_identity,
)


def _traits(index):
    return {"grade": "junior", "levels": {"python": 4, "react": 2, "sql": 3, "rest_api": 4, "teamwork": 3},
            "status": "confirmed", "duration_months": 12 + index, "production_support": False,
            "salary_min": 70000 + index * 1000, "description": f"Синтетический профиль {index}"}


def _family(family_id="backend-04"):
    return next(item for item in experiment.family_manifest() if item["family_id"] == family_id)


def _cached_batch(directory, family, batch, *, partial=False):
    values = [_traits(index + (batch - 1) * 5) for index in range(5)]
    if partial:
        values[4]["levels"]["python"] = 8
    request = {"path": "/v1/chat/completions", "kind": "generation", **generation_identity(),
               "prompt_version": "rerank-sverk-generation-v1",
               "payload": {"model": generation_identity()["model"], "messages": [{"role": "user", "content": (
                   f"Create 5 diverse synthetic IT candidate traits for {family['direction']}. "
                   f"Scenario: {family['preference']} Batch {batch}. JSON only.")}]}}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"profiles": values})}}]}
    cache_key = digest(request)
    record = {"request": request, "request_hash": cache_key, "response": response,
              "response_hash": digest(response), "usage": {"total_tokens": 25}, "usage_estimated": False}
    cache_path = directory / "provider-cache" / f"{cache_key}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(record), encoding="utf-8")
    return cache_key, cache_path, values


def _seal_nine_profiles(directory, family):
    for batch in (1, 2):
        cache_key, _path, _values = _cached_batch(directory, family, batch, partial=batch == 1)
        experiment.reuse_cached_batch(directory, family["family_id"], batch, cache_key)


def _passed_pilot(directory):
    pilot = {"state": "passed", "teacher": teacher_identity(), "swap_consistency": 1., "decisive_pairs": 10}
    pilot["report_hash"] = digest(pilot)
    (directory / "pilot-report.json").write_text(json.dumps(pilot), encoding="utf-8")
    (directory / "provider-cache" / "usage.json").write_text(json.dumps({"generation": 2}), encoding="utf-8")


class _FakeHarrier:
    """Provide an explicit unit-test unit vector without loading the real model."""

    def __init__(self, role="document"):
        self.model_version = f"unit-test-harrier:{role}"

    def encode(self, _text):
        return [1.] + [0.] * 639


def _forbid_http(monkeypatch):
    monkeypatch.setattr(SystemOneProviders, "_open", lambda *_args: pytest.fail("resume attempted live HTTP"))
    monkeypatch.setattr(SystemOneProviders, "_api_key", lambda *_args: pytest.fail("resume read a credential"))


def test_parallel_preserves_input_order_and_caps_active_jobs():
    active = maximum = 0
    guard = threading.Lock()

    def work(index):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(.002 * (12 - index))
        with guard:
            active -= 1
        return index * index

    assert experiment._parallel(work, list(range(12))) == [index * index for index in range(12)]
    assert 2 <= maximum <= 5


@pytest.mark.parametrize("workers", [0, 6])
def test_parallel_rejects_unsupported_worker_count(workers):
    with pytest.raises(ValueError, match="between one and five"):
        experiment._parallel(lambda value: value, [1], workers)


def test_parallel_failure_stops_new_submissions_at_the_bounded_window(monkeypatch):
    submitted = []

    class TrackingExecutor(ThreadPoolExecutor):
        def submit(self, function, *args, **kwargs):
            submitted.append(args[0])
            return super().submit(function, *args, **kwargs)

    def work(index):
        if index == 0:
            raise RuntimeError("synthetic first job failed")
        time.sleep(.02)
        return index

    monkeypatch.setattr(experiment, "ThreadPoolExecutor", TrackingExecutor)
    with pytest.raises(RuntimeError, match="synthetic first job failed"):
        experiment._parallel(work, list(range(20)))
    assert submitted == list(range(5))


def test_cache_only_partial_batch_keeps_valid_profiles_and_drop_receipt(tmp_path, monkeypatch):
    family = _family()
    cache_key, cache_path, raw = _cached_batch(tmp_path, family, 1, partial=True)
    usage_path = tmp_path / "provider-cache" / "usage.json"
    usage_path.write_text(json.dumps({"generation": 73, "pair": 90, "grade": 12}))
    original_cache, original_usage = cache_path.read_bytes(), usage_path.read_bytes()
    _forbid_http(monkeypatch)
    receipt = experiment.reuse_cached_batch(tmp_path, family["family_id"], 1, cache_key)
    assert receipt["raw_count"] == 5
    assert receipt["traits"] == raw[:4]
    assert receipt["reuse_policy"] == "explicit_cache_only"
    assert receipt["generator"]["prompt_version"] == "rerank-sverk-generation-v1"
    assert receipt["source_cache_key"] == cache_key
    assert receipt["source_response_hash"] == json.loads(original_cache)["response_hash"]
    assert receipt["dropped"] == [{"source_index": 4, "raw_hash": digest(raw[4]),
                                  "error_type": "ValueError", "reason": "invalid level"}]
    assert receipt["receipt_hash"] == digest({key: value for key, value in receipt.items() if key != "receipt_hash"})
    assert cache_path.read_bytes() == original_cache
    assert usage_path.read_bytes() == original_usage
    sealed_path = tmp_path / "generation-batches" / f"{family['family_id']}-1.json"
    sealed_bytes = sealed_path.read_bytes()
    assert experiment.reuse_cached_batch(tmp_path, family["family_id"], 1, cache_key) == receipt
    assert sealed_path.read_bytes() == sealed_bytes


@pytest.mark.parametrize("mutation", ["request_hash", "request", "response_hash", "response"])
def test_cache_reuse_refuses_tampered_hashes_before_sealing(tmp_path, monkeypatch, mutation):
    family = _family()
    cache_key, cache_path, _raw = _cached_batch(tmp_path, family, 1)
    record = json.loads(cache_path.read_text())
    if mutation in ("request_hash", "response_hash"):
        record[mutation] = "0" * 64
    elif mutation == "request":
        record["request"]["payload"]["model"] = "different-model"
    else:
        record["response"]["choices"][0]["message"]["content"] = "[]"
    cache_path.write_text(json.dumps(record))
    _forbid_http(monkeypatch)
    with pytest.raises(ValueError, match="integrity failed"):
        experiment.reuse_cached_batch(tmp_path, family["family_id"], 1, cache_key)
    assert not (tmp_path / "generation-batches").exists()


@pytest.mark.parametrize("family_id,batch", [("backend-05", 1), ("backend-04", 2)])
def test_cache_reuse_refuses_another_family_or_batch(tmp_path, monkeypatch, family_id, batch):
    cache_key, _cache_path, _raw = _cached_batch(tmp_path, _family(), 1)
    _forbid_http(monkeypatch)
    with pytest.raises(ValueError, match="another source family"):
        experiment.reuse_cached_batch(tmp_path, family_id, batch, cache_key)
    assert not (tmp_path / "generation-batches").exists()


@pytest.mark.parametrize("mutation", ["hash", "family"])
def test_sealed_generation_batch_refuses_tampering_without_a_provider_call(tmp_path, monkeypatch, mutation):
    family = _family()
    _seal_nine_profiles(tmp_path, family)
    receipt_path = tmp_path / "generation-batches" / f"{family['family_id']}-1.json"
    receipt = json.loads(receipt_path.read_text())
    if mutation == "hash":
        receipt["traits"][0]["salary_min"] += 1000
    else:
        receipt["family_id"] = "backend-05"
        receipt["receipt_hash"] = digest({key: value for key, value in receipt.items() if key != "receipt_hash"})
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(SystemOneProviders, "generate", lambda *_args: pytest.fail("tampered receipt triggered generation"))
    _forbid_http(monkeypatch)
    provider = SystemOneProviders(tmp_path / "provider-cache")
    with pytest.raises(ValueError, match="generation batch integrity"):
        experiment._generation_batch(tmp_path, provider, family, 1)


def test_nine_profile_family_uses_actual_size_for_scoring_labels_and_features(tmp_path, monkeypatch):
    family = _family()
    _seal_nine_profiles(tmp_path, family)
    _passed_pilot(tmp_path)
    monkeypatch.setattr(experiment, "family_manifest", lambda: [family])
    monkeypatch.setattr(experiment, "HarrierEncoder", _FakeHarrier)
    _forbid_http(monkeypatch)
    monkeypatch.setattr(SystemOneProviders, "generate", lambda *_args: pytest.fail("sealed batches regenerated"))
    pairs, grades, candidates, selections = [], [], [], []
    real_features, real_select = experiment.build_features, experiment.select_candidates

    def pair(_provider, _vacancy, a, b, _preferences):
        pairs.append((a["salary_min"], b["salary_min"]))
        return "A" if a["salary_min"] < b["salary_min"] else "B"

    def grade(_provider, _vacancy, trait, _preferences):
        grades.append(trait["salary_min"])
        return 2

    def features(vacancy, candidate, card, schema):
        candidates.append(candidate)
        return real_features(vacancy, candidate, card, schema)

    def select(vacancy, values, policy, top_k):
        result = real_select(vacancy, values, policy, top_k)
        selections.append((top_k, len(result.items)))
        return result

    monkeypatch.setattr(SystemOneProviders, "pair", pair)
    monkeypatch.setattr(SystemOneProviders, "grade", grade)
    monkeypatch.setattr(experiment, "build_features", features)
    monkeypatch.setattr(experiment, "select_candidates", select)
    originals = {path.name: path.read_bytes() for path in (tmp_path / "generation-batches").glob("*.json")}
    experiment._generate(tmp_path)
    record = json.loads((tmp_path / "families" / "backend-04.json").read_text())
    assert len(record["traits"]) == len(record["vectors"]) == len(record["main_scores"]) == 9
    assert record["grades"] == [2] * 9
    assert len(record["pairs"]) == 36
    assert len(pairs) == 39  # All unique pairs plus three swapped audits.
    assert len(grades) == 9
    assert record["swap_audits"] == [True, True, True]
    assert len(record["profile_order"]) == 9
    assert sorted(record["profile_order"]) == list(range(9))
    assert selections == [(9, 9)]
    assert len(candidates) == 9
    assert {candidate.class_size for candidate in candidates} == {9}
    assert all(candidate.rating_score == pytest.approx(1 - (candidate.rank - 1) / 8) for candidate in candidates)
    for vector, candidate in zip(record["vectors"], candidates, strict=True):
        assert vector["values"][vector["names"].index("rating")] == candidate.rating_score
    assert [len(batch["traits"]) for batch in record["generation_batches"]] == [4, 5]
    assert {path.name: path.read_bytes() for path in (tmp_path / "generation-batches").glob("*.json")} == originals
    assert json.loads((tmp_path / "dataset.json").read_text())["families"] == [record]


def test_failed_teacher_swaps_prevent_next_family_generation(tmp_path, monkeypatch):
    family, next_family = _family(), _family("backend-05")
    _seal_nine_profiles(tmp_path, family)
    _passed_pilot(tmp_path)
    monkeypatch.setattr(experiment, "family_manifest", lambda: [family, next_family])
    monkeypatch.setattr(experiment, "HarrierEncoder", _FakeHarrier)
    _forbid_http(monkeypatch)
    calls = []

    def generation(_provider, direction, scenario, count=5):
        calls.append((direction, scenario, count))
        pytest.fail("next family was generated after teacher failure")

    monkeypatch.setattr(SystemOneProviders, "generate", generation)
    monkeypatch.setattr(SystemOneProviders, "pair", lambda *_args: "A")
    monkeypatch.setattr(SystemOneProviders, "grade", lambda *_args: pytest.fail("grade called after failed swaps"))
    with pytest.raises(ValueError, match="teacher consistency failed for backend-04"):
        experiment.generate(tmp_path)
    assert calls == []
    assert not (tmp_path / "families" / "backend-05.json").exists()
    assert not (tmp_path / "dataset.json").exists()
    report = json.loads((tmp_path / "generation-report.json").read_text())
    assert report["state"] == "stopped"
    assert report["completed_families"] == 0


def _historical_family(directory, monkeypatch):
    """Build historical data with actual teacher request/cache code and synthetic HTTP."""
    family = _family()
    _seal_nine_profiles(directory, family)
    _passed_pilot(directory)
    monkeypatch.setattr(experiment, "family_manifest", lambda: [family])
    monkeypatch.setattr(experiment, "HarrierEncoder", _FakeHarrier)

    def respond(_provider, request):
        payload = json.loads(request.data)
        state = json.loads(payload["state"])
        if "preference" in payload["questions"]:
            label = "A" if state["A"]["salary_min"] < state["B"]["salary_min"] else "B"
            probabilities = dict.fromkeys(("A", "B", "tie", "insufficient"), .1)
            probabilities[label] = .7
            answers = {"preference": {"choice": label, "confidence": .7, "probabilities": probabilities}}
        else:
            assert set(payload["questions"]) == {"suitability"}
            answers = {"suitability": {"score": 2, "confidence": .7,
                                       "probabilities": {"0": .1, "1": .1, "2": .7, "3": .1}}}
        response = {"model": teacher_identity()["model"], "answers": answers,
                    "usage": {"total_tokens": 13}}
        return io.BytesIO(json.dumps(response).encode())

    with monkeypatch.context() as transport:
        transport.setattr(SystemOneProviders, "_open", respond)
        transport.setattr(SystemOneProviders, "_api_key", lambda _provider: "sk-unit-history-credential")
        transport.setattr(SystemOneProviders, "generate", lambda *_args: pytest.fail("sealed generation called HTTP"))
        experiment._generate(directory)
    path = directory / "families" / f"{family['family_id']}.json"
    record = json.loads(path.read_text())
    record.pop("record_hash")
    path.write_text(json.dumps(record), encoding="utf-8")
    _forbid_http(monkeypatch)
    return family, path, record


@pytest.mark.parametrize("formatting", ["compact", "custom_whitespace"])
def test_family_seal_keeps_exact_original_history_bytes_and_uses_cache_only(tmp_path, monkeypatch, formatting):
    family, path, original_record = _historical_family(tmp_path, monkeypatch)
    if formatting == "compact":
        original_bytes = json.dumps(original_record, ensure_ascii=False, separators=(",", ":")).encode()
    else:
        original_bytes = (" \n\t" + json.dumps(original_record, ensure_ascii=False, indent=1,
                                             separators=(" , ", " : ")) + "\r\n ").encode()
    path.write_bytes(original_bytes)
    original_hash = digest(original_record)
    cache_before = {item.name: item.read_bytes() for item in (tmp_path / "provider-cache").glob("*.json")}
    usage_before = (tmp_path / "provider-cache" / "usage.json").read_bytes()
    batches_before = {item.name: item.read_bytes() for item in (tmp_path / "generation-batches").glob("*.json")}
    sealed = experiment.seal_cached_family(tmp_path, family["family_id"])
    history = tmp_path / "family-history" / f"{family['family_id']}-{original_hash}.json"
    assert history.read_bytes() == original_bytes
    assert sealed["historical_seal"] == {"source_record_hash": original_hash, "policy": "verified_cache_only"}
    assert sealed["record_hash"] == digest({key: value for key, value in sealed.items() if key != "record_hash"})
    for key in ("traits", "pairs", "grades", "vectors", "main_scores", "profile_order", "swap_audits"):
        assert sealed[key] == original_record[key]
    assert [len(batch["traits"]) for batch in sealed["generation_batches"]] == [4, 5]
    assert all(batch["reuse_policy"] == "verified_cache_only" for batch in sealed["generation_batches"])
    assert (tmp_path / "provider-cache" / "usage.json").read_bytes() == usage_before
    assert {item.name: item.read_bytes() for item in (tmp_path / "provider-cache").glob("*.json")} == cache_before
    assert {item.name: item.read_bytes() for item in (tmp_path / "generation-batches").glob("*.json")} == batches_before
    assert all("sk-unit" not in item.read_text() for item in (tmp_path / "provider-cache").glob("*.json"))
    sealed_bytes = path.read_bytes()
    assert experiment.seal_cached_family(tmp_path, family["family_id"]) == sealed
    assert path.read_bytes() == sealed_bytes
    assert history.read_bytes() == original_bytes
    assert len(list((tmp_path / "family-history").glob("*.json"))) == 1


@pytest.mark.parametrize("mutation,error", [
    ("facts", "facts differ"),
    ("pair_label", "label differs"),
    ("grade_label", "grade differs"),
    ("generation_source", "generation source cache integrity"),
    ("teacher_source", "cached teacher receipt integrity"),
    ("record_hash", "family record integrity"),
])
def test_family_seal_refuses_modified_facts_labels_sources_and_hash(tmp_path, monkeypatch, mutation, error):
    family, path, record = _historical_family(tmp_path, monkeypatch)
    if mutation == "facts":
        record["traits"][0]["salary_min"] += 1
    elif mutation == "pair_label":
        record["pairs"][0][2] = "B"
    elif mutation == "grade_label":
        record["grades"][0] = 3
    elif mutation == "record_hash":
        record["record_hash"] = "0" * 64
    else:
        kind = "generation" if mutation == "generation_source" else "pair"
        source = next(item for item in (tmp_path / "provider-cache").glob("*.json")
                      if item.name != "usage.json" and json.loads(item.read_text())["request"]["kind"] == kind)
        receipt = json.loads(source.read_text())
        receipt["response_hash"] = "0" * 64
        source.write_text(json.dumps(receipt))
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    usage_before = (tmp_path / "provider-cache" / "usage.json").read_bytes()
    with pytest.raises(ValueError, match=error):
        experiment.seal_cached_family(tmp_path, family["family_id"])
    assert path.read_bytes() == before
    assert (tmp_path / "provider-cache" / "usage.json").read_bytes() == usage_before
    assert not (tmp_path / "family-history").exists()


@pytest.mark.parametrize("rehash_receipt,error", [
    (False, "cached family generation receipt mismatch"),
    (True, "family generation provenance differs"),
])
def test_sealed_family_receipt_mutation_is_rejected_even_with_a_new_record_hash(
    tmp_path, monkeypatch, rehash_receipt, error,
):
    family, path, _record = _historical_family(tmp_path, monkeypatch)
    sealed = experiment.seal_cached_family(tmp_path, family["family_id"])
    original_history = {item.name: item.read_bytes() for item in (tmp_path / "family-history").glob("*.json")}
    sealed["generation_batches"][0]["source_response_hash"] = "0" * 64
    if rehash_receipt:
        receipt = sealed["generation_batches"][0]
        receipt["receipt_hash"] = digest({key: value for key, value in receipt.items() if key != "receipt_hash"})
    sealed["record_hash"] = digest({key: value for key, value in sealed.items() if key != "record_hash"})
    path.write_text(json.dumps(sealed), encoding="utf-8")
    before = path.read_bytes()
    usage_before = (tmp_path / "provider-cache" / "usage.json").read_bytes()
    with pytest.raises(ValueError, match=error):
        experiment.seal_cached_family(tmp_path, family["family_id"])
    assert path.read_bytes() == before
    assert (tmp_path / "provider-cache" / "usage.json").read_bytes() == usage_before
    assert {item.name: item.read_bytes() for item in (tmp_path / "family-history").glob("*.json")} == original_history
