"""Promotion security, mathematical inversion bound and live rerank integration."""
from datetime import UTC, datetime
from random import Random

import pytest
from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import select_candidates
from matching_engine.fsp_boost import (
    FSP_BOOST_MAX,
    PromotionPolicy,
    compute_fsp_boost,
    compute_weekly_boost,
    promote,
    promotion_policy,
    promotion_explanation,
    weekly_activity_deadline,
)
from matching_engine.models import ExternalEvidence
from matching_engine.rerank_model import RerankScore, rerank_cards, tail_policy_hash
from matching_engine.strict import initial_schema
from test_combine_scoring import candidate, vacancy


def evidence(*, status="confirmed", demo=False, rating=2000):
    return ExternalEvidence(kind="fsp", subject="synthetic", status=status,
        verified_value={"rank": "MS", "rating": rating, "demo_registry": demo})


@pytest.mark.parametrize("status", ["partial", "pending", "self_reported", "rejected"])
def test_unverified_fsp_never_earns_boost(status):
    assert compute_fsp_boost(candidate(external_evidence=[evidence(status=status)]))["bonus"] == 0


def test_absent_demo_invalid_and_duplicate_facts():
    assert compute_fsp_boost(candidate())["bonus"] == 0
    demo = candidate(external_evidence=[evidence(demo=True)])
    assert compute_fsp_boost(demo)["bonus"] == 0
    assert compute_fsp_boost(demo, allow_demo=True)["bonus"] > 0
    partial_demo = candidate(external_evidence=[evidence(status="partial", demo=True)])
    assert compute_fsp_boost(partial_demo)["bonus"] == 0
    assert compute_fsp_boost(partial_demo, allow_demo=True)["bonus"] > 0
    for value in (True, "2000", -1, 0):
        assert compute_fsp_boost(candidate(external_evidence=[evidence(rating=value)]))["bonus"] == 0
    one = compute_fsp_boost(candidate(external_evidence=[evidence()]))["bonus"]
    assert 0 < one <= FSP_BOOST_MAX
    assert compute_fsp_boost(candidate(external_evidence=[evidence(), evidence()]))["bonus"] == one


def test_runtime_policy_and_generation_identity(monkeypatch):
    monkeypatch.setenv("MATCHING_PROMOTION_MODE", "shadow")
    before = tail_policy_hash(None)
    monkeypatch.setenv("MATCHING_PROMOTION_MODE", "active")
    assert tail_policy_hash(None) != before
    monkeypatch.setenv("MATCHING_PROMOTION_ALLOW_DEMO", "true")
    monkeypatch.setenv("APP_ENV", "production")
    assert not promotion_policy().allow_demo
    monkeypatch.setenv("APP_ENV", "development")
    assert promotion_policy().allow_demo
    monkeypatch.setenv("MATCHING_PROMOTION_MODE", "typo")
    with pytest.raises(ValueError):
        promotion_policy()


def test_weekly_reward_bound_and_expiry():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    fact = ExternalEvidence(kind="weekly_challenge", subject="current", status="confirmed", verified_value={
        "provider": "comit_weekly_challenge:v1", "completed_at": "2026-10-06T00:00:00Z", "expires_at": "2026-10-12T00:00:00Z"})
    profile = candidate(external_evidence=[fact, fact])
    assert compute_weekly_boost(profile, now=now) == .005
    assert compute_weekly_boost(profile, now=datetime(2026, 10, 12, tzinfo=UTC)) == 0
    assert compute_weekly_boost(profile, now=datetime(2026, 10, 5, tzinfo=UTC)) == 0
    fact.status = "self_reported"
    assert compute_weekly_boost(profile, now=now) == 0


def test_activity_deadline_is_server_earned_and_independent_of_rollout(monkeypatch):
    import matching_engine.fsp_boost as boost
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 11, 12, tzinfo=UTC)
    monkeypatch.setattr(boost, "datetime", FixedDateTime)
    fact = ExternalEvidence(kind="weekly_challenge", subject="current", status="confirmed", verified_value={
        "provider": "comit_weekly_challenge:v1", "completed_at": "2026-10-06T00:00:00Z", "expires_at": "2026-10-12T00:00:00Z"})
    for mode in ("off", "shadow", "active"):
        explanation = promotion_explanation(candidate(external_evidence=[fact]), PromotionPolicy(mode=mode))
        assert explanation.weekly_bonus == .005
    assert weekly_activity_deadline(candidate(external_evidence=[fact])) == datetime(2026, 10, 12, tzinfo=UTC)
    assert weekly_activity_deadline(candidate()) is None
    fact.status = "rejected"
    assert weekly_activity_deadline(candidate(external_evidence=[fact])) is None


def test_actual_rerank_fallback_promotion_keeps_scores_and_strict_exclusions(monkeypatch):
    a, b, excluded = candidate(rating_score=.8), candidate(rating_score=.795, external_evidence=[evidence()]), candidate(salary={"from_rub": 900000, "to_rub": 1000000}, external_evidence=[evidence()])
    target, policy = vacancy(), ScoringPolicy()
    candidates = [a, b, excluded]
    cards = select_candidates(target, candidates, policy, 50).items
    assert len(cards) == 2
    original = [c.model_dump() for c in cards]
    monkeypatch.setenv("MATCHING_PROMOTION_MODE", "shadow")
    shadow = rerank_cards(target, candidates, cards, policy, None, initial_schema())
    assert [r.candidate_id for r in shadow.items] == [a.candidate_id, b.candidate_id]
    monkeypatch.setenv("MATCHING_PROMOTION_MODE", "active")
    active = rerank_cards(target, candidates, cards, policy, None, initial_schema())
    assert [r.candidate_id for r in active.items] == [b.candidate_id, a.candidate_id]
    assert original == [c.model_dump() for c in cards]
    assert active.items[0].score is None


def test_all_new_inversions_respect_bound_and_kind_for_random_orders():
    rng = Random(24)
    for _ in range(80):
        candidates = [candidate(rating_score=rng.random(), external_evidence=[evidence()] if rng.random() < .4 else []) for _ in range(25)]
        cards = select_candidates(vacancy(), candidates, ScoringPolicy(), 50).items
        rng.shuffle(cards)  # R0 may disagree with the main ordering.
        rows = [RerankScore(c.candidate_id, None, {}, "recommended" if i < 20 else "reserve") for i, c in enumerate(cards)]
        result = promote(rows, candidates, cards, PromotionPolicy(mode="active"))
        after = {row.candidate_id: i for i, row in enumerate(result)}
        assert set(after) == {row.candidate_id for row in rows}
        assert [r.kind for r in result] == [r.kind for r in rows]
        for i, a in enumerate(cards):
            for b in cards[i + 1:]:
                if after[a.candidate_id] > after[b.candidate_id]:
                    assert abs(a.explanation.score - b.explanation.score) <= .02


def test_no_fsp_and_equal_bonus_are_exact_order_noops():
    for facts in ([], [evidence()]):
        candidates = [candidate(external_evidence=facts) for _ in range(5)]
        cards = select_candidates(vacancy(), candidates, ScoringPolicy(), 50).items
        rows = [RerankScore(c.candidate_id, None, {}, "recommended") for c in reversed(cards)]
        assert promote(rows, candidates, cards, PromotionPolicy(mode="active")) == rows
