"""Offline tests for the pre-registered hard benchmark namespace."""

import json
from pathlib import Path

import pytest
from matching_engine.rerank_cohort_provider import CohortProviders, validate_candidate
from matching_engine.rerank_hard_benchmark import (
    BENCHMARK_DIRECTIONS,
    FAMILY_SIZE,
    BenchmarkProviders,
    _difficulty,
    _isolation,
    _oracle_headroom,
    family_specs,
    pilot_cases,
)
from matching_engine.rerank_providers import LIMITS, BudgetExceeded


@pytest.mark.parametrize("pilot", [False, True])
def test_benchmark_ceiling_does_not_expand_shared_cohort_budget(tmp_path: Path, pilot: bool) -> None:
    """Benchmark grade720 is explicit, cumulative, and cannot leak into ordinary600 calls."""
    ledger = tmp_path / "usage.json"
    ledger.write_text(json.dumps({"grade": 600}), encoding="utf-8")
    ordinary = CohortProviders(tmp_path, cohort_id="cohort-budget-regression")
    benchmark = BenchmarkProviders(tmp_path, pilot=pilot)
    with pytest.raises(BudgetExceeded):
        ordinary._reserve("grade")
    assert benchmark._reserve("grade") == 601
    with pytest.raises(BudgetExceeded):
        ordinary._reserve("grade")
    ledger.write_text(json.dumps({"grade": 719}), encoding="utf-8")
    assert benchmark._reserve("grade") == 720
    with pytest.raises(BudgetExceeded):
        benchmark._reserve("grade")
    assert json.loads(ledger.read_text(encoding="utf-8"))["grade"] == 720
    assert LIMITS["grade"] == 600


def _candidate(grade: str, status: str, level: int, salary: int = 80000,
               description: str = "Разработчик с релевантным опытом поддержки сервисов") -> dict:
    return {"grade": grade, "status": status,
            "levels": {"python": level}, "skill_statuses": {"python": status},
            "salary_min": salary, "description": description,
            "structured_facts_available": False, "duration_months": None,
            "duration_status": None, "production_support": None,
            "production_status": None, "role": None, "role_status": None,
            "project_domains": [], "project_stacks": []}


def test_family_specs_are_frozen_and_consistent() -> None:
    """Twelve families across four directions with one policy per family."""
    specs = family_specs()
    assert len(specs) == 12
    assert len({spec["family_id"] for spec in specs}) == 12
    for spec in specs:
        assert spec["direction"] in BENCHMARK_DIRECTIONS
        assert spec["branch"] in ("mixed", "class_only", "mixed_alt", "stack_only", "domain_only", "domain_heavy")
        assert spec["vacancy"]["class"] == spec["direction"]
        assert spec["profile_count"] == FAMILY_SIZE
        if spec["branch"] == "class_only":
            assert spec["bio_absent"] is True
            assert spec["vacancy"]["stacks"] == {}
        elif spec["branch"] == "domain_only":
            assert spec["vacancy"]["domens"], "domain_only branches require explicit domens"
        else:
            assert (spec["vacancy"]["stacks"] or spec["vacancy"]["domens"]), \
                "required-key branches need explicit requirements"
        priorities = spec["policy"]["ordered_priorities"]
        assert priorities and all(isinstance(row, str) for row in priorities)


def test_pilot_cases_have_deterministic_expectations() -> None:
    """Sole-priority pairs decide or tie strictly; grades are fixed by the rubric."""
    cases = pilot_cases()
    assert len(cases) == 8
    for case in cases:
        validate_candidate(case["A"])
        validate_candidate(case["B"])
        assert case["expected"] in ("A", "tie")
        assert case["expected_grade"] in (1, 3)
        if case["case_id"].endswith("-sole"):
            assert case["A"]["levels"] != case["B"]["levels"]
        else:
            assert case["A"]["levels"] == case["B"]["levels"]


def test_difficulty_gate_rejects_easy_and_accepts_hard_families() -> None:
    """The preregistered gate counts close pairs, span and declared diversity."""
    scores = [0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25]
    candidates = [_candidate("junior", "confirmed", 4), _candidate("junior", "partial", 5),
                  _candidate("middle", "self_reported", 5), _candidate("middle", "confirmed", 6),
                  _candidate("senior", "partial", 6), _candidate("senior", "confirmed", 7),
                  _candidate("intern", "pending", 3)]
    gate = _difficulty(scores, candidates)
    assert gate["passed"] is True
    assert gate["close_pairs"] == 9
    easy = [0.05, 0.2, 0.4, 0.6, 0.75, 0.9, 0.99]
    assert _difficulty(easy, candidates)["passed"] is False
    same = [_candidate("middle", "confirmed", 5, description="Первый одинаковый профиль команды")
            for _ in range(7)]
    assert _difficulty(scores, same)["passed"] is False


def test_isolation_blocks_exact_collisions_with_base(tmp_path: Path) -> None:
    """Fact or description collisions against the base cohort are rejected."""
    base = [{"grade": "junior", "status": "confirmed", "levels": {"python": 4},
             "skill_statuses": {"python": "confirmed"}, "salary_min": 80000,
             "description": "Уникальное описание базового источника corpus",
             "structured_facts_available": False, "duration_months": None,
             "duration_status": None, "production_support": None,
             "production_status": None, "role": None, "role_status": None,
             "project_domains": [], "project_stacks": []}]
    records = [{"family_id": "backend-hard-1", "candidates": [_candidate(
        "junior", "confirmed", 4, description="Уникальное описание базового источника corpus")]}]
    report = _isolation(records, {"backend": base})
    assert report["passed"] is False
    assert report["duplicates"]
    fresh = [_candidate("junior", "confirmed", 5, salary=90000,
                        description="Совершенно новое описание для бенчмарка")]
    assert _isolation([{"family_id": "backend-hard-1", "candidates": fresh}],
                      {"backend": base})["passed"] is True


def test_oracle_headroom_is_bounded_by_unknown_grades() -> None:
    """Headroom equals one minus baseline NDCG and vanishes without known grades."""
    grades = [3, 2, 1, 0, 2, 1, 3]
    headroom = _oracle_headroom([0.9, 0.1, 0.3, 0.5, 0.2, 0.4, 0.8], grades)
    assert headroom is not None and 0.0 <= headroom <= 1.0
    assert _oracle_headroom([0.9, 0.1, 0.3, 0.5, 0.2, 0.4, 0.8], [None] * 7) is None


def test_version_and_generator_are_declared() -> None:
    """The namespace declares its version, generator provenance and evaluation-only use."""
    import matching_engine.rerank_hard_benchmark as module

    assert module.VERSION.startswith("rerank-hard-benchmark-")
    assert "subagent" in module.GENERATOR
    assert module.BENCHMARK_LIMITS["grade"] == 720
    assert module.BENCHMARK_LIMITS["pair"] == 3000
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "evaluation-only" in text
    assert json.dumps(module.family_specs(), ensure_ascii=False)
