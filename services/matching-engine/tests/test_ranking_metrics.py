"""Hand-computed metric regression and explicit missing-label/uncertainty semantics."""
import pytest
from matching_engine.ranking_metrics import evaluate_rankings, ranking_metrics


def test_exact_metrics_and_short_list_denominator():
    result = ranking_metrics([0, 3, 2, 0], [4, 3, 2, 1], k=3)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == 1
    assert result["reciprocal_rank"] == .5
    assert result["average_precision"] == pytest.approx((.5 + 2 / 3) / 2)
    assert 0 < result["ndcg"] < 1
    assert ranking_metrics([3], [1], k=10)["precision"] == .1


def test_missing_and_zero_relevance_are_explicit():
    assert ranking_metrics([None, 3], [2, 1], k=2)["precision"] is None
    assert ranking_metrics([None, 3], [2, 1], k=2)["judged_coverage"] == .5
    empty = ranking_metrics([0, 0], [2, 1], k=2)
    assert empty["precision"] == 0 and empty["recall"] is None
    assert empty["average_precision"] is None and empty["ndcg"] is None
    with pytest.raises(ValueError):
        ranking_metrics([4], [1], k=1)
    with pytest.raises(ValueError):
        ranking_metrics([3], [float("nan")], k=1)


def test_macro_bootstrap_slices_are_deterministic_and_paired():
    rows = [{"family_id": str(i), "direction": "backend", "grades": [0, 3],
             "baseline": [2, 1], "model": [1, 2]} for i in range(3)]
    report = evaluate_rankings(rows, cutoffs=(1, 5))
    assert report == evaluate_rankings(rows, cutoffs=(1, 5))
    assert report["aggregate"]["1"]["precision"]["paired_delta"]["ci95"] == [1, 1]
    assert report["slices"]["backend"]["supported"] is True
    assert report["protocol"]["real_hiring_outcomes"] is False
