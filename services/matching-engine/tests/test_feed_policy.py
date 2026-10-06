"""Golden checks for the deterministic candidate feed delta policy."""

from __future__ import annotations

from copy import deepcopy

import pytest
from matching_engine.feed_policy import assess, default_policy, policy_hash, snapshot


def profile(**overrides: object) -> dict[str, object]:
    """Build a small raw profile with every structural channel represented."""
    value: dict[str, object] = {
        "id": "candidate-a", "source_revision": 4, "display_name": "Private Name",
        "email": "private@example.invalid", "phone": "+70000000000", "city": " Москва ",
        "work_types": ["remote", "hybrid"], "relocation_ready": True,
        "relocation_cities": ["Казань", "Москва"], "salary": {"from_rub": 100_000, "to_rub": 180_000},
        "primary_direction": "backend", "grade_result": {"grade": "middle", "status": "approved"},
        "domains": [{"key": "algorithms", "level": 6, "status": "confirmed"}],
        "stacks": [{"key": "python", "level": 5, "status": "partial"}],
        "soft_skills": [{"key": "teamwork", "level": 4, "status": "self_reported"}],
        "projects": [{
            "id": "project-1", "title": "Private title", "description": "Private text", "status": "confirmed",
            "facts": {"domains": [{"key": "backend", "status": "confirmed"}],
                      "stacks": [{"key": "python", "status": "partial"}],
                      "duration_months": {"value": 12, "status": "confirmed"},
                      "role": {"value": "developer", "status": "confirmed"},
                      "production_support": {"value": True, "status": "confirmed"}},
        }],
        "experience": [], "achievements": [],
    }
    value.update(overrides)
    return value


def test_snapshot_is_text_free_and_order_invariant() -> None:
    """IDs, prose, contacts, and claim ordering do not affect the projection."""
    first = snapshot(profile())
    changed = profile(
        id="other-id", display_name="Other Name", email="other@example.invalid", phone="+79999999999",
        description="different prose",
        projects=[{"id": "other-project", "title": "Other title", "description": "Other text",
                   "status": "confirmed", "facts": profile()["projects"][0]["facts"]}],
    )
    assert snapshot(changed) == first
    assert "email" not in first and "description" not in first


def test_noop_and_removal_have_separate_positive_flag() -> None:
    """A noop is inert; removing a skill is a change but not a positive update."""
    base = snapshot(profile())
    noop = assess(base, snapshot(profile(description="new prose", id="new-id")))
    assert noop["distance"] == 0.0
    assert noop["changed"] is False
    assert noop["refresh"] is False
    assert noop["positive_professional"] is False

    removed = profile(domains=[])
    decision = assess(base, snapshot(removed))
    assert decision["changed"] is True
    assert decision["positive_professional"] is False
    assert decision["refresh"] is True


def test_exact_threshold_is_strictly_not_a_refresh() -> None:
    """The feed refresh predicate is N>K, including at the rounded boundary."""
    base = snapshot(profile())
    policy = default_policy()
    policy["group_weights"] = {**policy["group_weights"], "location": 1.0}
    decision = assess(base, snapshot(profile(city="Казань")), policy)
    assert decision["distance"] == 1.0
    assert decision["changed"] is True
    assert decision["refresh"] is False


def test_group_caps_bound_many_changes() -> None:
    """An arbitrary number of skill removals cannot bypass the skills cap."""
    base = snapshot(profile(
        domains=[{"key": f"domain-{index}", "level": 7, "status": "confirmed"} for index in range(10)],
        stacks=[{"key": f"stack-{index}", "level": 7, "status": "confirmed"} for index in range(10)],
        soft_skills=[{"key": f"soft-{index}", "level": 7, "status": "confirmed"} for index in range(10)],
    ))
    decision = assess(base, snapshot(profile()))
    assert decision["groups"]["skills"]["capped"] == 2.0
    assert len(decision["explanations"]) == 8


def test_confidence_weights_reduce_uncertain_skill_delta() -> None:
    """The same declared level contributes less when its evidence is uncertain."""
    base = snapshot(profile(stacks=[]))
    uncertain = assess(base, snapshot(profile(stacks=[{"key": "go", "level": 7, "status": "self_reported"}])))
    certain = assess(base, snapshot(profile(stacks=[{"key": "go", "level": 7, "status": "confirmed"}])))
    assert certain["distance"] > uncertain["distance"]


def test_narrative_facts_are_deduplicated_and_duration_is_maximum() -> None:
    """Duplicate typed claims collapse, while independent facts remain structural."""
    facts = profile()["projects"][0]["facts"]
    first = snapshot(profile(projects=[
        {"id": "a", "title": "A", "status": "partial", "facts": facts},
        {"id": "b", "title": "B", "status": "confirmed", "facts": facts},
    ]))
    second = snapshot(profile(projects=[
        {"id": "different", "title": "Different", "status": "confirmed", "facts": facts},
    ]))
    assert first == second
    assert first["narrative"]["projects"]["count"] == 1
    assert first["narrative"]["projects"]["duration_months"] == {"value": 12, "status": "confirmed"}


