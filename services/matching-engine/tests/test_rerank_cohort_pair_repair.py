"""Offline pair-only policy revision, immutable selection and raw-receipt replay."""

import io
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from matching_engine import rerank_cohort_pair_repair as repair
from matching_engine.rerank_cohort_provider import source_citations
from matching_engine.rerank_cohort_review import prepare_reviews
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import salary_comparison
from matching_engine.rerank_protocol import expected_families

BASE_LEDGER_PAIR = 2292


def _write(path, value, field=None):
    value = deepcopy(value)
    if field:
        value.pop(field, None)
        value[field] = digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def _answer(vacancy, a, b, policy):
    if repair._class_only(vacancy):
        label = repair.class_only_label(a, b, policy)
    elif repair._exclusive_missing(policy, a, b):
        label = "insufficient"
    else:
        label = "tie" if a["levels"]["python"] == b["levels"]["python"] else (
            "A" if a["levels"]["python"] > b["levels"]["python"] else "B")
    citations = source_citations(a, b)
    refs = {side: [token for token, fact in citations.items() if fact["candidate"] == side
                  and fact["field"] in ("status", "grade")] for side in ("A", "B")}
    return {"label": label, "reason_code": {"A": "prefer_known_facts", "B": "prefer_known_facts",
            "tie": "comparable_fit", "insufficient": "insufficient_source_facts"}[label],
            "status_A": a["status"], "status_B": b["status"], "evidence_A": refs["A"], "evidence_B": refs["B"],
            "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"]}


