"""Recovery uses a fresh identity, counts failures and never invalidates old publications."""

import json
from copy import deepcopy

import pytest
import test_rerank_teacher_comparison as comparisons

from matching_engine.rerank_pair_teacher import (
    COMPATIBLE_PROMPT_VERSION,
    ComparativeTeacherProviders,
    CompatibleTeacherProviders,
)
from matching_engine.rerank_teacher_comparison import run_comparison
from matching_engine.rerank_transport_recovery import run_probe, run_recovery

source = comparisons.source
prepared = comparisons.prepared


def test_fresh_identity_preserves_sealed_eight_call_comparison(prepared, monkeypatch):
    comparisons.transport(monkeypatch, "http")
    previous = run_comparison(prepared)
    # The fixture simulates prior spend separately; keep five remaining for recovery.
    comparisons.parent_tests.write(prepared / "provider-cache/usage.json", {"generation": 28, "pair": 691, "grade": 119})
    result = run_probe(prepared)
    assert result["answer"] is None and result["ledger_after"]["pair"] == 692
    assert result["budget_preserved"] and result["historical_cache_bytes_preserved"]
    comparisons.forbid(monkeypatch)
    assert run_probe(prepared) == result
    assert run_comparison(prepared) == previous


def test_success_is_one_bounded_call_and_replays_without_key(prepared, monkeypatch):
    calls = comparisons.transport(monkeypatch)
    result = run_probe(prepared)
    assert len(calls) == 1 and result["answer"] is not None
    assert result["ledger_after"]["pair"] == 684 and result["budget_preserved"]
    before = (prepared / "provider-cache/usage.json").read_bytes()
    comparisons.forbid(monkeypatch)
    assert run_probe(prepared) == result and (prepared / "provider-cache/usage.json").read_bytes() == before


def test_completed_receipt_tamper_rejected(prepared, monkeypatch):
    comparisons.transport(monkeypatch)
    run_probe(prepared)
    manifest = json.loads((prepared / "diagnostic-http-compatibility-v1/manifest.json").read_text())
    path = prepared / "provider-cache" / f'{manifest["request_hash"]}.json'
    value = json.loads(path.read_text())
    value["elapsed_seconds"] += 1
    comparisons.parent_tests.write(path, value)
    comparisons.forbid(monkeypatch)
    with pytest.raises(ValueError, match="receipt changed"):
        run_probe(prepared)


def test_started_probe_has_no_implicit_retry(prepared, monkeypatch):
    def crash(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(ComparativeTeacherProviders, "_open", crash)
    with pytest.raises(KeyboardInterrupt):
        run_probe(prepared)
    comparisons.forbid(monkeypatch)
    with pytest.raises(ValueError, match="interrupted"):
        run_probe(prepared)


@pytest.mark.parametrize("used", [675, 696, 3000])
def test_budget_blocks_before_any_new_request(prepared, monkeypatch, used):
    comparisons.parent_tests.write(prepared / "provider-cache/usage.json", {"pair": used})
    comparisons.forbid(monkeypatch)
    with pytest.raises(ValueError, match="budget"):
        run_probe(prepared)


def test_portable_wire_schema_preserves_v3_and_runtime_duplicate_rejection(prepared, monkeypatch):
    import test_rerank_pair_teacher as facts

    old = ComparativeTeacherProviders(prepared / "provider-cache")
    compatible = CompatibleTeacherProviders(prepared / "provider-cache")
    args = (facts.VACANCY, facts.A, facts.B, facts.PREFERENCES)
    original = old._pair_payload(*args)
    portable = compatible._pair_payload(*args)
    expected = deepcopy(original)
    expected["response_format"]["json_schema"]["name"] = "synthetic_pair_comparative_v4_wire"
    for side in ("A", "B"):
        expected["response_format"]["json_schema"]["schema"]["properties"][f"evidence_{side}"].pop("uniqueItems")
    assert portable == expected and old._pair_payload(*args) == original
    assert compatible._identity("/v1/chat/completions", portable, "pair")["prompt_version"] == COMPATIBLE_PROMPT_VERSION
    answer = facts._answer()
    answer["evidence_A"] *= 2
    monkeypatch.setattr(compatible, "request", lambda *_: facts._response(compatible.model, answer))
    with pytest.raises(ValueError, match="duplicated"):
        compatible.judge(*args)


def test_four_calls_preserve_prior_probe_and_reports(prepared, monkeypatch):
    calls = comparisons.transport(monkeypatch)
    probe = run_probe(prepared)
    report = run_recovery(prepared)
    assert len(calls) == 5 and report["ledger_after"]["pair"] == 688
    assert report["state"] == "needs_fact_review" and report["budget_preserved"] and report["historical_cache_bytes_preserved"]
    assert all(r["accepted"] == 2 and r["symmetric"] and r["agrees_with_blind"] for r in report["models"].values())
    assert not report["quality_gate_passed"] and not report["training_started"]
    comparisons.forbid(monkeypatch)
    assert run_recovery(prepared) == report and run_probe(prepared) == probe


def test_recovery_stops_on_first_invalid_reply_with_no_retry(prepared, monkeypatch):
    calls = comparisons.transport(monkeypatch, "fact")
    report = run_recovery(prepared)
    assert len(calls) == 1 and report["state"] == "stopped"
    assert report["ledger_after"]["pair"] == 684
    assert all(r["symmetric"] is None for r in report["models"].values())
    comparisons.forbid(monkeypatch)
    assert run_recovery(prepared) == report


def test_recovery_requires_four_remaining_attempts(prepared, monkeypatch):
    comparisons.parent_tests.write(prepared / "provider-cache/usage.json", {"pair": 693})
    comparisons.forbid(monkeypatch)
    with pytest.raises(ValueError, match="budget"):
        run_recovery(prepared)


def test_recovery_receipt_tamper_rejected(prepared, monkeypatch):
    comparisons.transport(monkeypatch)
    report = run_recovery(prepared)
    path = prepared / "provider-cache" / f'{report["attempts"][0]["request_hash"]}.json'
    value = json.loads(path.read_text())
    value["elapsed_seconds"] += 1
    comparisons.parent_tests.write(path, value)
    comparisons.forbid(monkeypatch)
    with pytest.raises(ValueError, match="receipt changed"):
        run_recovery(prepared)


def test_budget_anomaly_never_reports_review_ready(prepared, monkeypatch):
    comparisons.transport(monkeypatch)
    original = CompatibleTeacherProviders.judge

    def changed_ledger(provider, *args):
        answer = original(provider, *args)
        path = prepared / "provider-cache/usage.json"
        usage = json.loads(path.read_text())
        usage["generation"] += 1
        comparisons.parent_tests.write(path, usage)
        return answer

    monkeypatch.setattr(CompatibleTeacherProviders, "judge", changed_ledger)
    report = run_recovery(prepared)
    assert all(a["answer"] for a in report["attempts"])
    assert not report["budget_preserved"] and report["state"] == "stopped"
