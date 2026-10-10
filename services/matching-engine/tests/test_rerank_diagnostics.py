"""Diagnostic audit and bounded prompt pilot use synthetic cached facts and mocked HTTP."""

import io
import json
from copy import deepcopy

import pytest

import matching_engine.rerank_diagnostics as diagnostics
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    SYSTEMONE_PAIR_PROMPT_VERSION,
    SystemOneProviders,
    generation_identity,
    teacher_identity,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _trait(index):
    return {"grade": "junior", "levels": {"python": 3, "react": 4, "sql": 2, "rest_api": 3, "teamwork": 4},
            "status": "confirmed", "duration_months": 12 + index, "production_support": False,
            "salary_min": 70000 + index * 1000, "description": f"Синтетический профиль {index}"}


def _controlled_traits(raw):
    traits = deepcopy(raw)
    for index, trait in enumerate(traits):
        trait["grade"] = ("intern", "junior", "middle", "senior")[(index + 5) % 4]
        if index == 9:
            trait["status"] = "rejected"
        trait["structured_facts_available"] = index % 4 != 0
        if not trait["structured_facts_available"]:
            trait["duration_months"] = None
            trait["production_support"] = None
    return traits


def _response(label, confidence=.7):
    probabilities = dict.fromkeys(("A", "B", "tie", "insufficient"), .1)
    probabilities[label] = .7
    return {"model": teacher_identity()["model"], "answers": {"preference": {
        "choice": label, "confidence": confidence, "probabilities": probabilities}},
        "usage": {"total_tokens": 17}}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    raw = [_trait(index) for index in range(10)]
    for batch in (1, 2):
        receipt = {"family_id": "frontend-05", "batch": batch, "generator": generation_identity(),
                   "raw_count": 5, "traits": raw[(batch - 1) * 5:batch * 5], "dropped": []}
        receipt["receipt_hash"] = digest(receipt)
        _write(tmp_path / "generation-batches" / f"frontend-05-{batch}.json", receipt)
    traits = _controlled_traits(raw)
    key_file = tmp_path / "synthetic-key.txt"
    key_file.write_text("sk-unit-diagnostic-key", encoding="utf-8")
    monkeypatch.setenv("RERANK_SYSTEMONE_KEY_FILE", str(key_file))
    provider = SystemOneProviders(tmp_path / "provider-cache", key_file=key_file)
    _write(provider.directory / "usage.json", {"generation": 2, "grade": 7})
    vacancy = {"class": "frontend", "stacks": {"react": 4}, "salary": {"min": 50000, "max": 200000}}
    preferences = "Prefer economical salary with known frontend fit."

    def legacy_response(_provider, request):
        state = json.loads(json.loads(request.data)["state"])
        first, second = state["A"]["salary_min"], state["B"]["salary_min"]
        a, b = (first - 70000) // 1000, (second - 70000) // 1000
        label = "A" if (min(a, b), max(a, b)) in ((0, 1), (0, 2)) else ("A" if first < second else "B")
        return io.BytesIO(json.dumps(_response(label)).encode())

    with monkeypatch.context() as transport:
        transport.setattr(SystemOneProviders, "_open", legacy_response)
        for a, b in diagnostics.PAIRS:
            provider.pair(vacancy, traits[a], traits[b], preferences)
        for a, b in diagnostics.PAIRS[:3]:
            provider.pair(vacancy, traits[b], traits[a], preferences)
    swaps = []
    for a, b in diagnostics.PAIRS[:3]:
        hashes = [digest(provider._identity("/v1/systemone", provider._pair_payload(
            vacancy, traits[first], traits[second], preferences), "pair")) for first, second in ((a, b), (b, a))]
        swaps.append({"a": a, "b": b, "direct_request_hash": hashes[0], "swapped_request_hash": hashes[1]})
    report = {"failed_family": "frontend-05", "failed_family_swap_audits": swaps}
    report["report_hash"] = digest(report)
    source_report = tmp_path / "source-report.json"
    _write(source_report, report)
    assert json.loads((provider.directory / "usage.json").read_text()) == {"generation": 2, "grade": 7, "pair": 13}
    return {"directory": tmp_path, "source_report": source_report, "report": report, "traits": traits}


