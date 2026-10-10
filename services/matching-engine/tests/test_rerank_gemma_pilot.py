"""Frozen alternative-teacher pilot: budget, blindness, replay and crash safety."""

import io
import json
from copy import deepcopy

import pytest

import matching_engine.rerank_gemma_pilot as pilot
from matching_engine.rerank_diagnostics import REVERSE
from matching_engine.rerank_hash import digest


def write(path, value, hash_field=None):
    if hash_field:
        value[hash_field] = digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def source(tmp_path, monkeypatch):
    traits = [{"grade": "middle", "levels": {"python": i % 8, "react": 4, "sql": 4,
              "rest_api": 4, "teamwork": 4}, "status": "confirmed", "duration_months": 12,
              "production_support": True, "salary_min": 60000 + i * 1000,
              "structured_facts_available": True, "description": "Synthetic"} for i in range(10)]
    vacancy = {"class": "backend", "stacks": {"python": 4}, "salary": {"min": 50000, "max": 200000}}
    for name in ("backend-05", "backend-06"):
        write(tmp_path / "families" / f"{name}.json", {"manifest": {"split": "validation",
              "preference": f"Prefer economical salary for {name}"}, "traits": traits, "vacancy": vacancy}, "record_hash")
    write(tmp_path / "diagnostic-frontend-05/pilot-manifest.json", {
        "split": "validation", "cases": [{"a": a, "b": b, "state": {
        "A": traits[a], "B": traits[b], "vacancy": vacancy, "preferences": "Prefer explicit fit"}}
        for a, b in pilot.SELECTION["frontend-05"]]}, "manifest_hash")
    write(tmp_path / "provider-cache/usage.json", {"generation": 28, "pair": 676, "grade": 119})
    key_file = tmp_path / "unit-key"
    key_file.write_text("sk-unit-gemma-pilot", encoding="utf-8")
    monkeypatch.setenv("RERANK_SYSTEMONE_KEY_FILE", str(key_file))
    return tmp_path


def review(directory):
    manifest = pilot.prepare_pilot(directory)
    mapping = {r["case_id"]: r["swapped"] for r in json.loads((directory / "diagnostic-gemma-teacher/blind-map.json").read_text())}
    cases = []
    for r in manifest["cases"]:
        label = "A" if r["state"]["A"]["salary_min"] < r["state"]["B"]["salary_min"] else "B"
        cases.append({"case_id": r["case_id"], "label": REVERSE[label] if mapping[r["case_id"]] else label})
    write(directory / "diagnostic-gemma-teacher/blind-review.json", {
        "packet_hash": manifest["blind_packet_hash"], "reviewer_kind": "assistant",
        "independent_human_evaluation": False, "cases": cases}, "review_hash")