def test_narrative_replacement_is_not_positive() -> None:
    """Replacing one typed fact with another is a change without a positive signal."""
    base = snapshot(profile())
    replacement = profile(projects=[{"status": "confirmed", "facts": {
        "domains": [{"key": "frontend", "status": "confirmed"}],
        "duration_months": {"value": 12, "status": "confirmed"},
    }}])
    decision = assess(base, snapshot(replacement))
    assert decision["changed"] is True
    assert decision["positive_professional"] is False


def test_rejected_evidence_never_becomes_positive() -> None:
    """A higher declared level with rejected evidence does not signal improvement."""
    base = snapshot(profile(stacks=[]))
    rejected_skill = snapshot(profile(stacks=[{"key": "go", "level": 7, "status": "rejected"}]))
    decision = assess(base, rejected_skill)
    assert decision["changed"] is True
    assert decision["positive_professional"] is False

    confirmed = snapshot(profile(projects=[{"status": "confirmed", "facts": {
        "domains": [{"key": "backend", "status": "confirmed"}],
        "duration_months": {"value": 12, "status": "confirmed"},
    }}]))
    rejected = snapshot(profile(projects=[{"status": "confirmed", "facts": {
        "domains": [{"key": "backend", "status": "rejected"}],
        "duration_months": {"value": 12, "status": "confirmed"},
    }}]))
    assert assess(confirmed, rejected)["positive_professional"] is False


def test_grade_upgrade_exceeds_default_threshold_and_uses_last_baseline() -> None:
    """Grade distance is direct from the stored baseline and one step is material."""
    base = snapshot(profile(grade_result={"grade": "junior", "status": "self_reported"}))
    current = snapshot(profile(grade_result={"grade": "middle", "status": "self_reported"}))
    decision = assess(base, current)
    assert decision["distance"] > 1.0
    assert decision["refresh"] is True
    assert decision["distance"] == assess(base, current)["distance"]


def test_unknown_snapshot_version_fails_closed() -> None:
    """Workers cannot silently compare a future structural snapshot."""
    base = snapshot(profile())
    future = deepcopy(base)
    future["version"] = 99
    with pytest.raises(ValueError, match="unsupported feed snapshot version"):
        assess(base, future)


def test_policy_hash_freezes_every_policy_field() -> None:
    """Policy fingerprints are key-order invariant and change on any setting."""
    policy = default_policy()
    reordered = {key: policy[key] for key in reversed(list(policy))}
    assert policy_hash(policy) == policy_hash(reordered)
    changed = deepcopy(policy)
    changed["threshold"] = 1.1
    assert policy_hash(policy) != policy_hash(changed)


def test_policy_validation_rejects_unknown_version_and_incomplete_confidence() -> None:
    """A worker fails closed when the persisted policy contract is incomplete."""
    base = snapshot(profile())
    future = default_policy()
    future["version"] = "feed-delta-v2"
    with pytest.raises(ValueError, match="unsupported feed policy version"):
        assess(base, base, future)
    incomplete = default_policy()
    incomplete["confidence_weights"] = {"confirmed": 1.0}
    with pytest.raises(ValueError, match="invalid feed policy confidence weights"):
        assess(base, base, incomplete)


def test_project_improvement_sets_directional_flag_without_achievements() -> None:
    baseline = snapshot({})
    current = snapshot({"projects": [{"status": "self_reported", "facts": {"duration_months": {"value": 12, "status": "self_reported"}}}]})
    assert assess(baseline, current)["positive_professional"] is True


def test_rejected_narrative_addition_is_not_professional_improvement() -> None:
    current = snapshot({"projects": [{"status": "rejected", "facts": {
        "duration_months": {"value": 12, "status": "rejected"},
        "stacks": [{"key": "python", "status": "rejected"}],
    }}]})
    assert assess(snapshot({}), current)["positive_professional"] is False


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_untrusted_values_are_ignored(invalid: float) -> None:
    projection = snapshot({"salary": {"from_rub": invalid, "to_rub": invalid},
                           "stacks": [{"key": "python", "level": invalid, "status": "self_reported"}]})
    assert projection["salary"] == {"from_rub": None, "to_rub": None}
    assert projection["skills"]["stacks"] == []


def test_confirmed_claim_does_not_override_rejected_typed_facts() -> None:
    current = snapshot({"projects": [{"status": "confirmed", "facts": {
        "duration_months": {"value": 12, "status": "rejected"},
        "stacks": [{"key": "python", "status": "rejected"}],
    }}]})
    assert assess(snapshot({}), current)["positive_professional"] is False


def test_reference_evidence_upgrade_is_professionally_positive() -> None:
    old = {"projects": [{"status": "self_reported", "facts": {
        "stacks": [{"key": "python", "status": "self_reported"}]}}]}
    new = deepcopy(old)
    new["projects"][0]["facts"]["stacks"][0]["status"] = "confirmed"
    assert assess(snapshot(old), snapshot(new))["positive_professional"] is True
