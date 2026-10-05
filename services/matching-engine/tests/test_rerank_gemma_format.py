"""Regression checks for homogeneous Gemma citations and bounded two-call replay."""

import io
import json
from copy import deepcopy

import pytest
import test_rerank_gemma_pilot as parent_tests

from matching_engine.rerank_gemma_format import (
    FORMAT_PROMPT_VERSION,
    SimpleGemmaTeacherProviders,
    run_format_probe,
    source_citations,
)
from matching_engine.rerank_gemma_pilot import prepare_pilot
from matching_engine.rerank_gemma_teacher import (
    GEMMA_TEACHER_MODEL,
    GemmaTeacherProviders,
)
from matching_engine.rerank_hash import digest

source = parent_tests.source


@pytest.fixture
def prepared(source):
    parent_tests.review(source)
    manifest = prepare_pilot(source)
    review = json.loads((source / "diagnostic-gemma-teacher/blind-review.json").read_text())
    parent_tests.write(source / "diagnostic-gemma-teacher/report.json", {
        "manifest_hash": manifest["manifest_hash"], "review_hash": review["review_hash"],
        "ledger_before": {"pair": 676}, "ledger_after": {"pair": 681}, "state": "stopped"}, "report_hash")
    parent_tests.write(source / "provider-cache/usage.json", {"generation": 28, "pair": 681, "grade": 119})
    return source


