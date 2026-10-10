"""Regression checks for bounded, observed-fact hypothesis comparisons."""

import math
from copy import deepcopy
from dataclasses import asdict

import pytest
from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import select_candidates
from matching_engine.embeddings import EMBEDDING_SPACE_ID
from matching_engine.hypothesis_policy import comparable_pairs, policy_hash, summarize
from matching_engine.models import Category
from matching_engine.rerank_features import build_features
from matching_engine.strict import initial_schema
from test_combine_scoring import candidate as matching_candidate
from test_combine_scoring import vacancy


def candidate(candidate_id, changes=None, *, missing=(), facts=(), score=.8):
    """Build explicit immutable feature inputs with known required coordinates."""
    values = {"grade": 2 / 3, "salary_affordability": .5, "semantic_cosine": .5,
              "project_relevance_evidence": .5, "experience_relevance_evidence": .5}
    for group in ("domain", "stack", "soft_skill"):
        values.update({f"{group}.{signal}": .5 for signal in
                       ("coverage", "proximity", "depth", "weakest", "balance")})
        values[f"{group}.confirmed_share"] = 1.0
    for group, key in (("domain", "backend"), ("stack", "python"), ("stack", "sql")):
        values.update({f"{group}.{key}.target": .5, f"{group}.{key}.level": .5,
                       f"{group}.{key}.confirmed": 1.0,
                       f"{group}.{key}.rejected": 0.0})
    values.update(changes or {})
    return {"candidate_id": candidate_id, "score": score,
            "features": {"names": list(values), "values": list(values.values()),
                         "available": [name not in missing for name in values],
                         "schema_hash": "fixture-same-vacancy-schema"},
            "snapshot": {"candidate_id": candidate_id, "semantic_space_id": EMBEDDING_SPACE_ID,
                         "grade_status": "tested", "narrative_facts": list(facts)}}


def hard(signal, value):
    """Change one normalized aggregate for both required hard groups."""
    return {f"{group}.{signal}": value for group in ("domain", "stack")}


def fact(duration=None, production=None, role=None, *, status="confirmed", kind="experience"):
    """Only typed, explicit values can establish duration or production observations."""
    facts = {"domains": [], "stacks": [{"key": "python", "status": "confirmed"}]}
    for name, value in (("duration_months", duration), ("production_support", production), ("role", role)):
        if value is not None:
            facts[name] = {"value": value, "status": "confirmed"}
    return {"kind": kind, "status": status, "facts": facts}


def family_pairs(family, pool, model=None):
    """Extract a family from the shared comparison policy."""
    return [pair for pair in comparable_pairs(pool, model) if pair["hypothesis_id"] == family]


def row(family="H1", test="like", control="dislike", **extra):
    """Represent an actual journal pair, including incomplete or same actions."""
    return {"hypothesis_id": family, "test_action": test, "control_action": control, **extra}


def state(outcomes, family="H1"):
    """Read one fixed family result."""
    return next(item for item in summarize(outcomes) if item["hypothesis_id"] == family)


@pytest.mark.parametrize(("family", "test_changes", "control_changes"), [
    ("H1", {"grade": 1 / 3, **hard("coverage", .9)}, {"grade": 1.0, **hard("coverage", .5)}),
    ("H2", {**hard("confirmed_share", .9), **hard("depth", .4)},
     {**hard("confirmed_share", .3), **hard("depth", .8)}),
    ("H3", {"domain.proximity": .9, "stack.proximity": .4},
     {"domain.proximity": .4, "stack.proximity": .9}),
    ("H4", {"stack.python.level": .9, **hard("weakest", .3)},
     {"stack.python.level": .6, **hard("weakest", .8)}),
    ("H7", {"soft_skill.proximity": .9, **hard("proximity", .6)},
     {"soft_skill.proximity": .5, **hard("proximity", .8)}),
    ("H8", {"salary_affordability": .9, **hard("proximity", .6)},
     {"salary_affordability": .5, **hard("proximity", .8)}),
    ("H10", {"semantic_cosine": .9}, {"semantic_cosine": .3}),
])
def test_tradeoffs_are_directional_bounded_and_reproducible(family, test_changes, control_changes):
    pool = [candidate("test", test_changes), candidate("control", control_changes)]
    before = deepcopy(pool)
    pair = family_pairs(family, pool)[0]
    assert pair["test_id"] == "test" and pair["control_id"] == "control"
    assert pair["contrast"]["preferred_delta"] >= .15
    assert 0 <= pair["nuisance_distance"] <= .15
    assert 0 <= pair["priority"] <= 1 and math.isfinite(pair["priority"])
    assert len(pair["comparison_hash"]) == 64
    assert comparable_pairs(pool) == comparable_pairs(list(reversed(pool)))
    assert pool == before
    assert pair["contrast"]["calibration_status"] == "uncalibrated_baseline"


