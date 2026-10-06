"""Sealed cache-only fit gates, derived review corrections, and crash-safe no-refit replay."""

import io
import itertools
import json
import os
import shutil
from copy import deepcopy
from dataclasses import asdict

import pytest
from test_rerank_cohort_features import _trait
from test_rerank_cohort_pair_repair import _answer as repair_answer
from test_rerank_cohort_pair_repair import _previous_fixture

import matching_engine.rerank_cohort_training as module
from matching_engine import rerank_cohort_pair_repair as pair_repair
from matching_engine import rerank_cohort_review as review
from matching_engine.embeddings import HarrierEncoder
from matching_engine.rerank_cohort import _check_sources, _spent_profiles, pilot_cases
from matching_engine.rerank_cohort_features import (
    build_family_features,
    candidate_profile,
)
from matching_engine.rerank_cohort_provider import (
    CohortProviders,
    project_reference_slots,
    source_citations,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import promotion_eligible
from matching_engine.rerank_pair_teacher import salary_comparison
from matching_engine.rerank_protocol import expected_families
from matching_engine.rerank_providers import LIMITS
from matching_engine.rerank_sample_features import SEED_PATH


def _write(path, value, field=None):
    value = deepcopy(value)
    if field:
        value.pop(field, None)
        value[field] = digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace instead of modifying a hardlinked immutable base fixture.
    temporary = path.with_suffix(path.suffix + ".fixture-temp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return value


def _receipt(root, provider, target, job_id, payload, answer, kind):
    identity = provider._identity("/v1/chat/completions", payload, kind)
    key = digest(identity)
    response = {"model": identity["model"], "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(answer, ensure_ascii=False)}}]}
    _write(root / "provider-cache" / f"{key}.json", {"request": identity, "request_hash": key,
           "response": response, "response_hash": digest(response)})
    _write(target / "jobs" / f"{job_id}-0.json", {"request_hash": key, "state": "passed", "kind": kind,
           "answer": answer, "answer_hash": digest(answer)})
    return key


def _pair(root, provider, target, job_id, vacancy, a, b, policy, label=None):
    label = label or ("A" if a["levels"]["python"] > b["levels"]["python"] else "B")
    refs = source_citations(a, b)
    answer = {"label": label, "status_A": a["status"], "status_B": b["status"],
              "evidence_A": [key for key, fact in refs.items() if fact["candidate"] == "A"][:2],
              "evidence_B": [key for key, fact in refs.items() if fact["candidate"] == "B"][:2],
              "reason_code": {"A": "prefer_known_facts", "B": "prefer_known_facts", "tie": "comparable_fit",
                              "insufficient": "insufficient_source_facts"}[label],
              "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"]}
    _receipt(root, provider, target, job_id, provider.pair_payload(vacancy, a, b, policy), answer, "pair")
    return answer


def _grade(root, provider, target, job_id, vacancy, candidate, policy, grade):
    answer = {"grade": grade, "status": candidate["status"], "evidence": list(source_citations(candidate))[:2],
              "reason_code": "assessed" if grade is not None else "insufficient"}
    _receipt(root, provider, target, job_id, provider.suitability_payload(vacancy, candidate, policy), answer, "grade")
    return answer


def _grouped(trait, keys):
    result = {key: value for key, value in trait.items() if key in (
        "grade", "status", "levels", "skill_statuses", "salary_min", "description")}
    for target, value, status in (("duration", "duration_months", "duration_status"),
                                  ("production", "production_support", "production_status"),
                                  ("role", "role", "role_status")):
        result[target] = {"value": trait[value], "status": trait[status]}
    for field, group in (("project_domains", "domain"), ("project_stacks", "stack")):
        declared = {ref["key"]: ref["status"] for ref in trait[field]}
        result[field] = {key: declared.get(key) for key in project_reference_slots(keys, group)}
    return result


@pytest.fixture(scope="module")
def corpus_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("sealed-training-base")
    target, prior = root / module.DEFAULT_COHORT, root / "rerank-grounded-cohort-v7"
    provider = CohortProviders(root / "provider-cache", cohort_id=module.DEFAULT_COHORT)
    original_provider = CohortProviders(root / "provider-cache", cohort_id=prior.name)
    sources, rows, keys = {}, [], ["python", "sql", "teamwork"]
    for allocation in expected_families():
        item = {**allocation, "cohort_id": module.DEFAULT_COHORT, "keys": keys,
                "vacancy": {"class": allocation["direction"], "domens": {"sql": 4}, "stacks": {"python": 5},
                            "soft_skills": {}, "bio": "Разработка SQL и Python", "salary": {"min": 50000, "max": 200000}},
                "policy": {"id": "fixture-python", "ordered_priorities": ["Prefer higher stated Python levels."]},
                "reuse": None, "description_repairs": []}
        if int(allocation["family_id"][-2:]) in (1, 5):
            item["vacancy"]["domens"], item["vacancy"]["stacks"] = {}, {}
        traits = [_trait(index) for index in range(7)]
        for index, trait in enumerate(traits):
            trait["levels"]["python"] = index
            trait["salary_min"] = (50000, 60000, 70000, 80000, 90000, 100000, 120000)[index]
            trait["role"] = f'Fixture role {allocation["family_id"]}/{index}'
            trait["description"] = f'Задача Python {allocation["family_id"]} вариант {index}'
        original_item = {**item, "cohort_id": prior.name, "prior_source": None}
        original = _write(prior / "sources" / f'{allocation["family_id"]}.json',
                          {"manifest": original_item, "traits": traits, "source_receipts": "generation"}, "record_hash")
        payload = original_provider.generation_payload(allocation["direction"], allocation["family_id"], keys)
        key = _receipt(root, original_provider, prior, f'generate-{allocation["family_id"]}', payload,
                       {"profiles": [_grouped(trait, keys) for trait in traits]}, "generation")
        # Orchestrator job answers store canonical traits, unlike the raw grouped response.
        _write(prior / "jobs" / f'generate-{allocation["family_id"]}-0.json',
               {"request_hash": key, "state": "passed", "kind": "generation", "answer": traits, "answer_hash": digest(traits)})
        item["prior_source"] = {"path": f'{prior.name}/sources/{allocation["family_id"]}.json',
                                "record_hash": original["record_hash"]}
        source = _write(target / "sources" / f'{allocation["family_id"]}.json',
                        {"manifest": item, "traits": traits, "source_receipts": item["prior_source"]}, "record_hash")
        sources[allocation["family_id"]] = source
        rows.append(item)
    _write(root / "semantic-cache.json", {})
    _write(target / "semantic-cache.json", {})
    _write(root / "rerank-grounded-cohort-v1/manifest.json", {"ledger_before": {"generation": 0}})
    before = {"generation": 60, "pair": 52, "grade": 0}
    after = {"generation": 60, "pair": 1512, "grade": 430}
    attempts = {"generation": 0, "pair": 1460, "grade": 430}
    bound, accounting = _spent_profiles(root, 60, 0)
    code_names = ("rerank_cohort.py", "rerank_cohort_provider.py", "rerank_cohort_features.py", "rerank_description_repair.py",
                  "rerank_features.py", "combine_scoring.py", "models.py", "embeddings.py", "strict.py",
                  "strict_evidence.py", "vacancy_features.py")
    protected = {str(path.relative_to(root)): digest(path.read_bytes().hex()) for path in prior.rglob("*.json")}
    manifest = _write(target / "manifest.json", {"version": module.DEFAULT_COHORT, "families": rows,
        "profiles": 420, "split_counts": module.SPLITS, "activation_allowed": False, "global_limits": LIMITS,
        "ledger_before": before, "attempt_limits": {"generation": 0, "pair": 3000, "grade": 600},
        "generation_spent_profile_bound": bound, "generation_profile_receipts": accounting,
        "manual_description_variants": 0, "generator": provider.generator_model,
        "seed_hash": digest(SEED_PATH.read_bytes().hex()), "protected_source_hashes": protected,
        "code_hashes": {name: digest((module.Path(module.__file__).parent / name).read_bytes().hex()) for name in code_names}}, "manifest_hash")
    _write(root / "provider-cache/usage.json", after)
    _write(target / "attempt-budget.json", attempts)
    _write(target / "source-isolation.json", _check_sources(list(sources.values())))
    pools = {direction: [candidate_profile(trait, direction) for source in sources.values()
                        if source["manifest"]["direction"] == direction for trait in source["traits"]]
             for direction in {item["direction"] for item in rows}}
    lineages = {direction: {f'{identifier}/{index}': digest(candidate_profile(trait, direction).model_dump(mode="json"))
                           for identifier, source in sources.items() if source["manifest"]["direction"] == direction
                           for index, trait in enumerate(source["traits"])} for direction in pools}
    families = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(HarrierEncoder, "encode", lambda *_: [1.] + [0.] * 639)
        for identifier, source in sources.items():
            item, traits = source["manifest"], source["traits"]
            direction = item["direction"]
            vectors, scores, order, provenance = build_family_features(item, item["vacancy"], traits,
                target / "semantic-cache.json", module.DEFAULT_COHORT, class_profiles=pools[direction],
                class_source_lineage=lineages[direction], semantic_seed={})
            feature = _write(target / "features" / f"{identifier}.json", {"source_hash": source["record_hash"],
                "class_pool_hash": digest(sorted(lineages[direction].values())), "vectors": [asdict(vector) for vector in vectors],
                "main_scores": scores, "profile_order": order, "provenance": provenance}, "hash")
            indices = list(itertools.combinations(range(7), 2))
            pairs = [_pair(root, provider, target, f"{identifier}-pair-{a}-{b}", item["vacancy"], traits[a], traits[b], item["policy"])
                     for a, b in indices]
            swaps = [_pair(root, provider, target, f"{identifier}-pair-{b}-{a}", item["vacancy"], traits[b], traits[a], item["policy"])
                     for a, b in [indices[index] for index in (0, 7, 14)]]
            grades = [_grade(root, provider, target, f"{identifier}-grade-{index}", item["vacancy"], trait, item["policy"], min(index, 3))
                      for index, trait in enumerate(traits)]
            families.append(_write(target / "families" / f"{identifier}.json", {"manifest": item, "traits": traits,
                "vacancy": item["vacancy"], "teacher": provider.teacher_identity(), **feature,
                "pairs": [[a, b, answer["label"]] for (a, b), answer in zip(indices, pairs, strict=True)],
                "pair_answers": pairs, "swap_answers": swaps, "swap_audits": [True] * 3,
                "suitability_answers": grades, "grades": [answer["grade"] for answer in grades], "slice_tags": [], "quarantined": False}, "record_hash"))
    cases, pair_rows, grade_rows = pilot_cases(), [], []
    reverse = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}
    for case in cases:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            label = reverse[case["expected"]] if swapped else case["expected"]
            _pair(root, provider, target, f'pilot-{case["case_id"]}-{int(swapped)}', case["vacancy"], a, b, case["policy"], label)
            pair_rows.append({"case_id": case["case_id"], "reverse": swapped, "label": label, "agrees": True})
        _grade(root, provider, target, f'pilot-grade-{case["case_id"]}', case["vacancy"], case["A"], case["policy"], case["expected_grade"])
        grade_rows.append({"case_id": case["case_id"], "grade": case["expected_grade"], "agrees": True})
    packet = _write(target / "pilot-packet.json", {"version": module.DEFAULT_COHORT, "cases": cases,
                    "reviewer_kind": "assistant", "independent_human_evaluation": False}, "packet_hash")
    pilot = _write(target / "pilot-report.json", {"packet_hash": packet["packet_hash"], "pairs": pair_rows,
                   "grades": grade_rows, "passed": True, "cache_only_revalidated_pilot": None}, "report_hash")
    quality = {"minimum_golden_decisive_pairs": 21, "swap_correct": 180, "swap_total": 180,
               "quarantined_families": [], "passed": True}
    dataset = _write(target / "dataset.json", {"version": module.DEFAULT_COHORT, "manifest_hash": manifest["manifest_hash"],
        "families": families, "teacher": provider.teacher_identity(), "quality": quality, "pilot_hash": pilot["report_hash"],
        "description_audit": None, "training_started": False, "activation_allowed": False}, "dataset_hash")
    _write(target / "report.json", {"dataset_hash": dataset["dataset_hash"], "profiles": 420, "families": 60,
        "split_counts": module.SPLITS, "quality": quality, "ledger_before": before, "ledger_after": after,
        "attempts": attempts, "activation_allowed": False, "training_started": False,
        "golden_model_predictions": 0, "historical_source_bytes_preserved": True}, "report_hash")
    packets = review.prepare_reviews(root, module.DEFAULT_COHORT)
    manual = {"packet_hash": packets["assistant"]["packet_hash"], "reviewer_kind": "assistant", "labels": [
        {"pair_id": case["pair_id"], "label": "B", "reason": "Сравнение заявленных уровней Python по заданному критерию.", "factual_issue": False}
        for case in packets["assistant"]["cases"]]}
    path = target / "reviews/assistant-review.json"
    _write(path, manual)
    review.compare_review(root, path, module.DEFAULT_COHORT)
    return root


@pytest.fixture
def cohort(corpus_root, tmp_path, monkeypatch):
    shutil.copytree(corpus_root, tmp_path / "artifacts", copy_function=os.link)
    root = tmp_path / "artifacts"
    target = root / module.DEFAULT_COHORT
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("Harrier inference during training"))
    monkeypatch.setattr(CohortProviders, "_api_key", lambda *_: pytest.fail("credential read during training"))
    monkeypatch.setattr(CohortProviders, "_open", lambda *_: pytest.fail("HTTP during training"))
    return root, target, target / "reviews/assistant-review.json"