def transport(monkeypatch, failure=False):
    from matching_engine.rerank_gemma_teacher import (
        GEMMA_TEACHER_MODEL,
        GemmaTeacherProviders,
    )

    calls = []

    def respond(_provider, request):
        calls.append(request)
        if failure:
            raise TimeoutError("secret echo must never appear")
        payload = json.loads(request.data)
        state = json.loads(payload["messages"][-1]["content"])
        answer = {"label": "A" if state["A"]["salary_min"] < state["B"]["salary_min"] else "B",
                  "rationale": "Explicit synthetic salary preference", "uncertainties": [],
                  "evidence": [{"candidate": side, "field": "salary_min", "value": state[side]["salary_min"],
                                "reason": "Known salary"} for side in ("A", "B")]}
        return io.BytesIO(json.dumps({"model": GEMMA_TEACHER_MODEL, "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(answer)}}], "usage": {"total_tokens": 42}}).encode())

    monkeypatch.setattr(GemmaTeacherProviders, "_open", respond)
    return calls


def forbid(monkeypatch):
    from matching_engine.rerank_gemma_teacher import GemmaTeacherProviders

    monkeypatch.setattr(GemmaTeacherProviders, "_open", lambda *_: pytest.fail("unexpected HTTP"))
    monkeypatch.setattr(GemmaTeacherProviders, "_api_key", lambda *_: pytest.fail("unexpected credential read"))


def test_prepare_is_blind_immutable_and_rejects_golden(source, monkeypatch):
    forbid(monkeypatch)
    manifest = pilot.prepare_pilot(source)
    packet = json.loads((source / "diagnostic-gemma-teacher/blind-packet.json").read_text())
    assert len(packet["cases"]) == 10
    assert all(set(r) == {"case_id", "vacancy", "preferences", "A", "B"} for r in packet["cases"])
    assert pilot.prepare_pilot(source) == manifest
    path = source / "families/backend-06.json"
    value = json.loads(path.read_text())
    del value["record_hash"]
    value["manifest"]["split"] = "golden"
    write(path, value, "record_hash")
    with pytest.raises(ValueError, match="train or golden"):
        pilot.prepare_pilot(source)


def test_twenty_attempts_and_replay_without_http(source, monkeypatch):
    review(source)
    calls = transport(monkeypatch)
    report = pilot.run_pilot(source)
    assert report["state"] == "needs_fact_review" and len(calls) == 20
    assert report["ledger_after"] == {"generation": 28, "pair": 696, "grade": 119}
    assert report["gates"]["symmetric"] == report["gates"]["blind_agreement"] == 10
    assert report["historical_cache_bytes_preserved"] and report["budget_preserved"]
    assert not report["activation_allowed"] and not report["training_started"]
    assert not report["factual_gate_passed"]
    forbid(monkeypatch)
    assert pilot.run_pilot(source) == report


def test_transport_failure_is_sealed_without_retry(source, monkeypatch):
    review(source)
    calls = transport(monkeypatch, failure=True)
    report = pilot.run_pilot(source)
    assert report["state"] == "stopped" and len(calls) == 5
    assert report["ledger_after"]["pair"] == 681
    assert report["error_type"] == "RuntimeError"
    assert "secret echo" not in json.dumps(report)
    forbid(monkeypatch)
    assert pilot.run_pilot(source) == report


def test_missing_review_and_exhausted_budget_stop_before_http(source, monkeypatch):
    forbid(monkeypatch)
    with pytest.raises(FileNotFoundError):
        pilot.run_pilot(source)
    review(source)
    write(source / "provider-cache/usage.json", {"pair": 2990})
    with pytest.raises(ValueError, match="remaining pair budget"):
        pilot.run_pilot(source)
    assert not (source / "diagnostic-gemma-teacher/started.json").exists()


def test_crash_blocks_implicit_retry(source, monkeypatch):
    from matching_engine.rerank_gemma_teacher import GemmaTeacherProviders

    review(source)

    def crash(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(GemmaTeacherProviders, "_open", crash)
    with pytest.raises(KeyboardInterrupt):
        pilot.run_pilot(source)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="interrupted pilot"):
        pilot.run_pilot(source)


def test_changed_response_hash_is_not_silently_replayed(source, monkeypatch):
    review(source)
    transport(monkeypatch)
    report = pilot.run_pilot(source)
    key = report["comparisons"][0]["cache_keys"][0]
    path = source / "provider-cache" / f"{key}.json"
    cached = json.loads(path.read_text())
    cached["response"]["usage"]["total_tokens"] += 1
    cached["response_hash"] = digest(cached["response"])
    write(path, cached)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="response changed|receipt differs"):
        pilot.run_pilot(source)


def test_truncated_batch_stops_after_five_and_replay_checks_rejected_responses(source, monkeypatch):
    from matching_engine.rerank_gemma_teacher import (
        GEMMA_TEACHER_MODEL,
        GemmaTeacherProviders,
    )

    review(source)
    calls = []

    def truncated(_provider, request):
        calls.append(request)
        return io.BytesIO(json.dumps({"model": GEMMA_TEACHER_MODEL, "choices": [{"finish_reason": "length",
                         "message": {"content": '{"label":"A"'}}]}).encode())

    monkeypatch.setattr(GemmaTeacherProviders, "_open", truncated)
    report = pilot.run_pilot(source)
    assert report["state"] == "stopped" and len(calls) == 5
    assert report["comparisons"] == [] and report["ledger_after"]["pair"] == 681
    forbid(monkeypatch)
    assert pilot.run_pilot(source) == report
    key = report["attempt_keys"][0]
    path = source / "provider-cache" / f"{key}.json"
    cached = json.loads(path.read_text())
    cached["response"]["choices"][0]["message"]["content"] = "modified"
    cached["response_hash"] = digest(cached["response"])
    write(path, cached)
    with pytest.raises(ValueError, match="receipt differs"):
        pilot.run_pilot(source)


def test_symmetry_does_not_replace_blind_agreement_or_decisive_coverage():
    rows = [{"symmetric": True, "agrees_with_blind": True, "blind_label": "A"} for _ in range(10)]
    assert pilot.evaluate_gates(rows)["passed"]
    for i in range(3):
        rows[i]["agrees_with_blind"] = False
    assert not pilot.evaluate_gates(rows)["passed"]
    abstained = deepcopy(rows)
    for row in abstained:
        row.update(blind_label="insufficient", agrees_with_blind=True)
    assert not pilot.evaluate_gates(abstained)["passed"]