def _previous_fixture(directory):
    target = directory / repair.PREVIOUS_VERSION
    provider = repair.CohortProviders(directory / "provider-cache", cohort_id=repair.PREVIOUS_VERSION)
    cases, requests, rows = repair.pilot_cases(), [], []
    for case in cases:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            invalid = case["case_id"] in ("class-2", "essential-unknown")
            answer = _answer(case["vacancy"], a, b, case["policy"])
            if invalid:
                answer.update(label=("A" if swapped else "B") if case["case_id"] == "class-2" else (
                    "B" if swapped else "A"), reason_code="prefer_known_facts")
            for attempt in ((0, 1) if invalid else (0,)):
                payload = provider.pair_payload(case["vacancy"], a, b, case["policy"], attempt)
                state = json.loads(payload["messages"][1]["content"])
                state["request_nonce"]["cohort_id"] = repair.PREVIOUS_VERSION
                payload["messages"][1]["content"] = json.dumps(state, ensure_ascii=False, sort_keys=True)
                identity = provider._identity("/v1/chat/completions", payload, "pair")
                identity.update(cohort_id=repair.PREVIOUS_VERSION,
                    prompt_version="rerank-uniform-cohort-pair-v3" + ("-repair1" if attempt else ""))
                if attempt == 0:
                    requests.append(identity)
                key = digest(identity)
                response = {"model": "qwen3.8-27b", "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(answer)}}]}
                _write(directory / "provider-cache" / f"{key}.json", {"request": identity, "request_hash": key,
                    "response": response, "response_hash": digest(response)})
                job = {"state": "failed" if invalid else "passed", "request_hash": key, "kind": "pair",
                    "category": "pilot", "attempt": attempt}
                if not invalid:
                    job.update(answer=answer, answer_hash=digest(answer))
                _write(target / "jobs" / f"pilot-{case['case_id']}-{int(swapped)}-{attempt}.json", job)
            rows.append({"case_id": case["case_id"], "swapped": swapped, "answer": None if invalid else answer,
                         "expected": repair._REVERSE[case["expected"]] if swapped else case["expected"],
                         "error": "pair repair response invalid after one bounded repair" if invalid else None})
    sources = {path.stem: _read_json(path)["record_hash"]
               for path in (directory / repair.BASE_COHORT / "sources").glob("*.json")}
    manifest = _write(target / "pilot-manifest.json", {"cases": cases, "requests": requests,
        "code_hash": repair.PREVIOUS_CODE_HASH, "source_hashes": sources,
        "base_manifest_hash": _read_json(directory / repair.BASE_COHORT / "manifest.json")["manifest_hash"]}, "manifest_hash")
    report = _write(target / "pilot-report.json", {"pilot_manifest_hash": manifest["manifest_hash"], "rows": rows,
        "valid": 16, "correct": 16, "swap_correct": 8, "passed": False}, "report_hash")
    _write(target / "budget.json", {"pilot_initial": 20, "pilot_retry": 4, "family_initial": 0, "family_retry": 0})
    jobs = {path.name: _read_json(path) for path in sorted((target / "jobs").glob("*.json"))}
    raw = {job["request_hash"]: _read_json(directory / "provider-cache" / f"{job['request_hash']}.json")["response_hash"]
           for job in jobs.values()}
    _write(target / "closure-observation.json", {"cohort_id": repair.PREVIOUS_VERSION,
        "pilot_manifest_hash": manifest["manifest_hash"], "pilot_report_hash": report["report_hash"],
        "pilot_passed": False, "job_receipts_hash": digest(jobs), "raw_response_hashes": raw,
        "actual_pair_reservations": 24, "base_dataset_existed": False,
        "base_manifest_hash": manifest["base_manifest_hash"], "global_ledger": _read_json(directory / "provider-cache/usage.json")},
        "observation_hash")


@pytest.fixture
def directory(tmp_path):
    target = tmp_path / repair.BASE_COHORT
    rows, families = [], []
    for family_index, item in enumerate(expected_families()):
        is_class = int(item["family_id"][-2:]) in (1, 5)
        vacancy = {"class": item["direction"], "domens": {}, "stacks": {} if is_class else {"python": 4},
                   "soft_skills": {}, "bio": None, "salary": {"min": 50000, "max": 200000}}
        policy = {"id": "old-coverage", "ordered_priorities": ["Prefer coverage of explicit requirements."],
                  "exclusive_criterion": False, "required_measurements": []}
        row = {**item, "cohort_id": repair.BASE_COHORT, "vacancy": vacancy, "policy": policy}
        traits = [repair._candidate(grade=("intern", "junior", "middle", "senior")[index % 4], level=index)
                  for index in range(7)]
        for index, candidate in enumerate(traits):
            candidate["description"] = f"Семья {item['family_id']}, задача {index}."
            candidate["salary_min"] += family_index * 100 + index
        source = _write(target / "sources" / f"{item['family_id']}.json", {
            "manifest": row, "traits": traits, "source_receipts": "offline-fixture"}, "record_hash")
        pairs = [[a, b, "A"] for a, b in itertools.combinations(range(7), 2)]
        family = {"manifest": row, "vacancy": vacancy, "source_hash": source["record_hash"], "traits": traits,
                  "teacher": {"model": "qwen3.8-27b", "prompt_version": "rerank-uniform-cohort-pair-v2"},
                  "pairs": pairs, "pair_answers": [{"label": "A"}] * 21,
                  "swap_answers": [{"label": "B"}] * 3, "swap_audits": [True] * 3, "quarantined": False,
                  "grades": [3] * 7, "suitability_answers": [{"grade": 3}] * 7,
                  "vectors": [{"values": [float(index)], "names": ["fixture"]} for index in range(7)],
                  "main_scores": list(range(7)), "profile_order": list(range(7)), "provenance": {"fixture": True},
                  "class_pool_hash": "fixture-pool", "cyclic_components": [], "scalar_order_ceiling": {}}
        if item["family_id"] == "frontend-01":
            family.update(swap_audits=[True, False, False], quarantined=True)
        families.append(_write(target / "families" / f"{item['family_id']}.json", family, "record_hash"))
        rows.append(row)
    manifest = _write(target / "manifest.json", {"version": repair.BASE_COHORT, "families": rows}, "manifest_hash")
    dataset = _write(target / "dataset.json", {"version": repair.BASE_COHORT, "manifest_hash": manifest["manifest_hash"],
        "families": families, "teacher": families[0]["teacher"], "quality": {"passed": False},
        "activation_allowed": False, "training_started": False}, "dataset_hash")
    ledger = {"generation": 105, "pair": BASE_LEDGER_PAIR, "grade": 579}
    _write(tmp_path / "provider-cache/usage.json", ledger)
    _write(target / "report.json", {"dataset_hash": dataset["dataset_hash"], "ledger_after": ledger}, "report_hash")
    _write(target / "attempt-budget.json", {"generation": 0, "pair": 1440, "grade": 420})
    packets = prepare_reviews(tmp_path, repair.BASE_COHORT)
    _write(target / "reviews/assistant-review.json", {"packet_hash": packets["assistant"]["packet_hash"],
        "reviewer_kind": "assistant", "labels": [{"pair_id": case["pair_id"], "label": "A",
        "reason": "Offline original blind review.", "factual_issue": False} for case in packets["assistant"]["cases"]]})
    _previous_fixture(tmp_path)
    return tmp_path


@pytest.fixture
def remote(monkeypatch):
    calls = []
    monkeypatch.setattr(repair.PairRepairProviders, "_api_key", lambda _self: "offline-test-key")

    def opened(_self, request):
        payload = json.loads(request.data)
        state = json.loads(payload["messages"][1]["content"])
        calls.append(state)
        answer = _answer(state["vacancy"], state["A"], state["B"], state["policy"])
        response = {"model": "qwen3.8-27b", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(answer, ensure_ascii=False)}}]}
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(repair.PairRepairProviders, "_open", opened)
    return calls