def _run(cohort, **kwargs):
    root, _, manual = cohort
    return module.run_test_only_training(root, assistant_review_file=manual, **kwargs)


def test_real_test_fit_uses_fixed_grid_train_only_normalization_and_never_activates(cohort, monkeypatch):
    root, target, manual = cohort
    calls = []
    actual = module.train

    def fitted(families, schema, metadata):
        calls.append((families, metadata))
        return actual(families, schema, metadata)

    monkeypatch.setattr(module, "train", fitted)
    raw_before = (target / "dataset.json").read_bytes()
    report = _run(cohort)
    assert len(calls) == 1 and len(calls[0][0]) == 60 and sum(len(f.vectors) for f in calls[0][0]) == 420
    assert calls[0][1]["require_protocol"] is False
    assert report["test_only"] and report["protocol_valid"] and not report["activation_allowed"]
    assert report["source_allocation_validated"] and report["derived_pair_corrections"] == 0
    assert report["C_grid"] == [.01, .1, 1., 10.]
    assert set(report["metrics"]) == {"validation", "calibration", "golden", "tail"}
    assert report["metrics"]["calibration"]["golden_families"] == 8
    artifact = json.loads((target / "test-only-training/artifact.json").read_text())
    assert artifact["training"]["test_only"] and not artifact["gate"]["passed"] and not promotion_eligible(artifact)
    training = [vector for family in calls[0][0] if family.split == "train" for vector in family.vectors]
    index = artifact["feature_names"].index("salary_affordability")
    expected_mean = sum(vector.values[index] for vector in training) / len(training)
    assert artifact["means"][index] == pytest.approx(expected_mean)
    assert not (target / "reviews/user-review.json").exists()
    assert (target / "dataset.json").read_bytes() == raw_before
    assert _run(cohort) == report and len(calls) == 1
    assert module.run_test_only_training(root, assistant_review_file=manual) == report
    artifact["coefficients"][0] += .01
    artifact.pop("content_hash")
    _write(target / "test-only-training/artifact.json", module.validate_artifact(artifact))
    with pytest.raises(ValueError, match="replay integrity mismatch"):
        _run(cohort)
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["quality", "feature", "teacher_response", "source", "ledger", "comparison", "semantic"])
def test_tampering_or_failed_gates_never_start_fit(cohort, monkeypatch, mutation):
    root, target, manual = cohort
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("fit occurred before complete gates"))
    if mutation == "quality":
        path = target / "dataset.json"
        value = json.loads(path.read_text())
        value["quality"]["passed"] = False
        _write(path, value, "dataset_hash")
    elif mutation == "feature":
        path = target / "features/backend-01.json"
        value = json.loads(path.read_text())
        value["vectors"][0]["values"][0] += .01
        _write(path, value, "hash")
    elif mutation == "teacher_response":
        job = json.loads((target / "jobs/backend-01-pair-0-1-0.json").read_text())
        path = root / "provider-cache" / f'{job["request_hash"]}.json'
        value = json.loads(path.read_text())
        value["response"]["choices"][0]["message"]["content"] = "{}"
        value["response_hash"] = digest(value["response"])
        _write(path, value)
    elif mutation == "source":
        path = target / "sources/backend-01.json"
        value = json.loads(path.read_text())
        value["traits"][0]["description"] = "Подмена принятого источника"
        _write(path, value, "record_hash")
    elif mutation == "ledger":
        path = root / "provider-cache/usage.json"
        value = json.loads(path.read_text())
        value["pair"] = 3001
        _write(path, value)
    elif mutation == "semantic":
        path = target / "semantic-cache.json"
        value = json.loads(path.read_text())
        first = next(iter(value))
        value[first] = [0., 1.] + [0.] * 638
        _write(path, value)
    else:
        path = target / "reviews" / f'assistant-comparison-{digest(json.loads(manual.read_text()))}.json'
        value = json.loads(path.read_text())
        value["comparisons"][0]["teacher_label"] = "A"
        _write(path, value, "report_hash")
    with pytest.raises((ValueError, KeyError)):
        _run(cohort)
    assert not (target / "test-only-training/train-request.json").exists()


