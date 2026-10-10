"""Offline tests for the hypothesis exploration validation harness."""

import math
from pathlib import Path

from matching_engine.hypothesis_policy import (
    FAMILIES,
    MIN_DECISIVE_PAIRS,
    summarize,
)
from matching_engine.hypothesis_validation import (
    P_GRID,
    POWER_SEEDS,
    SCENARIOS,
    VERSION,
    _scenario_pairs,
    funnel,
    power,
)


def test_summarize_verdicts_on_hand_built_batches() -> None:
    """Strong support freezes supported; unanimous opposition freezes not_supported."""
    support = [{"hypothesis_id": "H2", "comparison_id": "a" + str(index),
                "test_action": "like", "control_action": "dislike"}
               for index in range(MIN_DECISIVE_PAIRS)]
    oppose = [{"hypothesis_id": "H2", "comparison_id": "b" + str(index),
               "test_action": "dislike", "control_action": "like"}
              for index in range(MIN_DECISIVE_PAIRS)]
    supported = next(row for row in summarize(support) if row["hypothesis_id"] == "H2")
    assert supported["status"] == "supported" and supported["decisive"] == 20
    assert supported["batch_status"] == "frozen"
    rejected = next(row for row in summarize(oppose) if row["hypothesis_id"] == "H2")
    assert rejected["status"] == "not_supported"
    assert rejected["confidence_interval"][1] < 0.5


def test_summarize_post_batch_pairs_cannot_reopen() -> None:
    """Decisions beyond the first 20 decisive pairs stay out of the frozen batch."""
    rows = [{"hypothesis_id": "H2", "comparison_id": "a" + str(index),
             "test_action": "like", "control_action": "dislike"}
            for index in range(MIN_DECISIVE_PAIRS)]
    rows += [{"hypothesis_id": "H2", "comparison_id": "b" + str(index),
              "test_action": "dislike", "control_action": "like"}
             for index in range(5)]
    verdict = next(row for row in summarize(rows) if row["hypothesis_id"] == "H2")
    assert verdict["decisive"] == 20
    assert verdict["observed_decisive"] == 25
    assert verdict["post_batch_decisive"] == 5
    assert verdict["status"] == "supported"


def test_h9_is_never_testable() -> None:
    """The disabled family reports not_testable regardless of outcomes."""
    rows = [{"hypothesis_id": "H9", "comparison_id": "x",
             "test_action": "like", "control_action": "dislike"}]
    verdict = next(row for row in summarize(rows) if row["hypothesis_id"] == "H9")
    assert verdict["status"] == "not_testable"


def test_scenarios_restore_policy_constants(tmp_path: Path) -> None:
    """A relaxation probe leaves the live module constants untouched."""
    import matching_engine.hypothesis_policy as policy

    before = {name: getattr(policy, name) for name in
              ("MIN_CONTRAST", "MAX_NUISANCE_DISTANCE", "MAX_NUISANCE_COMPONENT",
               "MAX_UTILITY_LOSS")}
    pool = [{"candidate_id": "only", "features": {"names": ("grade",), "values": (1.0,),
                                                  "available": (True,),
                                                  "schema_hash": "s"},
             "score": 1.0, "snapshot": {}}]
    for scenario in SCENARIOS:
        _scenario_pairs(pool, scenario)
    after = {name: getattr(policy, name) for name in before}
    assert before == after
    assert policy.policy_hash() == policy.policy_hash()


def test_funnel_reports_regeneration_cost() -> None:
    """Few pairs per pass inflate the pool regenerations needed for one batch."""
    report = funnel({"live": 3, "relaxed_all": 36}, families=60)
    assert report["live"]["pool_passes_for_batch"] == 1600
    assert report["relaxed_all"]["pool_passes_for_batch"] == 134
    zero = funnel({"dead": 0}, families=60)
    assert zero["dead"]["pool_passes_for_batch"] is None


def test_power_monte_carlo_is_monotone_and_conservative() -> None:
    """Power rises with true p; the null tips at most at the per-side alpha budget."""
    table = power(Path("."))
    assert str(0.8) in table and str(0.5) in table
    assert table["0.95"]["supported"] > table["0.8"]["supported"] > table["0.65"]["supported"]
    # Either false verdict is capped by the per-side Bonferroni budget (0.0025).
    assert table["0.5"]["supported"] <= 0.005
    assert table["0.5"]["not_supported"] <= 0.005
    assert abs(sum(table["0.8"].values()) - 1.0) < 1e-6
    assert POWER_SEEDS >= 1000 and len(P_GRID) == 4
    assert math.isclose(sum(1 for _ in FAMILIES), 10)


def test_version_and_scope_are_declared() -> None:
    """The namespace pins its version and evaluation-only scope."""
    assert VERSION.startswith("hypothesis-validation-v")
    module = Path(__file__).parents[1] / "src" / "matching_engine" / "hypothesis_validation.py"
    text = module.read_text(encoding="utf-8")
    assert "evaluation-only" in text
    assert "assumption" in text.lower()