def test_class_only_comparator_skips_missing_and_uses_own_status_not_parent():
    policy = repair.CLASS_ONLY_POLICY
    assert repair.class_only_label(repair._candidate("senior", "rejected"), repair._candidate("middle", "confirmed"), policy) == "A"
    unknown = repair._candidate()
    known = repair._candidate(duration_months=120, duration_status="confirmed")
    assert repair.class_only_label(unknown, known, policy) == "tie"
    rejected = repair._candidate(production_support=True, production_status="rejected")
    weak = repair._candidate(production_support=False, production_status="pending")
    assert repair.class_only_label(rejected, weak, policy) == "tie"
    strong = repair._candidate(status="pending", production_support=False, production_status="confirmed")
    assert repair.class_only_label(strong, weak, policy) == "A"
    actual = repair._candidate(production_support=True, production_status="pending")
    assert repair.class_only_label(actual, strong, policy) == "A"
    exclusive = {**policy, "exclusive_criterion": True, "required_measurements": ["uptime_percentage"]}
    assert repair.class_only_label(repair._candidate("senior"), weak, exclusive) == "insufficient"


def test_pair_only_identity_and_ten_preregistered_fixtures(tmp_path):
    provider = repair.PairRepairProviders(tmp_path)
    cases = repair.pilot_cases()
    assert len(cases) == 10 and len([case for case in cases if case["case_id"].startswith("class-")]) == 5
    assert len({case["vacancy"]["class"] for case in cases}) == 8
    for case in cases:
        if repair._class_only(case["vacancy"]):
            assert repair.class_only_label(case["A"], case["B"], case["policy"]) == case["expected"]
        payload = provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"])
        assert provider._identity("/v1/chat/completions", payload, "pair")["prompt_version"] == repair.PAIR_PROMPT_VERSION
        assert "BEFORE confirmation" in payload["messages"][0]["content"]
        assert "uniqueItems" not in json.dumps(payload["response_format"])
        for attempt in (0, 1):
            request = provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
            prompt = request["messages"][0]["content"]
            assert prompt.startswith(repair._DECISION_CONTRACT)
            assert prompt.index("STEP 0") < prompt.index("STEP 1") < prompt.index("STEP 2") < prompt.index("STEP 3")
            state = json.loads(request["messages"][1]["content"])
            assert "expected" not in state and "label" not in state["criterion_applicability"]
            if case["case_id"] == "class-2":
                assert not state["criterion_applicability"]["production_comparison_applicable"]
                assert not state["criterion_applicability"]["duration_comparison_applicable"]
                assert "do not reward the other side for being known or confirmed" in prompt
            if case["case_id"] == "essential-unknown":
                assert state["criterion_applicability"]["required_measurements"] == [
                    {"path": "uptime_percentage", "available_A": False, "available_B": False}]
                assert "Grade, confirmation, production, duration, salary" in prompt
    assert provider.teacher_identity()["prompt_version"] == repair.PAIR_PROMPT_VERSION
    assert provider.teacher_identity(attempt=1)["prompt_version"].endswith("-repair1")
    for kind in ("grade", "generation"):
        with pytest.raises(ValueError, match="forbids"):
            provider._endpoint(kind)
    with pytest.raises(ValueError, match="forbids"):
        provider.suitability_payload({}, {}, {})
    with pytest.raises(ValueError, match="forbids"):
        provider.generation_payload("backend", "x", ["python"])