def test_crashed_started_fit_never_automatically_refits(cohort, monkeypatch):
    calls = []

    def failed(*_):
        calls.append(1)
        raise RuntimeError("simulated learner interruption")

    monkeypatch.setattr(module, "train", failed)
    with pytest.raises(RuntimeError, match="learner interruption"):
        _run(cohort)
    with pytest.raises(RuntimeError, match="automatic refit forbidden"):
        _run(cohort)
    assert calls == [1]


def _changed_manual(cohort, count=1):
    root, _, manual_path = cohort
    manual = json.loads(manual_path.read_text())
    for row in manual["labels"][:count]:
        row["label"] = "A"
    _write(manual_path, manual)
    comparison = review.compare_review(root, manual_path, module.DEFAULT_COHORT)
    return comparison, manual


def _adjudication(cohort, comparison, outcome="assistant_label_confirmed", duplicate_reasons=False):
    _, target, _ = cohort
    blocked = [row for row in comparison["comparisons"] if row["category"] != "agreement"]
    value = {"dataset_hash": comparison["dataset_hash"], "packet_hash": comparison["packet_hash"],
             "review_hash": comparison["review_hash"], "labels_changed": False, "resolutions": [
                 {"pair_id": row["pair_id"], "teacher_label": row["teacher_label"], "assistant_label": row["review_label"],
                  "outcome": outcome, "factual_issue_resolved": True,
                  "reason": "Проверены заявленные исходные измерения и порядок критериев " + ("все пары" if duplicate_reasons else row["pair_id"])}
                 for row in blocked]}
    path = target / "reviews/adjudication.json"
    _write(path, value, "adjudication_hash")
    return path