def _forbid_transport(monkeypatch):
    monkeypatch.setattr(SystemOneProviders, "_open", lambda *_args: pytest.fail("cache-only operation attempted HTTP"))
    monkeypatch.setattr(SystemOneProviders, "_api_key", lambda *_args: pytest.fail("cache-only operation read credentials"))


def _review(sources):
    directory = sources["directory"]
    audit = diagnostics.audit_cached_pairs(directory, sources["source_report"])
    packet = json.loads((directory / "diagnostic-frontend-05" / "blind-packet.json").read_text())
    review = {"packet_hash": packet["packet_hash"], "reviewer_kind": "assistant",
              "independent_human_evaluation": False,
              "cases": [{"case_id": row["case_id"], "label": "insufficient"} for row in packet["cases"]]}
    review["review_hash"] = digest(review)
    _write(directory / "diagnostic-frontend-05" / "blind-review.json", review)
    return audit, review


def _pilot_transport(monkeypatch, *, abstain_hard=False):
    calls = []

    def respond(_provider, request):
        payload = json.loads(request.data)
        state = json.loads(payload["state"])
        assert list(state) == ["vacancy", "preferences", "A", "B"]
        calls.append(state)
        first, second = state["A"]["salary_min"], state["B"]["salary_min"]
        indices = ((first - 70000) // 1000, (second - 70000) // 1000)
        confidence = .1284 if abstain_hard and tuple(sorted(indices)) in diagnostics.PAIRS[:3] else .7
        return io.BytesIO(json.dumps(_response("A" if first < second else "B", confidence)).encode())

    monkeypatch.setattr(SystemOneProviders, "_open", respond)
    return calls


def test_cached_audit_verifies_swaps_and_emits_score_blind_packet_without_http(sources, monkeypatch):
    _forbid_transport(monkeypatch)
    directory = sources["directory"]
    original = {path.name: path.read_bytes() for path in (directory / "provider-cache").glob("*.json")}
    report = diagnostics.audit_cached_pairs(directory, sources["source_report"])
    assert report["http_calls"] == 0
    assert report["raw_consistency"] == pytest.approx(1 / 3)
    assert [row["raw_consistent"] for row in report["pairs"]] == [False, False, True]
    assert all(row["exact_swap"] for row in report["pairs"])
    assert [(row["a"], row["b"]) for row in report["pairs"]] == list(diagnostics.PAIRS[:3])
    assert report["source_report_hash"] == sources["report"]["report_hash"]
    packet = json.loads((directory / "diagnostic-frontend-05" / "blind-packet.json").read_text())
    mapping = json.loads((directory / "diagnostic-frontend-05" / "blind-map.json").read_text())
    assert len(packet["cases"]) == 3
    assert packet["packet_hash"] == digest({key: value for key, value in packet.items() if key != "packet_hash"})
    assert "not an independent human" in packet["purpose"]
    for case in packet["cases"]:
        assert set(case) == {"case_id", "vacancy", "preferences", "A", "B"}
        origin = next(item for item in mapping if item["case_id"] == case["case_id"])
        indices = (origin["original_b"], origin["original_a"]) if origin["swapped"] else (origin["original_a"], origin["original_b"])
        assert case["A"] == sources["traits"][indices[0]] and case["B"] == sources["traits"][indices[1]]
        assert not {"probabilities", "choice", "confidence", "main_score", "vectors", "source_request_hash"} & set(case)
    assert {path.name: path.read_bytes() for path in (directory / "provider-cache").glob("*.json")} == original
    assert diagnostics.audit_cached_pairs(directory, sources["source_report"]) == report


@pytest.mark.parametrize("mutation", ["report_hash", "request_hash", "response_hash"])
def test_audit_rejects_modified_source_hashes(sources, monkeypatch, mutation):
    _forbid_transport(monkeypatch)
    if mutation == "report_hash":
        value = deepcopy(sources["report"])
        value["report_hash"] = "0" * 64
        _write(sources["source_report"], value)
    else:
        key = sources["report"]["failed_family_swap_audits"][0]["direct_request_hash"]
        path = sources["directory"] / "provider-cache" / f"{key}.json"
        value = json.loads(path.read_text())
        value[mutation] = "0" * 64
        _write(path, value)
    with pytest.raises(ValueError, match="integrity failed"):
        diagnostics.audit_cached_pairs(sources["directory"], sources["source_report"])


@pytest.mark.parametrize("mutation", ["candidate", "preferences"])
def test_audit_rejects_nonexact_swaps_even_with_consistent_receipt_hashes(sources, monkeypatch, mutation):
    _forbid_transport(monkeypatch)
    report = deepcopy(sources["report"])
    entry = report["failed_family_swap_audits"][0]
    directory = sources["directory"] / "provider-cache"
    value = json.loads((directory / f"{entry['swapped_request_hash']}.json").read_text())
    state = json.loads(value["request"]["payload"]["state"])
    if mutation == "candidate":
        state["A"]["salary_min"] += 1
    else:
        state["preferences"] = "Different employer preference"
    value["request"]["payload"]["state"] = json.dumps(state)
    value["request_hash"] = digest(value["request"])
    _write(directory / f"{value['request_hash']}.json", value)
    entry["swapped_request_hash"] = value["request_hash"]
    report["report_hash"] = digest({key: item for key, item in report.items() if key != "report_hash"})
    _write(sources["source_report"], report)
    with pytest.raises(ValueError, match="not exact swaps"):
        diagnostics.audit_cached_pairs(sources["directory"], sources["source_report"])


def _gate_rows():
    return [{"original_hard_case": index < 3, "raw_consistent": True, "final_consistent": True,
             "direct_label": "A", "reverse_label": "B", "direct_raw_label": "A", "reverse_raw_label": "B"}
            for index in range(10)]


@pytest.mark.parametrize("mutation", ["hard_abstained", "hard_raw_wrong", "raw_below_gate", "too_few_decisive", "wrong_size"])
def test_pilot_gates_cannot_replace_raw_symmetry_with_abstention_or_incomplete_coverage(mutation):
    rows = _gate_rows()
    assert diagnostics.pilot_gates(rows)["passed"]
    if mutation == "hard_abstained":
        rows[0].update(direct_label="insufficient", reverse_label="insufficient")
    elif mutation == "hard_raw_wrong":
        rows[0].update(raw_consistent=False, direct_raw_label="A", reverse_raw_label="A")
    elif mutation == "raw_below_gate":
        for row in rows[3:6]:
            row["raw_consistent"] = False
    elif mutation == "too_few_decisive":
        for row in rows[5:]:
            row.update(direct_label="tie", reverse_label="tie")
    else:
        rows.pop()
    assert not diagnostics.pilot_gates(rows)["passed"]


def test_successful_pilot_makes_only_twenty_new_calls_and_replays_without_budget_change(sources, monkeypatch):
    directory = sources["directory"]
    audit, review = _review(sources)
    originals = {path.name: path.read_bytes() for path in (directory / "provider-cache").glob("*.json") if path.name != "usage.json"}
    calls = _pilot_transport(monkeypatch)
    result = diagnostics.run_prompt_pilot(directory, sources["source_report"])
    assert result["state"] == "passed"
    assert len(calls) == 20
    assert result["ledger_before"] == {"generation": 2, "grade": 7, "pair": 13}
    assert result["ledger_after"] == {"generation": 2, "grade": 7, "pair": 33}
    assert result["historical_cache_bytes_preserved"] and result["budget_preserved"]
    assert result["source_audit_hash"] == audit["report_hash"] and result["review_hash"] == review["review_hash"]
    assert result["teacher"] == teacher_identity(SYSTEMONE_PAIR_PROMPT_VERSION)
    assert result["gates"] == {"raw_consistent": 10, "final_consistent": 10, "direct_decisive": 10,
                               "hard_cases_passed": True, "passed": True}
    assert all(result[key] is False for key in ("training_started", "cohort_teacher_changed", "activation_allowed", "independent_human_evaluation"))
    assert [(row["a"], row["b"]) for row in result["comparisons"]] == list(diagnostics.PAIRS)
    for index, (a, b) in enumerate(diagnostics.PAIRS):
        assert calls[index * 2]["A"] == sources["traits"][a]
        assert calls[index * 2]["B"] == sources["traits"][b]
        assert calls[index * 2 + 1]["A"] == sources["traits"][b]
        assert calls[index * 2 + 1]["B"] == sources["traits"][a]
    assert [row["original_hard_case"] for row in result["comparisons"]] == [True] * 3 + [False] * 7
    assert sum(row["salary_focused_nonisolated_control"] for row in result["comparisons"]) == 1
    assert all((directory / "provider-cache" / name).read_bytes() == value for name, value in originals.items())
    assert result["report_hash"] == digest({key: value for key, value in result.items() if key != "report_hash"})
    _forbid_transport(monkeypatch)
    before = {path.name: path.read_bytes() for path in (directory / "provider-cache").glob("*.json")}
    assert diagnostics.run_prompt_pilot(directory, sources["source_report"]) == result
    assert {path.name: path.read_bytes() for path in (directory / "provider-cache").glob("*.json")} == before


def test_pilot_hard_case_abstention_fails_even_with_perfect_raw_and_final_symmetry(sources, monkeypatch):
    _review(sources)
    calls = _pilot_transport(monkeypatch, abstain_hard=True)
    result = diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    assert len(calls) == 20 and result["state"] == "stopped"
    assert result["gates"] == {"raw_consistent": 10, "final_consistent": 10, "direct_decisive": 7,
                               "hard_cases_passed": False, "passed": False}
    for row in result["comparisons"][:3]:
        assert (row["direct_raw_label"], row["reverse_raw_label"]) == ("A", "B")
        assert (row["direct_label"], row["reverse_label"]) == ("insufficient", "insufficient")
        assert row["direct_answer"]["confidence"] == .1284
    _forbid_transport(monkeypatch)
    before = (sources["directory"] / "provider-cache" / "usage.json").read_bytes()
    assert diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"]) == result
    assert (sources["directory"] / "provider-cache" / "usage.json").read_bytes() == before


def test_failed_pilot_consumes_one_attempt_sanitizes_error_and_never_retries(sources, monkeypatch):
    _review(sources)
    calls = []

    def fail(_provider, request):
        calls.append(request)
        raise TimeoutError("private echoed Authorization: sk-unit-diagnostic-key")

    monkeypatch.setattr(SystemOneProviders, "_open", fail)
    result = diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    assert len(calls) == 1 and result["state"] == "stopped"
    assert result["error_type"] == "RuntimeError" and result["comparisons"] == []
    assert result["ledger_after"]["pair"] - result["ledger_before"]["pair"] == 1
    assert "sk-unit" not in json.dumps(result)
    _forbid_transport(monkeypatch)
    assert diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"]) == result


def test_interrupted_pilot_blocks_retry_without_http_or_budget_reset(sources, monkeypatch):
    _review(sources)

    def interrupt(_provider, _request):
        raise KeyboardInterrupt("synthetic operator interruption")

    monkeypatch.setattr(SystemOneProviders, "_open", interrupt)
    with pytest.raises(KeyboardInterrupt):
        diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    target = sources["directory"] / "diagnostic-frontend-05"
    assert (target / "pilot-started.json").exists() and not (target / "pilot-report.json").exists()
    before = (sources["directory"] / "provider-cache" / "usage.json").read_bytes()
    _forbid_transport(monkeypatch)
    with pytest.raises(ValueError, match="interrupted pilot requires operator review"):
        diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    assert (sources["directory"] / "provider-cache" / "usage.json").read_bytes() == before


def test_pilot_cannot_bypass_an_exhausted_shared_pair_budget(sources, monkeypatch):
    _review(sources)
    ledger_path = sources["directory"] / "provider-cache" / "usage.json"
    _write(ledger_path, {"generation": 2, "grade": 7, "pair": 3000})
    before = ledger_path.read_bytes()
    _forbid_transport(monkeypatch)
    report = diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    assert report["state"] == "stopped" and report["error_type"] == "BudgetExceeded"
    assert report["comparisons"] == []
    assert report["ledger_before"] == report["ledger_after"] == {"generation": 2, "grade": 7, "pair": 3000}
    assert ledger_path.read_bytes() == before


@pytest.mark.parametrize("rehash", [False, True])
def test_completed_pilot_checks_cached_raw_answers_before_replay(sources, monkeypatch, rehash):
    _review(sources)
    _pilot_transport(monkeypatch)
    directory = sources["directory"]
    report = diagnostics.run_prompt_pilot(directory, sources["source_report"])
    key = report["comparisons"][0]["cache_keys"][0]
    path = directory / "provider-cache" / f"{key}.json"
    value = json.loads(path.read_text())
    value["response"]["answers"]["preference"]["confidence"] = .4
    if rehash:
        value["response_hash"] = digest(value["response"])
    _write(path, value)
    ledger = (directory / "provider-cache" / "usage.json").read_bytes()
    _forbid_transport(monkeypatch)
    with pytest.raises(ValueError, match="integrity failed|differs from cached"):
        diagnostics.run_prompt_pilot(directory, sources["source_report"])
    assert (directory / "provider-cache" / "usage.json").read_bytes() == ledger


def test_corrupted_completed_pilot_report_is_rejected_without_new_attempts(sources, monkeypatch):
    _review(sources)
    _pilot_transport(monkeypatch)
    report = diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    report["report_hash"] = "0" * 64
    _write(sources["directory"] / "diagnostic-frontend-05" / "pilot-report.json", report)
    _forbid_transport(monkeypatch)
    before = (sources["directory"] / "provider-cache" / "usage.json").read_bytes()
    with pytest.raises(ValueError, match="diagnostic pilot report integrity"):
        diagnostics.run_prompt_pilot(sources["directory"], sources["source_report"])
    assert (sources["directory"] / "provider-cache" / "usage.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["generation_receipt", "review_hash", "review_coverage"])
def test_pilot_rejects_modified_generation_or_blind_review_before_new_attempts(sources, monkeypatch, mutation):
    _audit, review = _review(sources)
    directory = sources["directory"]
    if mutation == "generation_receipt":
        path = directory / "generation-batches" / "frontend-05-1.json"
        value = json.loads(path.read_text())
        value["traits"][0]["salary_min"] += 1
        _write(path, value)
    else:
        if mutation == "review_hash":
            review["review_hash"] = "0" * 64
        else:
            review["cases"].pop()
            review["review_hash"] = digest({key: value for key, value in review.items() if key != "review_hash"})
        _write(directory / "diagnostic-frontend-05" / "blind-review.json", review)
    _forbid_transport(monkeypatch)
    before = (directory / "provider-cache" / "usage.json").read_bytes()
    with pytest.raises(ValueError):
        diagnostics.run_prompt_pilot(directory, sources["source_report"])
    assert (directory / "provider-cache" / "usage.json").read_bytes() == before
    assert not (directory / "diagnostic-frontend-05" / "pilot-started.json").exists()