def test_pilot_freezes_sources_and_publishes_policy_corrected_blind_packets(directory, remote):
    base = directory / repair.BASE_COHORT
    before = {path: path.read_bytes() for path in base.rglob("*.json")}
    previous_bytes = {path: path.read_bytes() for path in (directory / repair.PREVIOUS_VERSION).rglob("*.json")}
    manifest = repair.prepare_pilot(directory)
    assert not remote and manifest["maximum_attempts"] == 672 and len(manifest["requests"]) == 20
    assert manifest["previous_repair"]["attempts"] == 24 and manifest["previous_repair"]["source_commit"] == repair.PREVIOUS_COMMIT
    output = repair.run_pair_repair(directory, pilot_only=True)
    assert output["state"] == "pilot_passed"
    assert output["pilot"]["valid"] == output["pilot"]["correct"] == 20 and output["pilot"]["swap_correct"] == 10
    assert len(remote) == 20
    assert not (directory / repair.VERSION / "manifest.json").exists()
    for reviewer in ("assistant", "user"):
        old = json.loads((base / "reviews" / f"{reviewer}-packet.json").read_text())
        new = json.loads((directory / repair.VERSION / "reviews" / f"{reviewer}-packet.json").read_text())
        assert new["base_packet_hash"] == old["packet_hash"] and new["packet_hash"] != old["packet_hash"]
        assert [case["pair_id"] for case in new["cases"]] == [case["pair_id"] for case in old["cases"]]
        assert new["source_hashes"] == old["source_hashes"]
        for old_case, new_case in zip(old["cases"], new["cases"], strict=True):
            assert {key: value for key, value in old_case.items() if key != "policy"} == {
                key: value for key, value in new_case.items() if key != "policy"}
            assert new_case["policy"] == repair.revised_policy(old_case["vacancy"], old_case["policy"])
    assert all(path.read_bytes() == value for path, value in before.items())
    assert all(path.read_bytes() == value for path, value in previous_bytes.items())