def test_disagreements_require_explicit_adjudication_and_corrections_touch_only_reviewed_pairs(cohort, monkeypatch):
    _, target, manual = cohort
    comparison, _ = _changed_manual(cohort)
    before = {path: path.read_bytes() for path in [target / "dataset.json", *list((target / "families").glob("*.json"))]}
    captured = []
    actual = module.train

    def fitted(families, schema, metadata):
        captured.append(families)
        return actual(families, schema, metadata)

    monkeypatch.setattr(module, "train", fitted)
    with pytest.raises(ValueError, match="explicit adjudication"):
        _run(cohort)
    assert captured == []
    adjudication = _adjudication(cohort, comparison)
    report = _run(cohort, adjudication_file=adjudication)
    assert report["derived_pair_corrections"] == 1 and not report["activation_allowed"]
    corrected = next(row for row in report["pinned"]["assistant_gate"]["corrections"])
    raw = json.loads((target / "dataset.json").read_text())
    changes = []
    for derived, source in zip(captured[0], raw["families"], strict=True):
        assert derived.grades == source["grades"]
        for after, prior in zip(derived.pairs, source["pairs"], strict=True):
            if list(after) != prior:
                changes.append((derived.family_id, *after))
    assert changes == [(corrected["family_id"], corrected["a_index"], corrected["b_index"], "A")]
    assert all(path.read_bytes() == content for path, content in before.items())
    correction = json.loads((target / "test-only-training/corrections.json").read_text())
    assert correction["raw_dataset_unchanged"] and correction["independent_suitability_unchanged"]
    assert not correction["original_reverse_audits_validate_derived_corrected_pairs"]
    assert correction["raw_label_hash"] != correction["derived_label_hash"]
    assert json.loads(manual.read_text())["reviewer_kind"] == "assistant"


@pytest.mark.parametrize("mutation", ["outside_pair", "wrong_labels", "blanket_reason", "old_blanket_shape"])
def test_adjudication_cannot_blanket_approve_or_edit_unreviewed_pairs(cohort, monkeypatch, mutation):
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("fit accepted invalid adjudication"))
    comparison, _ = _changed_manual(cohort, count=2)
    path = _adjudication(cohort, comparison, duplicate_reasons=mutation == "blanket_reason")
    value = json.loads(path.read_text())
    if mutation == "outside_pair":
        value["resolutions"][0]["pair_id"] = "not-a-reviewed-pair"
    elif mutation == "wrong_labels":
        value["resolutions"][0]["teacher_label"] = "A"
    elif mutation == "old_blanket_shape":
        value["resolutions"][0].pop("outcome")
        value["resolutions"][0]["teacher_label_confirmed"] = True
    _write(path, value, "adjudication_hash")
    with pytest.raises(ValueError, match="adjudication"):
        _run(cohort, adjudication_file=path)


