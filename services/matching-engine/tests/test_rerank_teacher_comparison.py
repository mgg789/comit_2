"""Comparison receipt integrity, shared budgets, failure accounting and cache-only replay."""

import io
import json
from urllib.error import HTTPError

import pytest
import test_rerank_gemma_pilot as parent_tests

from matching_engine.rerank_gemma_pilot import prepare_pilot
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import ComparativeTeacherProviders
from matching_engine.rerank_teacher_comparison import prepare_comparison, run_comparison

source = parent_tests.source


@pytest.fixture
def prepared(source):
    parent_tests.review(source)
    manifest = prepare_pilot(source)
    review = json.loads((source / "diagnostic-gemma-teacher/blind-review.json").read_text())
    parent_tests.write(source / "diagnostic-gemma-teacher/report.json", {
        "manifest_hash": manifest["manifest_hash"], "review_hash": review["review_hash"],
        "ledger_before": {"pair": 676}, "ledger_after": {"pair": 681}}, "report_hash")
    parent_tests.write(source / "provider-cache/usage.json", {"generation": 28, "pair": 683, "grade": 119})
    return source


def transport(monkeypatch, mutation=None):
    calls = []

    def respond(_provider, request):
        payload = json.loads(request.data)
        state = json.loads(payload["messages"][-1]["content"])
        calls.append((payload["model"], state))
        if mutation == "network":
            raise TimeoutError("sk-unit-secret-do-not-echo")
        if mutation == "http":
            raise HTTPError("https://invalid/sk-unit-secret", 400, "sk-unit-secret", {},
                            io.BytesIO(b"sk-unit-secret-body"))
        answer = {"label": state["salary_comparison"]["cheaper_side"], "rationale": "Known explicit preference",
                  "evidence_A": [next(c for c in state["available_citations"]["A"] if ".salary_min[" in c)],
                  "evidence_B": [next(c for c in state["available_citations"]["B"] if ".salary_min[" in c)],
                  "salary_comparison_choice": state["salary_comparison"]["cheaper_side"], "uncertainties": []}
        if mutation == "fact":
            answer["salary_comparison_choice"] = "equal"
        elif mutation == "asymmetric":
            answer["label"] = "A"
        return io.BytesIO(json.dumps({"model": payload["model"], "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 12, "completion_tokens": 30,
                                                                   "total_tokens": 42}}).encode())

    monkeypatch.setattr(ComparativeTeacherProviders, "_open", respond)
    return calls


def forbid(monkeypatch):
    monkeypatch.setattr(ComparativeTeacherProviders, "_open", lambda *_: pytest.fail("unexpected HTTP"))
    monkeypatch.setattr(ComparativeTeacherProviders, "_api_key", lambda *_: pytest.fail("unexpected credential read"))


def test_requests_freeze_identical_facts_and_no_teacher_labels(prepared, monkeypatch):
    forbid(monkeypatch)
    frozen = prepare_comparison(prepared)
    assert len(frozen["requests"]) == 8 and len({r["request_hash"] for r in frozen["requests"]}) == 8
    assert all(c["split"] == "validation" for c in frozen["cases"])
    for offset in (0, 4):
        rows = frozen["requests"][offset:offset + 4]
        assert rows[0]["request"]["payload"]["messages"] == rows[2]["request"]["payload"]["messages"]
        assert rows[1]["request"]["payload"]["messages"] == rows[3]["request"]["payload"]["messages"]
        assert "blind_label" not in json.dumps([r["request"] for r in rows])
    assert prepare_comparison(prepared) == frozen


def test_eight_calls_and_verified_zero_http_replay(prepared, monkeypatch):
    calls = transport(monkeypatch)
    result = run_comparison(prepared)
    assert len(calls) == 8 and result["ledger_after"] == {"generation": 28, "pair": 691, "grade": 119}
    assert result["diagnostic_attempts_remaining"] == 5 and result["budget_preserved"]
    assert result["historical_cache_bytes_preserved"]
    assert all(m["mechanical_gate_passed"] and m["observed_usage"]["total_tokens"] == 168 for m in result["models"].values())
    assert not result["training_started"] and not result["activation_allowed"] and not result["quality_gate_passed"]
    before = (prepared / "provider-cache/usage.json").read_bytes()
    forbid(monkeypatch)
    assert run_comparison(prepared) == result
    assert (prepared / "provider-cache/usage.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["fact", "network", "http", "asymmetric"])
def test_failures_are_counted_and_never_retried(prepared, monkeypatch, mutation):
    calls = transport(monkeypatch, mutation)
    result = run_comparison(prepared)
    assert len(calls) == 8 and result["ledger_after"]["pair"] == 691
    assert not any(m["mechanical_gate_passed"] for m in result["models"].values())
    assert "sk-unit-secret" not in json.dumps(result)
    if mutation == "http":
        assert all(r["raw_receipt"]["failures"][0]["http_status"] == 400 for r in result["attempts"])
    forbid(monkeypatch)
    assert run_comparison(prepared) == result


@pytest.mark.parametrize("used", [675, 689, 2995])
def test_original_and_shared_budget_preflight(prepared, monkeypatch, used):
    parent_tests.write(prepared / "provider-cache/usage.json", {"pair": used})
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="remaining diagnostic budget"):
        run_comparison(prepared)
    assert not (prepared / "diagnostic-teacher-comparison-v3/started.json").exists()


def test_started_run_requires_operator_review(prepared, monkeypatch):
    frozen = prepare_comparison(prepared)
    parent_tests.write(prepared / "diagnostic-teacher-comparison-v3/started.json", {"manifest_hash": frozen["manifest_hash"]})
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="interrupted comparison"):
        run_comparison(prepared)


def test_rehashed_response_tamper_still_rejected(prepared, monkeypatch):
    transport(monkeypatch)
    result = run_comparison(prepared)
    key = result["attempts"][0]["request_hash"]
    path = prepared / "provider-cache" / f"{key}.json"
    row = json.loads(path.read_text())
    row["response"]["usage"]["total_tokens"] += 1
    row["response_hash"] = digest(row["response"])
    parent_tests.write(path, row)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="cached response changed"):
        run_comparison(prepared)


def test_blind_map_tamper_blocks_network(prepared, monkeypatch):
    path = prepared / "diagnostic-gemma-teacher/blind-map.json"
    rows = json.loads(path.read_text())
    rows[0]["swapped"] = not rows[0]["swapped"]
    parent_tests.write(path, rows)
    forbid(monkeypatch)
    with pytest.raises(ValueError):
        run_comparison(prepared)