def test_completed_whole_family_repair_preserves_sources_features_grades_and_replays(directory, remote, monkeypatch):
    base = repair._verified(directory / repair.BASE_COHORT / "dataset.json", "dataset_hash")
    report = repair.run_pair_repair(directory)
    assert report["state"] == "repaired_quality_passed" and len(report["selected_families"]) == 16
    assert report["quality"]["swap_correct"] == report["quality"]["swap_total"] == 180
    assert report["quality"]["golden_families"] == 12 and report["quality"]["minimum_golden_decisive_pairs"] >= 6
    assert report["budget"] == {"pilot_initial": 20, "pilot_retry": 0, "family_initial": 384, "family_retry": 0}
    assert len(remote) == 404
    monkeypatch.setattr(repair.PairRepairProviders, "_open", lambda *_args: pytest.fail("HTTP during completed replay"))
    monkeypatch.setattr(repair.PairRepairProviders, "_api_key", lambda *_args: pytest.fail("key during completed replay"))
    derived, verified = repair.validate_pair_repair(directory)
    assert verified == report and repair.run_pair_repair(directory) == report
    assert repair.verify_repair_dataset(directory / repair.VERSION) == derived
    assert repair._verified(directory / repair.BASE_COHORT / "dataset.json", "dataset_hash") == base
    for old, new in zip(base["families"], derived["families"], strict=True):
        for field in ("manifest", "traits", "source_hash", "vectors", "main_scores", "profile_order", "provenance", "grades", "suitability_answers"):
            assert old[field] == new[field]
        if old["manifest"]["family_id"] not in report["selected_families"]:
            assert old == new


def test_remaining_base_calls_and_global_budget_are_reserved_before_pilot(directory):
    manifest = repair.prepare_pilot(directory)
    assert manifest["projected_base_final_pair"] + manifest["projected_prior_pair_attempts"] + manifest["maximum_attempts"] == 2964
    _write(directory / "provider-cache/usage.json", {"generation": 105, "pair": 2329, "grade": 579})
    with pytest.raises(ValueError, match="remaining base calls"):
        repair._budget_check(directory, 0)
    # A completed family reserves no additional ordinary HTTP; an unfinished one reserves all 24.
    (directory / repair.BASE_COHORT / "families/backend-01.json").unlink()
    assert repair._base_remaining(directory) == 24


@pytest.mark.parametrize("mutation", ["budget", "raw", "code", "unknown_namespace", "extra_job"])
def test_prior_24_attempts_and_raw_receipts_are_audited_before_any_v2_http(directory, remote, mutation):
    target = directory / repair.PREVIOUS_VERSION
    if mutation == "budget":
        path = target / "budget.json"
        budget = _read_json(path)
        budget["pilot_retry"] += 1
        _write(path, budget)
    elif mutation == "raw":
        job = _read_json(target / "jobs/pilot-class-0-0-0.json")
        path = directory / "provider-cache" / f"{job['request_hash']}.json"
        receipt = _read_json(path)
        receipt["response"]["choices"][0]["finish_reason"] = "length"
        receipt["response_hash"] = digest(receipt["response"])
        _write(path, receipt)
    elif mutation == "code":
        path = target / "pilot-manifest.json"
        manifest = _read_json(path)
        manifest["code_hash"] = "changed"
        _write(path, manifest, "manifest_hash")
    elif mutation == "unknown_namespace":
        (directory / "rerank-grounded-pair-repair-unknown").mkdir()
    else:
        _write(target / "jobs/unknown-0.json", {})
    with pytest.raises(ValueError):
        repair.run_pair_repair(directory, pilot_only=True)
    assert not remote and not (directory / repair.VERSION / "pilot-manifest.json").exists()


def test_started_without_raw_receipt_never_resends_and_one_retry_has_distinct_nonce(directory, remote):
    provider = repair.PairRepairProviders(directory / "provider-cache")
    case = repair.pilot_cases()[0]
    payload = provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"])
    key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
    path = directory / repair.VERSION / "jobs/test-0.json"
    _write(path, {"state": "started", "request_hash": key})
    with pytest.raises(ValueError, match="ambiguous started"):
        repair._job(directory, provider, "test", case["vacancy"], case["A"], case["B"], case["policy"], "pilot")
    assert not remote
    _write(path, {"state": "failed", "request_hash": key})
    answer = repair._job(directory, provider, "test", case["vacancy"], case["A"], case["B"], case["policy"], "pilot")
    assert answer["label"] == "B" and len(remote) == 1
    assert remote[0]["request_nonce"]["attempt"] == 1
    assert _read_json(directory / repair.VERSION / "budget.json")["pilot_retry"] == 1