@pytest.fixture(scope="module")
def repaired_root(corpus_root, tmp_path_factory):
    root = tmp_path_factory.mktemp("derived-training-base") / "artifacts"
    shutil.copytree(corpus_root, root, copy_function=os.link)
    base = root / module.DEFAULT_COHORT
    dataset = json.loads((base / "dataset.json").read_text())
    family = next(row for row in dataset["families"] if row["manifest"]["family_id"] == "backend-02")
    provider = CohortProviders(root / "provider-cache", cohort_id=module.DEFAULT_COHORT)
    indices = list(itertools.combinations(range(7), 2))
    for position in (1, 2):
        a, b = indices[(0, 7, 14)[position]]
        family["swap_answers"][position] = _pair(root, provider, base, f"backend-02-pair-{b}-{a}",
            family["vacancy"], family["traits"][b], family["traits"][a], family["manifest"]["policy"], "B")
    family["swap_audits"], family["quarantined"] = [True, False, False], True
    pinned = _write(base / "families/backend-02.json", family, "record_hash")
    dataset["families"] = [pinned if row["manifest"]["family_id"] == "backend-02" else row for row in dataset["families"]]
    dataset["quality"].update(swap_correct=178, quarantined_families=["backend-02"], passed=False)
    dataset = _write(base / "dataset.json", dataset, "dataset_hash")
    report = json.loads((base / "report.json").read_text())
    report.update(dataset_hash=dataset["dataset_hash"], quality=dataset["quality"])
    _write(base / "report.json", report, "report_hash")
    _historical_fixture(root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pair_repair.PairRepairProviders, "_api_key", lambda _: "offline-test-key")

        def opened(_, request):
            state = json.loads(json.loads(request.data)["messages"][1]["content"])
            answer = repair_answer(state["vacancy"], state["A"], state["B"], state["policy"])
            response = {"model": "qwen3.8-27b", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(answer, ensure_ascii=False)}}]}
            return io.BytesIO(json.dumps(response).encode())

        patch.setattr(pair_repair.PairRepairProviders, "_open", opened)
        repaired = pair_repair.run_pair_repair(root)
        assert repaired["quality"]["passed"] and len(repaired["selected_families"]) == 17
        assert "backend-02" in repaired["selected_families"]
    target = root / pair_repair.VERSION
    packet = json.loads((target / "reviews/assistant-packet.json").read_text())
    manual = {"packet_hash": packet["packet_hash"], "reviewer_kind": "assistant", "labels": [
        {"pair_id": case["pair_id"], "label": repair_answer(case["vacancy"], case["A"], case["B"], case["policy"])["label"],
         "reason": "Проверены независимые исходные факты по исправленному порядку критериев.", "factual_issue": False}
        for case in packet["cases"]]}
    path = target / "reviews/assistant-review.json"
    _write(path, manual)
    comparison = module.compare_test_only_review(root, path, derived_dataset_file=target / "dataset.json")
    assert comparison["agreements"] == 20
    return root


@pytest.fixture
def repaired(repaired_root, tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    shutil.copytree(repaired_root, root, copy_function=os.link)
    target = root / pair_repair.VERSION
    _fixture_historical_pins(root, monkeypatch)
    # Nomination is pinned to its original reviewed bytes, not to this copied path.
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("Harrier inference during repair fit"))
    for provider in (CohortProviders, pair_repair.PairRepairProviders):
        monkeypatch.setattr(provider, "_api_key", lambda *_: pytest.fail("credential read during repair fit"))
        monkeypatch.setattr(provider, "_open", lambda *_: pytest.fail("HTTP during repair fit"))
    return root, target, target / "reviews/assistant-review.json"


def _run_repaired(repaired, **kwargs):
    root, target, manual = repaired
    return module.run_test_only_training(root, assistant_review_file=manual,
                                        derived_dataset_file=target / "dataset.json", **kwargs)


def test_verified_pair_repair_can_train_after_base_quality_failure_in_own_namespace(repaired, monkeypatch):
    root, target, manual = repaired
    base = root / module.DEFAULT_COHORT
    original = {path: path.read_bytes() for path in [base / "dataset.json", target / "dataset.json",
                root / "provider-cache/usage.json", *list((base / "families").glob("*.json"))]}
    captured = []
    actual = module.train

    def fitted(families, schema, metadata):
        captured.append(families)
        return actual(families, schema, metadata)

    monkeypatch.setattr(module, "train", fitted)
    report = _run_repaired(repaired)
    assert len(captured) == 1 and report["protocol_valid"] and report["test_only"]
    assert not report["raw_teacher_quality"]["passed"] and report["effective_teacher_quality"]["passed"]
    assert report["namespace"] == pair_repair.VERSION and report["pair_repair"]["base_dataset_hash"] != report["pinned"]["dataset_hash"]
    assert not report["activation_allowed"] and report["derived_pair_corrections"] == 0
    provenance = report["label_provenance"]
    assert provenance["validation_entirely_rule_derived"] and provenance["golden_oracle_families"] == 0
    assert provenance["split_counts"]["rule_derived_class_only"] == {"train": 8, "validation": 8, "calibration": 0, "golden": 0}
    assert provenance["oracle_local_receipts"] == 384 and provenance["oracle_remote_calls"] == 0
    assert provenance["oracle_code_hash"] == report["pair_repair"]["oracle_code_hash"]
    quality = report["effective_label_quality"]
    assert quality["swap_total"] == 132 and quality["oracle_swap_total"] == 48
    assert quality["teacher_passed"] and quality["oracle_passed"] and report["effective_quality_has_mixed_label_sources"]
    assert report["label_source_metrics"]["llm_pair_teacher"]["validation"]["golden_families"] == 0
    assert report["label_source_metrics"]["llm_pair_teacher"]["golden"]["golden_families"] == 12
    assert report["label_source_metrics"]["rule_derived_class_only"]["validation"]["golden_families"] == 8
    assert report["concurrent_pair_attempts"]["charged_pair_attempts"] == 52
    assert all(not row["pilot_passed"] for row in report["concurrent_pair_attempts"]["pilots"].values())
    reconciliation = report["pair_repair"]["charge_reconciliation"]
    assert reconciliation["pilot_charges_already_in_base"] == 0
    assert reconciliation["additional_charged_pair_attempts"] == 8 + 24
    semantics = report["policy_evaluation"]
    assert semantics["suitability_policy_original"] and not semantics["independent_suitability_regraded"]
    assert len(semantics["families_with_different_pair_and_suitability_policy"]) == 16
    assert not semantics["policy_scope_metrics_used_for_tuning"]
    assert set(report["policy_scope_metrics"]) == {"class_only_revision", "original_nonclass"}
    for split in ("validation", "calibration", "golden"):
        observed = sum(group[split]["golden_families"] for group in report["policy_scope_metrics"].values())
        assert observed == module.SPLITS[split]
    assert (target / "test-only-training/completed.json").is_file()
    assert not (base / "test-only-training/train-request.json").exists()
    effective = json.loads((target / "dataset.json").read_text())
    for derived, source in zip(captured[0], effective["families"], strict=True):
        assert derived.grades == source["grades"] and derived.pairs == [tuple(pair) for pair in source["pairs"]]
    assert all(path.read_bytes() == value for path, value in original.items())
    assert _run_repaired(repaired) == report and len(captured) == 1
    assert module.compare_test_only_review(root, manual, derived_dataset_file=target / "dataset.json")["agreements"] == 20