def test_missing_class_only_and_unknown_or_rejected_level_are_not_contrasts():
    test = candidate("test", {"grade": 1 / 3, **hard("coverage", .9)})
    control = candidate("control", {"grade": 1.0, **hard("coverage", .5)})
    for name in ("stack.python.level", "domain.backend.level"):
        malformed = deepcopy(test)
        malformed["features"]["available"][malformed["features"]["names"].index(name)] = False
        assert comparable_pairs([malformed, control]) == []
    rejected = deepcopy(test)
    rejected["features"]["values"][rejected["features"]["names"].index("stack.python.rejected")] = 1
    assert comparable_pairs([rejected, control]) == []
    for item in (test, control):
        for index, name in enumerate(item["features"]["names"]):
            if name.endswith(".target"):
                item["features"]["available"][index] = False
    assert comparable_pairs([test, control]) == []


def test_h2_does_not_conflate_self_reported_pending_and_unknown_or_rejected():
    test = candidate("test", {**hard("confirmed_share", .9), **hard("depth", .4)})
    for status in ("self_reported", "pending"):
        changes = {**hard("confirmed_share", .3), **hard("depth", .8)}
        # Keep identical full feature schema; statuses are separate observations.
        test_status = deepcopy(test)
        changes.update({"stack.python.confirmed": 0, f"stack.python.{status}": 1})
        control = candidate("control", changes)
        test_status = candidate("test", {**hard("confirmed_share", .9), **hard("depth", .4),
                                         f"stack.python.{status}": 0})
        assert family_pairs("H2", [test_status, control])
        control["features"]["available"][control["features"]["names"].index(f"stack.python.{status}")] = False
        assert not family_pairs("H2", [test_status, control])


def test_h5_uses_longest_explicit_total_experience_and_never_sums_jobs():
    test = candidate("test", {"project_relevance_evidence": .9}, facts=[fact(24), fact(24)])
    control = candidate("control", {"project_relevance_evidence": .5}, facts=[fact(36)])
    pair = family_pairs("H5", [test, control])[0]
    assert pair["contrast"]["sacrificed_delta"] == pytest.approx(12 / 1200)
    for claims in ([], [fact(24, status="rejected")], [fact(True)],
                   [fact(24, kind="project")], [{"kind": "experience", "status": "confirmed", "facts": {}}]):
        missing = candidate("test", {"project_relevance_evidence": .9}, facts=claims)
        assert not family_pairs("H5", [missing, control])
    rejected_duration = fact(24)
    rejected_duration["facts"]["duration_months"]["status"] = "rejected"
    assert not family_pairs("H5", [candidate("test", {"project_relevance_evidence": .9},
                                            facts=[rejected_duration]), control])


def test_h6_requires_relevant_explicit_production_and_component_role_not_missing_false():
    values = {"experience.production": .8, "experience.role_evidence": 1, "experience.developer": 1}
    test = candidate("test", values, facts=[fact(production=True, role="developer")])
    control = candidate("control", {**values, "experience.production": 0},
                        facts=[fact(production=False, role="developer")])
    assert family_pairs("H6", [test, control])
    for claims in ([], [fact(role="developer")], [fact(production=False)],
                   [fact(production=False, role="developer", status="rejected")],
                   [fact(production=True, role="developer")]):
        unknown = candidate("control", {**values, "experience.production": 0}, facts=claims)
        assert not family_pairs("H6", [test, unknown])
    unrelated = fact(production=False, role="developer")
    unrelated["facts"]["stacks"][0]["key"] = "unrelated"
    assert not family_pairs("H6", [test, candidate("control", {**values, "experience.production": 0},
                                                  facts=[unrelated])])


def test_h9_disabled_and_h10_requires_compatible_semantics_and_near_hard_profiles():
    test, control = candidate("test", {"semantic_cosine": .9}), candidate("control", {"semantic_cosine": .3})
    assert not family_pairs("H9", [test, control])
    control["snapshot"]["semantic_space_id"] = "different-space"
    assert not family_pairs("H10", [test, control])
    control["snapshot"]["semantic_space_id"] = EMBEDDING_SPACE_ID
    control["features"]["available"][control["features"]["names"].index("semantic_cosine")] = False
    assert not family_pairs("H10", [test, control])
    far = candidate("control", {"semantic_cosine": .3, **hard("proximity", .9)})
    assert not family_pairs("H10", [test, far])
    assert state([row("H9") for _ in range(20)], "H9")["status"] == "not_testable"


def test_nuisance_and_utility_caps_reject_confounded_or_poor_comparisons():
    test = candidate("test", {"grade": 1 / 3, **hard("coverage", .9)})
    control = candidate("control", {"grade": 1.0, **hard("coverage", .5), "salary_affordability": .9})
    assert not family_pairs("H1", [test, control])
    control = candidate("control", {"grade": 1.0, **hard("coverage", .5)}, score=.1)
    assert not family_pairs("H1", [test, control])
    control = candidate("control", {"grade": 1.0, **hard("coverage", .5)}, missing=("semantic_cosine",))
    assert not family_pairs("H1", [test, control])
    control = candidate("control", {"grade": 1.0, **hard("coverage", .5)})
    assert family_pairs("H1", [test, control])
    assert not family_pairs("H1", [test, control, candidate("top", score=1.3)])