def _read_json(path):
    return json.loads(path.read_text())


def test_blind_disagreement_nominates_whole_nonclass_family_without_leaking_targets(directory, remote):
    path = directory / repair.BASE_COHORT / "reviews/assistant-review.json"
    manual = _read_json(path)
    packet = _read_json(path.with_name("assistant-packet.json"))
    case = next(case for case in packet["cases"] if not repair._class_only(case["vacancy"]))
    next(row for row in manual["labels"] if row["pair_id"] == case["pair_id"])["label"] = "B"
    _write(path, manual)
    report = repair.run_pair_repair(directory, original_review_file=path)
    selection = repair._verified(directory / repair.VERSION / "manifest.json", "manifest_hash")
    nominated = next(row for row in selection["selected"] if row["family_id"] == case["family_id"])
    assert nominated["reasons"] == ["original_blind_disagreement"]
    assert nominated["policy_scope"] == "original_nonclass"
    assert nominated["pair_policy"] == case["policy"]
    assert nominated["old_policy_hash"] == nominated["new_policy_hash"]
    assert len(report["selected_families"]) == 17 and len(remote) == 428
    assert all("review_label" not in json.dumps(state) and "nomination" not in json.dumps(state) for state in remote)
    assert report["quality"]["by_policy_scope"]["original_nonclass"]["families"] == 44
    assert report["post_repair_blind_review"]["included_in_reverse_quality"] is False
    repair.validate_pair_repair(directory)
    next(row for row in manual["labels"] if row["pair_id"] == case["pair_id"])["label"] = "A"
    _write(path, manual)
    with pytest.raises(ValueError, match="predicate"):
        repair.validate_pair_repair(directory)


@pytest.mark.parametrize("mutation", ["packet", "duplicate", "missing"])
def test_original_blind_nomination_requires_hash_bound_exact_twenty(directory, mutation):
    path = directory / repair.BASE_COHORT / "reviews/assistant-review.json"
    manual = _read_json(path)
    if mutation == "packet":
        manual["packet_hash"] = "changed"
    elif mutation == "duplicate":
        manual["labels"][-1] = manual["labels"][0]
    else:
        manual["labels"].pop()
    _write(path, manual)
    dataset = repair._verified(directory / repair.BASE_COHORT / "dataset.json", "dataset_hash")
    with pytest.raises(ValueError, match="manual|review"):
        repair._blind_nomination(directory, dataset)


def test_union_capacity_blocks_before_family_http_without_truncating_blind_nominees(directory, remote):
    pilot = repair.prepare_pilot(directory)
    dataset = repair._base_dataset(directory, pilot)
    packet = _read_json(directory / repair.BASE_COHORT / "reviews/assistant-packet.json")
    case = next(case for case in packet["cases"] if not repair._class_only(case["vacancy"]))
    others = [family for family in dataset["families"] if not repair._class_only(family["vacancy"])
              and family["manifest"]["family_id"] != case["family_id"]]
    for family in others[:9]:
        family["quarantined"] = True
    path = directory / repair.BASE_COHORT / "reviews/assistant-review.json"
    manual = _read_json(path)
    next(row for row in manual["labels"] if row["pair_id"] == case["pair_id"])["label"] = "B"
    _write(path, manual)
    with pytest.raises(ValueError, match="plus 9"):
        repair._selection(directory, dataset, pilot)
    blocked = repair._verified(directory / repair.VERSION / "selection-blocked-report.json", "report_hash")
    assert len(blocked["selected_nonclass"]) == 10 and case["family_id"] in blocked["selected_nonclass"]
    assert not remote and not (directory / repair.VERSION / "manifest.json").exists()