@pytest.mark.parametrize("mutation", ["old_review", "failed_quality", "feature", "grade", "raw_pair", "base_pair", "policy", "comparison", "path", "oracle_receipt", "extra_oracle_receipt", "teacher_180", "oracle_gold", "source_metrics"])
def test_derived_repair_requires_exact_provenance_and_new_review_before_fit(repaired, monkeypatch, mutation):
    root, target, manual = repaired
    base = root / module.DEFAULT_COHORT
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("fit before verified derived gates"))
    if mutation == "old_review":
        manual = base / "reviews/assistant-review.json"
    elif mutation in ("failed_quality", "feature", "grade"):
        value = json.loads((target / "dataset.json").read_text())
        if mutation == "failed_quality":
            value["quality"]["passed"] = False
        elif mutation == "feature":
            value["families"][0]["vectors"][0]["values"][0] += .01
        else:
            value["families"][0]["grades"][0] = 3
        _write(target / "dataset.json", value, "dataset_hash")
    elif mutation in ("raw_pair", "base_pair"):
        folder = target if mutation == "raw_pair" else base
        job = json.loads((folder / "jobs/backend-02-pair-0-1-0.json").read_text())
        path = root / "provider-cache" / f'{job["request_hash"]}.json'
        value = json.loads(path.read_text())
        value["response"]["choices"][0]["message"]["content"] = "{}"
        value["response_hash"] = digest(value["response"])
        _write(path, value)
    elif mutation == "policy":
        value = json.loads((target / "reviews/assistant-packet.json").read_text())
        value["cases"][0]["policy"]["id"] = "unreviewed-policy"
        _write(target / "reviews/assistant-packet.json", value, "packet_hash")
    elif mutation == "comparison":
        path = target / "reviews" / f'assistant-comparison-{digest(json.loads(manual.read_text()))}.json'
        value = json.loads(path.read_text())
        value["pair_repair_report_hash"] = "wrong"
        _write(path, value, "report_hash")
    elif mutation in ("oracle_receipt", "extra_oracle_receipt"):
        path = target / "oracle-receipts/backend-01-pair-0-1.json"
        value = json.loads(path.read_text())
        if mutation == "oracle_receipt":
            value["oracle_code_hash"] = "different-rule-source"
            _write(path, value, "record_hash")
        else:
            _write(path.with_name("unadmitted-local-receipt.json"), value, "record_hash")
    elif mutation in ("teacher_180", "oracle_gold", "source_metrics"):
        value = json.loads((target / "dataset.json").read_text())
        if mutation == "teacher_180":
            value["quality"].update(swap_correct=180, swap_total=180)
        elif mutation == "oracle_gold":
            family = next(row for row in value["families"] if row["manifest"]["split"] == "golden")
            family["pair_repair"] = {"label_source": "rule_derived_class_only"}
            family["teacher"] = {"label_source": "rule_derived_class_only", "model": None}
        else:
            value["source_metrics"]["rule_derived_class_only"]["families"] = 17
        value = _write(target / "dataset.json", value, "dataset_hash")
        repaired_report = json.loads((target / "repair-report.json").read_text())
        repaired_report.update(dataset_hash=value["dataset_hash"], quality=value["quality"], source_metrics=value["source_metrics"])
        _write(target / "repair-report.json", repaired_report, "report_hash")
    derived = target / "dataset.json" if mutation != "path" else base / "dataset.json"
    with pytest.raises((ValueError, KeyError)):
        module.run_test_only_training(root, assistant_review_file=manual, derived_dataset_file=derived)
    assert not (target / "test-only-training/train-request.json").exists()


def test_derived_review_adjudication_changes_only_new_reviewed_pair_and_preserves_both_raw_datasets(repaired):
    root, target, path = repaired
    manual = json.loads(path.read_text())
    label = manual["labels"][0]["label"]
    manual["labels"][0]["label"] = "A" if label != "A" else "B"
    _write(path, manual)
    comparison = module.compare_test_only_review(root, path, derived_dataset_file=target / "dataset.json")
    with pytest.raises(ValueError, match="explicit adjudication"):
        _run_repaired(repaired)
    adjudication = _adjudication(repaired, comparison)
    before = {folder: (folder / "dataset.json").read_bytes() for folder in (target, root / module.DEFAULT_COHORT)}
    report = _run_repaired(repaired, adjudication_file=adjudication)
    assert report["derived_pair_corrections"] == 1
    assert all((folder / "dataset.json").read_bytes() == value for folder, value in before.items())
    assert not report["pinned"]["assistant_gate"]["original_reverse_audits_validate_derived_corrected_pairs"]


def test_unregistered_repair_namespace_cannot_become_training_input(cohort, monkeypatch):
    root, _, manual = cohort
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("fit accepted an unregistered namespace"))
    with pytest.raises(ValueError, match="explicitly registered"):
        module.run_test_only_training(root, assistant_review_file=manual,
                                      derived_dataset_file=root / "unregistered-repair" / "dataset.json")