def test_optional_covariance_changes_information_priority_not_reward():
    pool = [candidate("test", {"semantic_cosine": .9}), candidate("control", {"semantic_cosine": .3})]
    confident = {"feature_names": ["semantic_cosine"], "scales": [1], "covariance": [[.001]]}
    uncertain = {**confident, "covariance": [[100]]}
    assert family_pairs("H10", pool, uncertain)[0]["priority"] > family_pairs("H10", pool, confident)[0]["priority"]
    assert family_pairs("H10", pool, {**confident, "covariance": [[float("nan")]]})
    other = candidate("other")
    assert family_pairs("H10", [*pool, other])[0]["contrast"]["pool_impact"] > 0


def test_summaries_require_complete_decisive_pairs_fixed_batch_and_ten_family_correction():
    assert state([])["status"] == "not_testable"
    assert state([row(test=None, control=None)])["status"] == "unclear"
    nondecisive = [row(test="like", control="like"), row(test="dislike", control="dislike"),
                  row(test="like", control=None), row(test="skip", control="like")]
    assert state(nondecisive)["decisive"] == 0
    assert state([row() for _ in range(19)])["status"] == "unclear"
    supported = state([row() for _ in range(20)])
    assert supported["status"] == "supported"
    assert supported["multiple_testing_families"] == 10
    assert supported["batch_status"] == "frozen"
    assert state([row(test="dislike", control="like") for _ in range(20)])["status"] == "not_supported"
    mixed = [row() for _ in range(15)] + [row(test="dislike", control="like") for _ in range(5)]
    assert state(mixed)["status"] == "unclear"  # an uncorrected 95% Wilson interval excludes .5
    extended = state([row() for _ in range(20)] + [row(test="dislike", control="like") for _ in range(100)])
    assert extended["status"] == supported["status"]
    assert extended["confidence_interval"] == supported["confidence_interval"]
    assert extended["post_batch_decisive"] == 100
    assert extended["observed_decisive"] == 120
    assert extended["policy_hash"] == policy_hash()


def test_repeat_journal_rows_do_not_become_independent_evidence():
    assert state([row(comparison_id="one") for _ in range(20)])["decisive"] == 1
    assert state([row(status="not_testable")])["status"] == "not_testable"


def test_h1_is_available_on_actual_builder_snapshots_and_grade_status_remains_separate():
    target = vacancy(**{"class": None, "stacks": {"python": 4, "postgres": 4}})
    schema = initial_schema()
    lower = matching_candidate(category=Category(specialization="backend", grade="junior"))
    higher = matching_candidate(category=Category(specialization="backend", grade="senior"))
    for person in (lower, higher):
        for coordinate in schema.coordinates:
            if coordinate.group == "stack" and coordinate.key in ("python", "postgres"):
                evidence = person.strict_evidence[coordinate.index]
                evidence.status, evidence.level = "self_reported", 4
                person.strict_vector[coordinate.index] = 4 / 7
    postgres = next(item.index for item in schema.coordinates if item.group == "stack" and item.key == "postgres")
    higher.strict_evidence[postgres].status = "rejected"
    higher.strict_vector[postgres] = 0
    cards = select_candidates(target, [lower, higher], ScoringPolicy(), 10).items
    raw = []
    for person in (lower, higher):
        card = next(item for item in cards if item.candidate_id == person.candidate_id)
        raw.append({"candidate_id": str(person.candidate_id), "score": card.explanation.score,
                    "features": asdict(build_features(target, person, card, schema)),
                    "snapshot": person.model_dump(mode="json")})
    pairs = family_pairs("H1", raw)
    assert pairs and pairs[0]["test_id"] == str(lower.candidate_id)
    raw[1]["snapshot"]["category"]["grade_status"] = "self_reported"
    assert not family_pairs("H1", raw)


def test_invalid_vectors_scores_duplicates_and_schema_mismatch_are_safe():
    a, b = candidate("a"), candidate("b")
    assert comparable_pairs([]) == []
    for field, value in (("score", float("nan")), ("score", True)):
        with pytest.raises(ValueError, match="finite"):
            comparable_pairs([{**a, field: value}, b])
    malformed = deepcopy(a)
    malformed["features"]["available"].pop()
    with pytest.raises(ValueError, match="dimensions"):
        comparable_pairs([malformed, b])
    with pytest.raises(ValueError, match="duplicate"):
        comparable_pairs([a, a])
    b["features"]["schema_hash"] = "another-context"
    assert comparable_pairs([a, b]) == []