def transport(monkeypatch, mutation=None):
    calls = []

    def respond(_provider, request):
        payload = json.loads(request.data)
        state = json.loads(payload["messages"][-1]["content"])
        calls.append(state)
        if mutation == "network":
            raise TimeoutError("Authorization: sk-unit-secret-echo")
        evidence = [f"{side}.salary_min[int]={state[side]['salary_min']}" for side in ("A", "B")]
        answer = {"label": "A" if state["A"]["salary_min"] < state["B"]["salary_min"] else "B",
                  "rationale": "Lower stated salary within the range.", "evidence": evidence, "uncertainties": []}
        finish = "stop"
        if mutation == "type":
            answer["evidence"][0] = evidence[0].replace("[int]", "[str]")
        elif mutation == "fact":
            answer["evidence"][0] += "0"
        elif mutation == "duplicate":
            answer["evidence"][1] = evidence[0]
        elif mutation == "one_side":
            answer["evidence"][1] = next(c for c in state["available_citations"] if c.startswith("A.grade"))
        elif mutation == "truncated":
            finish = "length"
        elif mutation == "model":
            payload["model"] = "unapproved"
        elif mutation == "label":
            answer["label"] = "hire"
        return io.BytesIO(json.dumps({"model": payload["model"], "choices": [{"finish_reason": finish,
            "message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 12, "completion_tokens": 30,
                                                                   "total_tokens": 42}}).encode())

    monkeypatch.setattr(SimpleGemmaTeacherProviders, "_open", respond)
    return calls


def forbid(monkeypatch):
    monkeypatch.setattr(SimpleGemmaTeacherProviders, "_open", lambda *_: pytest.fail("unexpected HTTP"))
    monkeypatch.setattr(SimpleGemmaTeacherProviders, "_api_key", lambda *_: pytest.fail("unexpected key read"))


def test_homogeneous_citations_preserve_type_and_unknown_semantics():
    facts = source_citations({"grade": "middle", "levels": {"react": 3}, "duration_months": None,
                              "production_support": False}, {"grade": "junior", "levels": {"react": "3"}})
    assert facts["A.levels.react[int]=3"]["value"] == 3
    assert facts["B.levels.react[str]=3"]["value"] == "3"
    assert facts["A.duration_months[null]=null"]["value"] is None
    assert facts["A.production_support[bool]=false"]["value"] is False
    with pytest.raises(ValueError, match="primitive"):
        source_citations({"salary_min": float("nan")}, {})


def test_schema_has_no_mixed_primitive_evidence_and_v1_identity_is_preserved(prepared):
    manifest = prepare_pilot(prepared)
    state = manifest["cases"][0]["state"]
    args = (state["vacancy"], state["A"], state["B"], state["preferences"])
    old = GemmaTeacherProviders(prepared / "provider-cache")
    current = SimpleGemmaTeacherProviders(prepared / "provider-cache")
    before = old._pair_payload(*args)
    payload = current._pair_payload(*args)
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["evidence"]["items"]["type"] == "string"
    assert payload["model"] == GEMMA_TEACHER_MODEL and payload["max_tokens"] == 1400
    identity = current._identity("/v1/chat/completions", payload, "pair")
    assert identity["prompt_version"] == FORMAT_PROMPT_VERSION
    assert old._pair_payload(*args) == before
    assert digest(identity) != digest(old._identity("/v1/chat/completions", before, "pair"))


@pytest.mark.parametrize("mutation", ["type", "fact", "duplicate", "one_side", "truncated", "model", "label"])
def test_invalid_outputs_do_not_become_labels(prepared, monkeypatch, mutation):
    calls = transport(monkeypatch, mutation)
    report = run_format_probe(prepared)
    assert len(calls) == 2 and report["state"] == "stopped"
    assert report["valid_responses"] == 0 and report["symmetric"] is None
    assert report["ledger_after"]["pair"] == 683
    forbid(monkeypatch)
    assert run_format_probe(prepared) == report


def test_two_exact_swaps_and_cache_only_replay(prepared, monkeypatch):
    calls = transport(monkeypatch)
    report = run_format_probe(prepared)
    assert report["state"] == "format_passed" and report["valid_responses"] == 2
    assert report["symmetric"] is True and len(calls) == 2
    assert calls[0]["A"] == calls[1]["B"] and calls[0]["B"] == calls[1]["A"]
    assert {k: v for k, v in calls[0].items() if k not in ("A", "B", "available_citations")} == {
        k: v for k, v in calls[1].items() if k not in ("A", "B", "available_citations")}
    assert report["ledger_after"] == {"generation": 28, "pair": 683, "grade": 119}
    assert report["historical_cache_bytes_preserved"] and report["budget_preserved"]
    assert not report["quality_gate_passed"] and not report["training_started"] and not report["activation_allowed"]
    before = (prepared / "provider-cache/usage.json").read_bytes()
    forbid(monkeypatch)
    assert run_format_probe(prepared) == report
    assert before == (prepared / "provider-cache/usage.json").read_bytes()


def test_network_failures_are_sealed_without_extra_retries(prepared, monkeypatch):
    calls = transport(monkeypatch, "network")
    report = run_format_probe(prepared)
    assert len(calls) == 2 and report["ledger_after"]["pair"] == 683
    assert all(r["error_type"] == "RuntimeError" for r in report["attempts"])
    assert "sk-unit-secret-echo" not in json.dumps(report)
    forbid(monkeypatch)
    assert run_format_probe(prepared) == report


@pytest.mark.parametrize("pair_count", [695, 2999])
def test_shared_and_original_diagnostic_ceilings_block_before_http(prepared, monkeypatch, pair_count):
    forbid(monkeypatch)
    parent_tests.write(prepared / "provider-cache/usage.json", {"pair": pair_count})
    with pytest.raises(ValueError, match="remaining diagnostic budget"):
        run_format_probe(prepared)
    assert not (prepared / "diagnostic-gemma-format-v2/started.json").exists()


def test_crash_has_no_implicit_retry(prepared, monkeypatch):
    def crash(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(SimpleGemmaTeacherProviders, "_open", crash)
    with pytest.raises(KeyboardInterrupt):
        run_format_probe(prepared)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="interrupted format probe"):
        run_format_probe(prepared)


def test_rehashed_cache_tamper_is_rejected_by_published_raw_receipt(prepared, monkeypatch):
    transport(monkeypatch)
    report = run_format_probe(prepared)
    key = report["attempts"][0]["request_hash"]
    path = prepared / "provider-cache" / f"{key}.json"
    record = json.loads(path.read_text())
    record["response"]["usage"]["total_tokens"] += 1
    record["response_hash"] = digest(record["response"])
    parent_tests.write(path, record)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="cached response changed"):
        run_format_probe(prepared)


def test_blind_mapping_cannot_silently_change_agreement(prepared, monkeypatch):
    forbid(monkeypatch)
    path = prepared / "diagnostic-gemma-teacher/blind-map.json"
    rows = deepcopy(json.loads(path.read_text()))
    rows[0]["swapped"] = not rows[0]["swapped"]
    parent_tests.write(path, rows)
    with pytest.raises(ValueError, match="blind mapping invalid"):
        run_format_probe(prepared)