def test_exact_base_charges_deduct_only_verified_cached_v5_pilot_reservations(cohort):
    root, target, _ = cohort
    original = json.loads((target / "pilot-report.json").read_text())
    historical = _write(root / "rerank-grounded-cohort-v5/pilot-report.json", original, "report_hash")
    for path in (target / "jobs").glob("pilot-*.json"):
        job = json.loads(path.read_text())
        raw = json.loads((root / "provider-cache" / f'{job["request_hash"]}.json').read_text())
        request = raw["request"]
        request["cohort_id"] = "rerank-grounded-cohort-v5"
        state = json.loads(request["payload"]["messages"][1]["content"])
        state["request_nonce"]["cohort_id"] = request["cohort_id"]
        request["payload"]["messages"][1]["content"] = json.dumps(state, ensure_ascii=False, sort_keys=True)
        key = digest(request)
        raw["request_hash"] = key
        _write(root / "provider-cache" / f"{key}.json", raw)
        job["request_hash"] = key
        _write(path, job)
    original["cache_only_revalidated_pilot"] = {"cohort_id": "rerank-grounded-cohort-v5", "report_hash": historical["report_hash"]}
    pilot = _write(target / "pilot-report.json", original, "report_hash")
    dataset = json.loads((target / "dataset.json").read_text())
    dataset["pilot_hash"] = pilot["report_hash"]
    verified = module._pilot_gate(root, target, module.DEFAULT_COHORT, dataset)
    attempts = json.loads((target / "attempt-budget.json").read_text())
    charges = module._base_charges(root, target, module.DEFAULT_COHORT, attempts, verified)
    assert charges["reserved"] == attempts
    assert charges["cached_pilot_reservations"] == {"generation": 0, "pair": 20, "grade": 10}
    assert charges["charged"] == {"generation": 0, "pair": 1440, "grade": 420}


@pytest.fixture(scope="module")
def concurrent_root(corpus_root, tmp_path_factory):
    root = tmp_path_factory.mktemp("concurrent-training-base") / "artifacts"
    shutil.copytree(corpus_root, root, copy_function=os.link)
    base = root / module.DEFAULT_COHORT
    _historical_fixture(root)
    assert (base / "dataset.json").is_file()
    return root


def _historical_fixture(root):
    """Replay both failed legacy raw pilots during the base's open accounting window."""
    base = root / module.DEFAULT_COHORT
    base_manifest = json.loads((base / "manifest.json").read_text())
    snapshot = deepcopy(base_manifest["ledger_before"])
    for version, spec in pair_repair.PRIOR_REPAIRS.items():
        _previous_fixture(root, version)
        target = root / version
        manifest = json.loads((target / "pilot-manifest.json").read_text())
        manifest.update(version=version, base_cohort=module.DEFAULT_COHORT,
                        ledger_before_pilot=deepcopy(snapshot), pinned_base_family_audits={})
        manifest = _write(target / "pilot-manifest.json", manifest, "manifest_hash")
        snapshot["pair"] += 20 + spec["retry"]
        report = json.loads((target / "pilot-report.json").read_text())
        report.update(pilot_manifest_hash=manifest["manifest_hash"], generation_calls=0, grade_calls=0,
                      activation_allowed=False, ledger_after_pilot=deepcopy(snapshot))
        report = _write(target / "pilot-report.json", report, "report_hash")
        closure = json.loads((target / "closure-observation.json").read_text())
        closure.update(pilot_manifest_hash=manifest["manifest_hash"], pilot_report_hash=report["report_hash"],
                       global_ledger=deepcopy(snapshot), base_cohort=module.DEFAULT_COHORT,
                       base_completed_families=38, observer_kind="assistant", observed_at="2026-10-06T10:00:00+00:00",
                       base_attempt_budget={"generation": 0, "pair": 1, "grade": 0})
        _write(target / "closure-observation.json", closure, "observation_hash")
    ledger = json.loads((root / "provider-cache/usage.json").read_text())
    ledger["pair"] += 52
    _write(root / "provider-cache/usage.json", ledger)
    report = json.loads((base / "report.json").read_text())
    report["ledger_after"] = ledger
    _write(base / "report.json", report, "report_hash")


def _fixture_historical_pins(root, monkeypatch):
    """Use fixture observation seals while retaining checkpoint code/prompt identities."""
    pins = deepcopy(module.HISTORICAL_PILOT_PINS)
    for version in pins:
        pins[version]["observation_hash"] = json.loads((root / version / "closure-observation.json").read_text())["observation_hash"]
    monkeypatch.setattr(module, "HISTORICAL_PILOT_PINS", pins)


