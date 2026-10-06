"""Explicit variants retain candidate facts and ancestry while requiring a new blind review."""

import json

import pytest
import test_rerank_grounded_pilot as pilot_tests
import test_rerank_teacher_comparison as previous

from matching_engine.rerank_explicit_cases import (
    prepare_explicit_cases,
    reviewed_explicit_cases,
)
from matching_engine.rerank_grounded_pilot import (
    prepare_grounded_pilot,
    run_grounded_pilot,
)

source = previous.source
prepared = previous.prepared


def new_review(directory):
    manifest = prepare_explicit_cases(directory)
    previous.parent_tests.write(directory / "diagnostic-explicit-policy-v1/blind-review.json", {
        "packet_hash": manifest["blind_packet_hash"], "reviewer_kind": "assistant", "independent_human_evaluation": False,
        "cases": [{"case_id": case["case_id"], "label": "insufficient"} for case in manifest["cases"]]}, "review_hash")


def test_variants_are_frozen_before_review_and_preserve_candidate_sources(prepared, monkeypatch):
    pilot_tests.forbid(monkeypatch)
    original = prepare_grounded_pilot(prepared)
    variant = prepare_explicit_cases(prepared)
    for source, case in zip(original["cases"], variant["cases"], strict=True):
        assert source["state"]["A"] == case["state"]["A"] and source["state"]["B"] == case["state"]["B"]
        assert source["split"] == case["split"] and source["family"] == case["family"]
        assert source["case_id"] != case["case_id"] and "blind_label" not in case
    assert prepare_explicit_cases(prepared) == variant
    assert prepare_grounded_pilot(prepared) == original
    with pytest.raises(FileNotFoundError):
        prepare_grounded_pilot(prepared, explicit_policy=True)


def test_explicit_requests_require_the_new_review_and_do_not_leak_labels(prepared, monkeypatch):
    pilot_tests.forbid(monkeypatch)
    original = prepare_grounded_pilot(prepared)
    new_review(prepared)
    variant = prepare_grounded_pilot(prepared, explicit_policy=True)
    assert {r["request_hash"] for r in variant["requests"]}.isdisjoint(r["request_hash"] for r in original["requests"])
    assert "blind_label" not in json.dumps([r["request"] for r in variant["requests"]])
    assert variant["global_limits"] == original["global_limits"]
    assert not variant["training_eligible"]


def test_blind_mapping_tamper_blocks_teacher(prepared, monkeypatch):
    prepare_grounded_pilot(prepared)
    new_review(prepared)
    path = prepared / "diagnostic-explicit-policy-v1/blind-map.json"
    rows = json.loads(path.read_text())
    rows[0]["swapped"] = not rows[0]["swapped"]
    previous.parent_tests.write(path, rows)
    pilot_tests.forbid(monkeypatch)
    with pytest.raises(ValueError, match="immutable diagnostic receipt differs"):
        reviewed_explicit_cases(prepared)


def test_new_receipts_replay_separately_from_old_diagnostic(prepared, monkeypatch):
    pilot_tests.transport(monkeypatch)
    old = run_grounded_pilot(prepared)
    new_review(prepared)
    new = run_grounded_pilot(prepared, explicit_policy=True)
    assert new["ledger_after"]["pair"] - old["ledger_after"]["pair"] == 20
    assert new["historical_cache_bytes_preserved"] and new["budget_preserved"]
    pilot_tests.forbid(monkeypatch)
    assert run_grounded_pilot(prepared) == old
    assert run_grounded_pilot(prepared, explicit_policy=True) == new
