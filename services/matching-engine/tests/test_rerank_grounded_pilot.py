"""Bounded fresh diagnostics preserve old limits and report all valid/invalid denominators."""

import io
import json
from urllib.error import HTTPError

import pytest
import test_rerank_teacher_comparison as previous

from matching_engine.rerank_grounded_pilot import (
    prepare_grounded_pilot,
    run_grounded_pilot,
)
from matching_engine.rerank_grounded_teacher import GroundedTeacherProviders

source = previous.source
prepared = previous.prepared


def transport(monkeypatch, mode=None):
    calls = []

    def respond(_provider, request):
        payload = json.loads(request.data)
        state = json.loads(payload["messages"][-1]["content"])
        calls.append(state)
        first_failure = mode == "one_invalid" and len(calls) == 1
        if mode == "network":
            raise TimeoutError("sk-unit-secret")
        answer = {"label": state["salary_comparison"]["cheaper_side"],
                  "evidence_A": [next(x for x in state["available_citations"]["A"] if ".status[" in x),
                                 next(x for x in state["available_citations"]["A"] if ".salary_min[" in x)],
                  "evidence_B": [next(x for x in state["available_citations"]["B"] if ".status[" in x),
                                 next(x for x in state["available_citations"]["B"] if ".salary_min[" in x)],
                  "unknown_A": [], "unknown_B": [], "reason_code": "prefer_known_facts",
                  "salary_comparison_choice": state["salary_comparison"]["cheaper_side"]}
        if first_failure:
            answer["evidence_A"][1] += "0"
        if mode == "asymmetric":
            answer["label"] = "A"
        return io.BytesIO(json.dumps({"model": payload["model"], "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 12, "completion_tokens": 30,
                                                                   "total_tokens": 42}}).encode())

    monkeypatch.setattr(GroundedTeacherProviders, "_open", respond)
    return calls


def forbid(monkeypatch):
    monkeypatch.setattr(GroundedTeacherProviders, "_open", lambda *_: pytest.fail("unexpected HTTP"))
    monkeypatch.setattr(GroundedTeacherProviders, "_api_key", lambda *_: pytest.fail("unexpected credential read"))


def test_frozen_requests_are_score_blind_and_roles_explicit(prepared, monkeypatch):
    forbid(monkeypatch)
    manifest = prepare_grounded_pilot(prepared)
    assert len(manifest["requests"]) == 20 and len(manifest["cases"]) == 10
    assert manifest["maximum_attempts"] == 20 and manifest["global_limits"]["pair"] == 3000
    assert all(c["split"] in ("validation", "calibration") and c["role"].startswith("diagnostic_") for c in manifest["cases"])
    assert "blind_label" not in json.dumps([r["request"] for r in manifest["requests"]])
    assert not manifest["training_eligible"] and not manifest["activation_eligible"]


def test_new_twenty_call_scope_does_not_reopen_old_ceiling(prepared, monkeypatch):
    previous.parent_tests.write(prepared / "provider-cache/usage.json", {"generation": 28, "pair": 696, "grade": 119})
    calls = transport(monkeypatch)
    report = run_grounded_pilot(prepared)
    assert len(calls) == 20 and report["ledger_after"] == {"generation": 28, "pair": 716, "grade": 119}
    assert report["budget_preserved"] and report["historical_cache_bytes_preserved"]
    assert report["gates"]["passed"] and report["gates"]["symmetric_pairs"] == 10
    assert report["gates"]["expected_decisive"] == 10 and report["gates"]["valid_decisive"] == 10
    assert not report["training_started"] and not report["teacher_promoted"] and not report["activation_allowed"]
    before = (prepared / "provider-cache/usage.json").read_bytes()
    forbid(monkeypatch)
    assert run_grounded_pilot(prepared) == report and (prepared / "provider-cache/usage.json").read_bytes() == before


def test_single_invalid_does_not_abort_remaining_calls(prepared, monkeypatch):
    calls = transport(monkeypatch, "one_invalid")
    report = run_grounded_pilot(prepared)
    assert len(calls) == 20 and report["gates"]["invalid_responses"] == 1
    assert report["gates"]["valid_pairs"] == 9 and not report["fatal_transport_failure"]
    assert report["gates"]["expected_decisive"] == 10 and report["gates"]["valid_decisive"] == 9
    assert report["gates"]["decisive_agreement_expected_fraction"] == .9
    assert report["gates"]["valid_only_decisive_agreement_fraction"] == 1


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("schema_error", [False, True])
def test_only_classified_schema_client_errors_abort_batch(prepared, monkeypatch, status, schema_error):
    calls = []

    def respond(_provider, _request):
        calls.append(True)
        body = b'{"error":"JSON schema rejected: uniqueItems is unsupported"}' if schema_error else b'{"error":"invalid request argument"}'
        raise HTTPError("https://unit.invalid", status, "synthetic", {}, io.BytesIO(body))

    monkeypatch.setattr(GroundedTeacherProviders, "_open", respond)
    report = run_grounded_pilot(prepared)
    assert report["fatal_transport_failure"] is schema_error
    assert len(calls) == (5 if schema_error else 20)
    assert report["ledger_after"]["pair"] - report["ledger_before"].get("pair", 0) == len(calls)
    assert report["gates"]["invalid_responses"] == len(calls)


@pytest.mark.parametrize("mode", ["network", "asymmetric"])
def test_failed_quality_is_reported_without_secret_text_or_fake_accuracy(prepared, monkeypatch, mode):
    calls = transport(monkeypatch, mode)
    report = run_grounded_pilot(prepared)
    assert len(calls) == 20 and not report["gates"]["passed"]
    assert "sk-unit-secret" not in json.dumps(report)
    if mode == "network":
        assert report["gates"]["valid_only_decisive_agreement_fraction"] is None
        assert all(row["symmetric"] is None for row in report["gates"]["pairs"])


def test_global_budget_blocks_before_any_request(prepared, monkeypatch):
    previous.parent_tests.write(prepared / "provider-cache/usage.json", {"pair": 2981})
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="global pair budget"):
        run_grounded_pilot(prepared)


def test_started_run_requires_explicit_recovery(prepared, monkeypatch):
    frozen = prepare_grounded_pilot(prepared)
    previous.parent_tests.write(prepared / "diagnostic-grounded-qwen-v5/started.json", {"manifest_hash": frozen["manifest_hash"]})
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="interrupted grounded"):
        run_grounded_pilot(prepared)


def test_raw_elapsed_receipt_tamper_rejected(prepared, monkeypatch):
    transport(monkeypatch)
    report = run_grounded_pilot(prepared)
    path = prepared / "provider-cache" / f'{report["attempts"][0]["request_hash"]}.json'
    value = json.loads(path.read_text())
    value["elapsed_seconds"] += 1
    previous.parent_tests.write(path, value)
    forbid(monkeypatch)
    with pytest.raises(ValueError, match="cached receipt changed"):
        run_grounded_pilot(prepared)