@pytest.fixture
def concurrent(concurrent_root, tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    shutil.copytree(concurrent_root, root, copy_function=os.link)
    target = root / module.DEFAULT_COHORT
    _fixture_historical_pins(root, monkeypatch)
    for provider in (CohortProviders, pair_repair.PairRepairProviders):
        monkeypatch.setattr(provider, "_api_key", lambda *_: pytest.fail("credential read during exact reconciliation"))
        monkeypatch.setattr(provider, "_open", lambda *_: pytest.fail("HTTP during exact reconciliation"))
    monkeypatch.setattr(HarrierEncoder, "encode", lambda *_: pytest.fail("Harrier during exact reconciliation"))
    return root, target, target / "reviews/assistant-review.json"


def test_concurrent_pilot_charges_are_exactly_reconciled_without_admitting_its_labels(concurrent):
    root, target, _ = concurrent
    manifest, sources = review._sources(root, module.DEFAULT_COHORT)
    accounting = module._concurrent_pilots(root, manifest, sources, manifest["ledger_before"],
        json.loads((target / "report.json").read_text())["ledger_after"],
        json.loads((target / "attempt-budget.json").read_text()), None)
    assert accounting["charged_pair_attempts"] == 52 and len(accounting["pilots"]) == 2
    for pilot in accounting["pilots"].values():
        assert not pilot["pilot_passed"] and pilot["accounting_only_no_label_admission"]
        assert not pilot["remote_timestamp_attestation"] and pilot["local_operator_observation"]
    with pytest.raises(ValueError, match="verified passed current hybrid repair"):
        _run(concurrent)


@pytest.mark.parametrize("mutation", ["extra_charge", "started", "observation", "budget", "retry_nonce"])
def test_concurrent_allowance_rejects_unaccounted_or_unproven_reservations(concurrent, monkeypatch, mutation):
    root, _, _ = concurrent
    target = root / "rerank-grounded-pair-repair-v2"
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("fit accepted unverified concurrent allowance"))
    if mutation == "extra_charge":
        path = root / "provider-cache/usage.json"
        value = json.loads(path.read_text())
        value["pair"] += 1
        _write(path, value)
        report_path = root / module.DEFAULT_COHORT / "report.json"
        report = json.loads(report_path.read_text())
        report["ledger_after"] = value
        _write(report_path, report, "report_hash")
    elif mutation == "observation":
        path = target / "closure-observation.json"
        value = json.loads(path.read_text())
        value["base_dataset_existed"] = True
        _write(path, value, "observation_hash")
    elif mutation == "budget":
        path = target / "budget.json"
        value = json.loads(path.read_text())
        value["family_initial"] = 1
        _write(path, value)
    else:
        path = next((target / "jobs").glob("*.json"))
        value = json.loads(path.read_text())
        if mutation == "started":
            value["state"] = "started"
            _write(path, value)
        else:
            receipt_path = root / "provider-cache" / f'{value["request_hash"]}.json'
            raw = json.loads(receipt_path.read_text())
            state = json.loads(raw["request"]["payload"]["messages"][1]["content"])
            state["request_nonce"]["attempt"] = 1
            raw["request"]["payload"]["messages"][1]["content"] = json.dumps(state, ensure_ascii=False, sort_keys=True)
            key = digest(raw["request"])
            raw["request_hash"] = key
            _write(root / "provider-cache" / f"{key}.json", raw)
            value["request_hash"] = key
            _write(path, value)
    with pytest.raises((ValueError, KeyError)):
        _run(concurrent)


@pytest.mark.parametrize("previous_namespace", ["rerank-grounded-pair-repair-v1", "rerank-grounded-pair-repair-v2"])
def test_metadata_rebind_preserves_exact_decisions_and_policy_context_without_claiming_new_review(repaired, previous_namespace):
    root, target, manual_path = repaired
    packet = json.loads((target / "reviews/assistant-packet.json").read_text())
    manual = json.loads(manual_path.read_text())
    old_packet = deepcopy(packet)
    old_packet["pair_repair_pilot_manifest_hash"] = "previous-pilot-manifest"
    old = root / previous_namespace / "reviews"
    old_packet = _write(old / "assistant-packet.json", old_packet, "packet_hash")
    old_manual = {**manual, "packet_hash": old_packet["packet_hash"]}
    _write(old / "assistant-review.json", old_manual)
    value = {"version": "blind-review-metadata-rebind-v1", "old_packet_hash": old_packet["packet_hash"],
             "new_packet_hash": packet["packet_hash"], "old_pilot_manifest_hash": old_packet["pair_repair_pilot_manifest_hash"],
             "new_pilot_manifest_hash": packet["pair_repair_pilot_manifest_hash"], "old_review_hash": digest(old_manual),
             "new_review_hash": digest(manual), "cases_hash": digest(packet["cases"]), "source_hashes": packet["source_hashes"],
             "policy_revision": packet["policy_revision"], "labels_hash": digest(manual["labels"]), "label_changes": 0,
             "cases_equal": True, "new_blind_review": False, "claimed_human_review": False}
    receipt = _write(target / "reviews/review-metadata-rebind-receipt.json", value, "receipt_hash")
    assert module._review_metadata_rebind(root, target / "reviews", packet, manual) == receipt
    changed = deepcopy(manual)
    changed["labels"][0]["label"] = "B" if manual["labels"][0]["label"] == "A" else "A"
    value.update(new_review_hash=digest(changed), labels_hash=digest(changed["labels"]))
    _write(target / "reviews/review-metadata-rebind-receipt.json", value, "receipt_hash")
    with pytest.raises(ValueError, match="changed decisions"):
        module._review_metadata_rebind(root, target / "reviews", packet, changed)


@pytest.mark.parametrize("state", ["failed", "incomplete"])
def test_current_failed_or_incomplete_v3_pilot_blocks_even_base_fit(cohort, monkeypatch, state):
    root, base, _ = cohort
    target = root / pair_repair.VERSION
    _write(target / "pilot-manifest.json", {"version": pair_repair.VERSION}, "manifest_hash")
    if state == "failed":
        _write(target / "pilot-report.json", {"passed": False, "valid": 8, "correct": 7, "swap_correct": 3}, "report_hash")
    monkeypatch.setattr(module, "train", lambda *_: pytest.fail("base fit bypassed failed current pilot"))
    with pytest.raises(ValueError, match="current pair-repair pilot has not passed"):
        _run(cohort)
    assert not (base / "test-only-training/train-request.json").exists()


def test_historical_failed_v1_pilot_remains_accounting_only(cohort, monkeypatch):
    root, target, _ = cohort
    ledger = json.loads((root / "provider-cache/usage.json").read_text())
    ledger["pair"] += 24
    _write(root / "provider-cache/usage.json", ledger)
    base_report = json.loads((target / "report.json").read_text())
    base_report["ledger_after"] = ledger
    _write(target / "report.json", base_report, "report_hash")
    # Isolate the admission boundary; raw concurrent-receipt validation has its own tests.
    accounting = {"charged_pair_attempts": 24, "pilots": {
        "rerank-grounded-pair-repair-v1": {"pilot_passed": False,
            "accounting_only_no_label_admission": True}}}
    monkeypatch.setattr(module, "_concurrent_pilots", lambda *_: deepcopy(accounting))
    report = _run(cohort)
    assert report["pinned"]["concurrent_pair_attempts"] == accounting
    assert report["test_only"] and not report["activation_allowed"]
    assert report["pinned"]["pair_repair"] is None