def test_transport_timeout_counts_attempt_but_never_resends_ambiguously(directory, monkeypatch):
    calls = []
    monkeypatch.setattr(repair.PairRepairProviders, "_api_key", lambda _self: "offline-test-key")

    def timed_out(_self, _request):
        calls.append(True)
        raise TimeoutError("offline transport fixture")

    monkeypatch.setattr(repair.PairRepairProviders, "_open", timed_out)
    provider = repair.PairRepairProviders(directory / "provider-cache")
    case = repair.pilot_cases()[0]
    for _repeat in range(2):
        with pytest.raises(ValueError, match="ambiguous started"):
            repair._job(directory, provider, "transport", case["vacancy"], case["A"], case["B"], case["policy"], "pilot")
    assert len(calls) == 1 and _read_json(directory / "provider-cache/usage.json")["pair"] == BASE_LEDGER_PAIR + 1
    assert not (directory / repair.VERSION / "jobs/transport-1.json").exists()


def test_pilot_raw_response_replayed_before_any_family_http(directory, remote):
    repair.run_pair_repair(directory, pilot_only=True)
    job = _read_json(directory / repair.VERSION / "jobs/pilot-class-0-0-0.json")
    cache_path = directory / "provider-cache" / f"{job['request_hash']}.json"
    receipt = _read_json(cache_path)
    receipt["response"]["choices"][0]["finish_reason"] = "length"
    receipt["response_hash"] = digest(receipt["response"])
    _write(cache_path, receipt)
    with pytest.raises(ValueError):
        repair.run_pair_repair(directory)
    assert len(remote) == 20 and not (directory / repair.VERSION / "manifest.json").exists()


def test_pilot_closure_counter_cannot_precede_its_terminal_raw_reservations(directory, remote):
    repair.run_pair_repair(directory, pilot_only=True)
    target = directory / repair.VERSION
    manifest = _read_json(target / "pilot-manifest.json")
    report = _read_json(target / "pilot-report.json")
    observation = _read_json(target / "closure-observation.json")
    assert observation["actual_pair_reservations"] == 20
    assert observation["global_ledger"] == report["ledger_after_pilot"]
    assert report["ledger_after_pilot"]["pair"] == manifest["ledger_before_pilot"]["pair"] + 20
    # Even consistently rehashed metadata cannot pretend that all 20 calls ended at the nineteenth counter.
    report["ledger_after_pilot"]["pair"] -= 1
    report = _write(target / "pilot-report.json", report, "report_hash")
    observation.update(global_ledger=report["ledger_after_pilot"], pilot_report_hash=report["report_hash"])
    _write(target / "closure-observation.json", observation, "observation_hash")
    with pytest.raises(ValueError, match="closure"):
        repair.run_pair_repair(directory)
    assert len(remote) == 20 and not (target / "manifest.json").exists()


def test_concurrent_reservations_no_lost_updates_duplicate_job_is_idempotent_and_cap_is_hard(directory, remote):
    provider = repair.PairRepairProviders(directory / "provider-cache")
    case = repair.pilot_cases()[0]

    def submit(identifier):
        return repair._job(directory, provider, identifier, case["vacancy"], case["A"], case["B"], case["policy"], "pilot")

    with ThreadPoolExecutor(max_workers=5) as executor:
        answers = list(executor.map(submit, ["duplicate"] * 10))
        assert all(answer == answers[0] for answer in answers)
        assert _read_json(directory / repair.VERSION / "budget.json")["pilot_initial"] == 1
        list(executor.map(submit, [f"concurrent-{index}" for index in range(19)]))
    assert _read_json(directory / repair.VERSION / "budget.json")["pilot_initial"] == 20
    with pytest.raises(ValueError, match="allowance exhausted"):
        submit("over-cap")
    assert len(remote) == 1 and _read_json(directory / "provider-cache/usage.json")["pair"] == BASE_LEDGER_PAIR + 1
    assert not (directory / repair.VERSION / "jobs/over-cap-0.json").exists()


