"""Offline continuation: fixed stack proofs, immutable v3 lineage and cumulative charges."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from test_rerank_cohort_pair_repair import (
    _answer,
    _previous_fixture,
    _read_json,
    _write,
)
from test_rerank_cohort_pair_repair import directory as base_fixture

from matching_engine import rerank_cohort_pair_completion as completion
from matching_engine import rerank_cohort_pair_repair as parent
from matching_engine.rerank_cohort_review import _packets
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import salary_comparison

POLICY = {"id": "global-baseline-prior-3", "ordered_priorities": ["Prefer declared fit to the stated stacks.",
          "Then prefer stronger confirmation of those matching skills."], "instructions": "Use provided own typed facts.",
          "exclusive_criterion": False, "required_measurements": []}


def _completion_answer(state):
    a, b = state["A"], state["B"]
    levels = a["levels"]["python"], b["levels"]["python"]
    statuses = {"confirmed": 3, "partial": 2, "self_reported": 1, "pending": 0}
    left, right = (levels[0], statuses[a["skill_statuses"]["python"]]), (levels[1], statuses[b["skill_statuses"]["python"]])
    label = "tie" if left == right else "A" if left > right else "B"
    return {"label": label, "reason_code": completion._REASON[label], "status_A": a["status"], "status_B": b["status"],
            "evidence_A": completion._refs(a, b)["A"], "evidence_B": completion._refs(a, b)["B"],
            "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"]}


def _response(answer, finish="stop"):
    return io.BytesIO(json.dumps({"model": "qwen3.8-27b", "choices": [{"finish_reason": finish,
          "message": {"content": json.dumps(answer)}}]}).encode())


@pytest.fixture
def directory(tmp_path, monkeypatch):
    directory = base_fixture.__wrapped__(tmp_path)
    base = directory / parent.BASE_COHORT
    manifest = _read_json(base / "manifest.json")
    row = next(row for row in manifest["families"] if row["family_id"] == completion.FAMILY_ID)
    row["policy"] = deepcopy(POLICY)
    source = _read_json(base / "sources/qa-06.json")
    source["manifest"] = deepcopy(row)
    for index, trait in enumerate(source["traits"]):
        trait["levels"]["python"] = [2, 2, 1, 2, 2, 1, 2][index]
        trait["skill_statuses"]["python"] = ["confirmed", "confirmed", "self_reported", "confirmed", "confirmed", "pending", "confirmed"][index]
    source = _write(base / "sources/qa-06.json", source, "record_hash")
    dataset = _read_json(base / "dataset.json")
    for family in dataset["families"]:
        if family["manifest"]["family_id"] == completion.FAMILY_ID:
            family.update(manifest=deepcopy(row), traits=source["traits"], source_hash=source["record_hash"])
        if family["manifest"]["family_id"] in ("backend-03", "fullstack-02", completion.FAMILY_ID):
            family.update(quarantined=True, swap_audits=[True, False, False])
        family.update(_write(base / "families" / f"{family['manifest']['family_id']}.json", family, "record_hash"))
    manifest = _write(base / "manifest.json", manifest, "manifest_hash")
    dataset["manifest_hash"] = manifest["manifest_hash"]
    dataset = _write(base / "dataset.json", dataset, "dataset_hash")
    report = _read_json(base / "report.json")
    report["dataset_hash"] = dataset["dataset_hash"]
    _write(base / "report.json", report, "report_hash")
    sources = {path.stem: _read_json(path) for path in (base / "sources").glob("*.json")}
    packets = _packets(manifest, sources, parent.BASE_COHORT)
    for reviewer, packet in packets.items():
        _write(base / "reviews" / f"{reviewer}-packet.json", packet)
    _write(base / "reviews/assistant-review.json", {"packet_hash": packets["assistant"]["packet_hash"], "reviewer_kind": "assistant",
        "labels": [{"pair_id": case["pair_id"], "label": "A", "reason": "Original offline nomination", "factual_issue": False}
                   for case in packets["assistant"]["cases"]]})
    for version in parent.PRIOR_REPAIRS:
        _previous_fixture(directory, version)
    monkeypatch.setattr(parent.PairRepairProviders, "_api_key", lambda _self: "offline-key")

    def v3_response(_provider, request):
        state = json.loads(json.loads(request.data)["messages"][1]["content"])
        answer = _answer(state["vacancy"], state["A"], state["B"], state["policy"])
        if state["A"]["description"].startswith("Семья qa-06, задача 3") and state["B"]["description"].startswith("Семья qa-06, задача 5"):
            answer["evidence_A"] = [answer["evidence_A"][0]] * 2
        return _response(answer)

    monkeypatch.setattr(parent.PairRepairProviders, "_open", v3_response)
    parent_report = parent.run_pair_repair(directory)
    assert parent_report["budget"] == completion._PRIOR_BUDGET
    assert parent_report["failed_families"] == [completion.FAMILY_ID]
    assert parent_report["ledger_after"]["pair"] == 2398
    return directory


@pytest.fixture
def remote(monkeypatch):
    calls = []
    monkeypatch.setattr(completion.PairCompletionProviders, "_api_key", lambda _self: "offline-key")

    def opened(_provider, request):
        state = json.loads(json.loads(request.data)["messages"][1]["content"])
        calls.append(state)
        return _response(_completion_answer(state))

    monkeypatch.setattr(completion.PairCompletionProviders, "_open", opened)
    return calls


def test_exact_array_schema_projection_and_four_label_reason_objects(tmp_path):
    a, b = parent._candidate(level=2, skill_status="confirmed"), parent._candidate(level=1, skill_status="pending")
    vacancy = {"stacks": {"python": 4}, "domens": {"algorithms": 4}, "soft_skills": {"teamwork": 4}}
    provider = completion.PairCompletionProviders(tmp_path)
    for attempt in (0, 1):
        payload = provider.pair_payload(vacancy, a, b, POLICY, attempt)
        state = json.loads(payload["messages"][1]["content"])
        assert state["A"] == a and state["B"] == b and state["policy"] == POLICY
        assert set(state["decision_inputs"]["A"]) == {"python"}
        assert "expected" not in state and "main_scores" not in json.dumps(state)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert len(schema["anyOf"]) == 4 and "uniqueItems" not in json.dumps(schema)
        for branch in schema["anyOf"]:
            for side in ("A", "B"):
                assert branch["properties"][f"evidence_{side}"] == {"type": "array", "enum": [completion._refs(a, b)[side]]}
                assert len(set(completion._refs(a, b)[side])) == 2
        assert "project references" in payload["messages"][0]["content"]
        assert provider._identity("/v1/chat/completions", payload, "pair")["prompt_version"] == completion.PAIR_PROMPT_VERSION + ("-repair1" if attempt else "")
    for kind in ("generation", "grade"):
        with pytest.raises(ValueError, match="forbids"):
            provider._endpoint(kind)


@pytest.mark.parametrize("change", ["duplicate", "project", "reverse_order", "root_status", "reason"])
def test_raw_evidence_format_error_never_normalized(tmp_path, monkeypatch, change):
    a, b = parent._candidate(level=2), parent._candidate(level=1)
    vacancy = {"stacks": {"python": 4}}
    answer = _completion_answer({"A": a, "B": b})
    if change == "duplicate":
        answer["evidence_A"] *= 2
    elif change == "project":
        answer["evidence_A"][0] = next(token for token, fact in parent.source_citations(a, b).items()
                                      if fact["candidate"] == "A" and fact["field"] == "grade")
    elif change == "reverse_order":
        answer["evidence_A"].reverse()
    elif change == "root_status":
        answer["status_A"] = "rejected"
    else:
        answer["reason_code"] = "comparable_fit"
    provider = completion.PairCompletionProviders(tmp_path)
    monkeypatch.setattr(provider, "request", lambda *_args: json.loads(_response(answer).read()))
    before = deepcopy(answer)
    with pytest.raises(ValueError):
        provider.judge(vacancy, a, b, POLICY)
    assert answer == before


def test_whole_completion_preserves_v3_sources_grades_oracle_and_replays_cache_only(directory, remote, monkeypatch):
    frozen = {path: path.read_bytes() for path in (directory / parent.VERSION).rglob("*.json")}
    manifest = completion.prepare_completion(directory)
    assert not remote and manifest["maximum_attempts"] == 64
    assert [(case["a_index"], case["b_index"], case["expected"]) for case in manifest["cases"]] == [
        (0, 1, "tie"), (1, 3, "tie"), (3, 5, "A"), (2, 6, "B")]
    report = completion.run_pair_completion(directory)
    assert report["state"] == "repaired_quality_passed"
    assert report["budget"] == {"pilot_initial": 8, "pilot_retry": 0, "family_initial": 24, "family_retry": 0}
    # Four pilot inputs reuse their exact identities during full-family processing.
    assert len(remote) <= 32 and report["ledger_after"]["pair"] == 2398 + len(remote)
    assert report["quality"]["swap_total"] == 132 and report["quality"]["oracle_swap_total"] == 48
    assert not report["quality"]["quarantined_families"]
    assert report["parent_repair"]["actual_pair_reservations"] == 78
    assert report["actual_pair_reservations"] == 25 and report["cached_pair_reservations"] == 7
    closure = _read_json(directory / completion.VERSION / "closure-observation.json")
    assert report["pilot_closure_observation_hash"] == closure["observation_hash"]
    assert closure["actual_pair_reservations"] == 8 and closure["global_ledger"]["pair"] == 2406
    assert closure["base_dataset_existed"] is True and closure["base_completed_families"] == 60
    assert all(path.read_bytes() == value for path, value in frozen.items())
    monkeypatch.setattr(completion.PairCompletionProviders, "_open", lambda *_args: pytest.fail("replay HTTP"))
    monkeypatch.setattr(completion.PairCompletionProviders, "_api_key", lambda *_args: pytest.fail("replay key"))
    dataset, checked = completion.validate_pair_completion(directory)
    assert checked == report and completion.run_pair_completion(directory) == report
    old = _read_json(directory / parent.VERSION / "dataset.json")
    for left, right in zip(old["families"], dataset["families"], strict=True):
        if left["manifest"]["family_id"] != completion.FAMILY_ID:
            assert left == right
        else:
            for field in ("traits", "source_hash", "vectors", "main_scores", "grades", "suitability_answers", "manifest"):
                assert left[field] == right[field]
    for reviewer in ("assistant", "user"):
        a = _read_json(directory / parent.VERSION / "reviews" / f"{reviewer}-packet.json")
        b = _read_json(directory / completion.VERSION / "reviews" / f"{reviewer}-packet.json")
        assert {key: value for key, value in a.items() if key not in ("packet_hash", "pair_repair_pilot_manifest_hash")} == {
            key: value for key, value in b.items() if key not in ("packet_hash", "pair_repair_pilot_manifest_hash")}


@pytest.mark.parametrize("target", ["failed_parent_raw", "local_oracle", "unselected_grade", "extra_usage"])
def test_parent_all_raw_failures_and_derived_lineage_tamper_blocked(directory, remote, target):
    if target == "failed_parent_raw":
        job = _read_json(directory / parent.VERSION / "jobs/qa-06-pair-3-5-0.json")
        path = directory / "provider-cache" / f"{job['request_hash']}.json"
        raw = _read_json(path)
        answer = json.loads(raw["response"]["choices"][0]["message"]["content"])
        answer["evidence_A"] = list(dict.fromkeys(answer["evidence_A"]))
        # Restore a truly valid raw answer while the frozen job still says failed.
        state = json.loads(raw["request"]["payload"]["messages"][1]["content"])
        answer = _answer(state["vacancy"], state["A"], state["B"], state["policy"])
        raw["response"]["choices"][0]["message"]["content"] = json.dumps(answer)
        raw["response_hash"] = digest(raw["response"])
        _write(path, raw)
        with pytest.raises(ValueError, match="sealed state"):
            completion.prepare_completion(directory)
        assert not remote
        return
    completion.run_pair_completion(directory)
    if target == "local_oracle":
        path = next((directory / parent.VERSION / "oracle-receipts").glob("*.json"))
        value = _read_json(path)
        value["oracle_code_hash"] = "changed"
        _write(path, value, "record_hash")
    elif target == "unselected_grade":
        path = directory / completion.VERSION / "dataset.json"
        value = _read_json(path)
        value["families"][0]["grades"][0] = 0
        _write(path, value, "dataset_hash")
    else:
        path = directory / "provider-cache/usage.json"
        value = _read_json(path)
        value["pair"] += 1
        _write(path, value)
    with pytest.raises(ValueError):
        completion.validate_pair_completion(directory)


def test_concurrent_job_idempotence_and_bounded_reservation(directory, remote):
    manifest = completion.prepare_completion(directory)
    provider = completion.PairCompletionProviders(directory / "provider-cache")
    case = manifest["cases"][0]
    with ThreadPoolExecutor(max_workers=5) as executor:
        answers = list(executor.map(lambda _: completion._job(directory, provider, manifest, "duplicate", case, "pilot"), range(10)))
    assert all(answer == answers[0] for answer in answers) and len(remote) == 1
    assert _read_json(directory / completion.VERSION / "budget.json")["pilot_initial"] == 1
    path = directory / completion.VERSION / "jobs/ambiguous-0.json"
    identity = provider._identity("/v1/chat/completions", provider.pair_payload(case["vacancy"], case["B"], case["A"], case["policy"]), "pair")
    _write(path, {"state": "started", "request_hash": digest(identity)})
    with pytest.raises(ValueError, match="ambiguous started"):
        completion._job(directory, provider, manifest, "ambiguous", {**case, "A": case["B"], "B": case["A"]}, "pilot")
    assert len(remote) == 1
    usage = _read_json(directory / "provider-cache/usage.json")
    usage["pair"] = 3000
    _write(directory / "provider-cache/usage.json", usage)
    with pytest.raises(ValueError, match="remaining allowance"):
        completion._job(directory, provider, manifest, "cap", case, "pilot")
    assert not (directory / completion.VERSION / "jobs/cap-0.json").exists()



def test_strict_flags_budget_types_and_pilot_closure_are_not_selfhash_bypasses(directory, remote):
    completion.run_pair_completion(directory)
    target = directory / completion.VERSION
    report_path = target / "repair-report.json"
    original_report = _read_json(report_path)
    mutations = [("state", "pilot_passed"), ("grade_calls", 1), ("generation_calls", False),
                 ("golden_model_predictions", 1), ("training_started", True), ("activation_allowed", True),
                 ("actual_pair_reservations", 32), ("cached_pair_reservations", 0), ("raw_jobs_hash", "changed")]
    for key, value in mutations:
        _write(report_path, {**original_report, key: value}, "report_hash")
        with pytest.raises(ValueError):
            completion.validate_pair_completion(directory)
    _write(report_path, original_report)
    budget_path = target / "budget.json"
    budget = _read_json(budget_path)
    _write(budget_path, {**budget, "pilot_retry": False})
    with pytest.raises(ValueError, match="accounting"):
        completion.validate_pair_completion(directory)
    _write(budget_path, budget)
    closure_path = target / "closure-observation.json"
    closure = _read_json(closure_path)
    _write(closure_path, {**closure, "base_dataset_existed": False}, "observation_hash")
    with pytest.raises(ValueError, match="closure"):
        completion.validate_pair_completion(directory)
    assert len(remote) == 25