def test_selection_includes_all_quarantined_nonclass_and_blocks_more_than_nine(directory, remote):
    repair.run_pair_repair(directory, pilot_only=True)
    pilot = repair.prepare_pilot(directory)
    dataset = repair._base_dataset(directory, pilot)
    technical = [family for family in dataset["families"] if not repair._class_only(family["vacancy"])]
    for family in technical[:10]:
        family["quarantined"] = True
    with pytest.raises(ValueError, match="plus 9"):
        repair._selection(directory, dataset, pilot)
    blocked = repair._verified(directory / repair.VERSION / "selection-blocked-report.json", "report_hash")
    assert len(blocked["quarantined_nonclass"]) == 10 and blocked["activation_allowed"] is False


def test_cache_only_validator_rejects_tampered_preserved_grade_or_raw_response(directory, remote):
    repair.run_pair_repair(directory)
    path = directory / repair.VERSION / "dataset.json"
    derived = _read_json(path)
    derived["families"][0]["grades"][0] = 0
    _write(path, derived, "dataset_hash")
    with pytest.raises(ValueError):
        repair.validate_pair_repair(directory)


def test_invalid_teacher_class_label_is_rejected_not_overwritten(tmp_path, monkeypatch):
    provider = repair.PairRepairProviders(tmp_path)
    case = repair.pilot_cases()[0]
    answer = _answer(case["vacancy"], case["A"], case["B"], case["policy"])
    answer.update(label="A", reason_code="prefer_known_facts")
    monkeypatch.setattr(provider, "request", lambda *_args: {"model": "qwen3.8-27b", "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})
    with pytest.raises(ValueError, match="frozen class-only policy"):
        provider.judge(case["vacancy"], case["A"], case["B"], case["policy"])


def test_known_but_inapplicable_salary_evidence_is_rejected_for_class_only(tmp_path, monkeypatch):
    provider = repair.PairRepairProviders(tmp_path)
    case = repair.pilot_cases()[0]
    answer = _answer(case["vacancy"], case["A"], case["B"], case["policy"])
    answer["evidence_A"][0] = next(token for token, fact in source_citations(case["A"], case["B"]).items()
                                   if fact["candidate"] == "A" and fact["field"] == "salary_min")
    monkeypatch.setattr(provider, "request", lambda *_args: {"model": "qwen3.8-27b", "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})
    with pytest.raises(ValueError, match="inapplicable"):
        provider.judge(case["vacancy"], case["A"], case["B"], case["policy"])


def test_invalid_family_is_wholly_unadmitted_and_keeps_quarantine(directory, remote, monkeypatch):
    opened = repair.PairRepairProviders._open

    def fail_one_family(provider, request):
        state = json.loads(json.loads(request.data)["messages"][1]["content"])
        if state["A"]["description"].startswith("Семья backend-01"):
            return io.BytesIO(json.dumps({"model": "qwen3.8-27b", "choices": [{
                "finish_reason": "length", "message": {"content": "{}"}}]}).encode())
        return opened(provider, request)

    monkeypatch.setattr(repair.PairRepairProviders, "_open", fail_one_family)
    report = repair.run_pair_repair(directory)
    assert report["failed_families"] == ["backend-01"] and not report["quality"]["passed"]
    assert "backend-01" in report["quality"]["quarantined_families"]
    base = repair._verified(directory / repair.BASE_COHORT / "dataset.json", "dataset_hash")
    derived, verified = repair.validate_pair_repair(directory)
    assert verified == report
    old = next(family for family in base["families"] if family["manifest"]["family_id"] == "backend-01")
    new = next(family for family in derived["families"] if family["manifest"]["family_id"] == "backend-01")
    assert new["pairs"] == old["pairs"] and new["pair_answers"] == old["pair_answers"]
    assert new["quarantined"] is True and "pair_repair" not in new
    assert not (directory / repair.VERSION / "families/backend-01.json").exists()
