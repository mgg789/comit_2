"""Decision cases for strict gates, declared evidence, bounded semantics and main top-k."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_contracts import eligible_profile
from test_vacancy_contracts import eligible_vacancy

from matching_engine.combine_models import MatchCandidate, ScoringPolicy
from matching_engine.combine_scoring import (
    policy_hash,
    score_candidate,
    select_candidates,
    strict_filter,
)
from matching_engine.models import Category, category_for
from matching_engine.strict import build_strict_vector, initial_schema
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import build_strict_features
from matching_engine.vacancy_models import VacancyReady, missing_requirements


def vacancy(**values) -> VacancyReady:
    """Build actual registry artifacts for synthetic completed vacancies."""
    source = eligible_vacancy(**values)
    schema = initial_schema()
    vector, mask = build_strict_features(source, schema)
    return VacancyReady(**source.model_dump(by_alias=True), vacancy_id=uuid4(), revision=1,
        build_mode="mixed" if any(mask) else "class_only", strict_vector=vector,
        requirement_mask=mask, strict_schema_version=1, strict_schema_hash=schema.schema_hash,
        semantic_vector=None, semantic_model_version=None, semantic_space_id=None,
        semantic_available=False, published_at=datetime.now(UTC))


def candidate(**overrides) -> MatchCandidate:
    """Use the real strict/evidence builders; inference doubles remain in the test only."""
    profile = eligible_profile()
    schema = initial_schema()
    fields = {"candidate_id": uuid4(), "profile_revision": 1, "display_name": "Candidate synthetic",
        "city": "Москва", "salary": profile.salary, "category": category_for(profile), "rank": 1,
        "class_size": 1, "rating_score": 1, "confirmed_strength": 0,
        "strict_vector": build_strict_vector(profile, schema),
        "strict_evidence": build_strict_evidence(profile, schema),
        "strict_schema_version": 1, "strict_schema_hash": schema.schema_hash,
        "semantic_vector": [1.0] + [0.0] * 639, "semantic_model_version": "test-document",
        "semantic_space_id": "test-space", "published_at": datetime.now(UTC), "resume_url": "/public.pdf",
        "work_types": ["onsite", "remote", "hybrid"], "relocation_ready": False}
    return MatchCandidate(**{**fields, **overrides})


@pytest.mark.parametrize("overrides,reason", [
    ({"category": Category(specialization="ml", grade="middle")}, "class_mismatch"),
    ({"salary": {"from_rub": 200001, "to_rub": 300000}}, "salary_disjoint"),
    ({"work_types": None}, "work_type_unknown"),
    ({"work_types": ["onsite"]}, "work_type_mismatch"),
    ({"city": ""}, "location_unknown"),
    ({"strict_schema_version": 2}, "strict_schema_mismatch"),
])
def test_strict_exclusions(overrides, reason) -> None:
    assert reason in strict_filter(vacancy(), candidate(**overrides)).reasons


def test_salary_boundaries_and_classless_requirements() -> None:
    assert strict_filter(vacancy(), candidate(salary={"from_rub": 200000, "to_rub": 300000})).passed
    assert strict_filter(vacancy(**{"class": None, "stacks": {"python": 2}}),
                         candidate(category=Category(specialization="ml", grade="middle"))).passed


@pytest.mark.parametrize("work_type", ["onsite", "hybrid"])
def test_relocation_requires_explicit_consent_and_destination(work_type) -> None:
    target = vacancy(type=work_type)
    assert strict_filter(target, candidate(city="Казань")).reasons == ["relocation_not_ready"]
    assert strict_filter(target, candidate(city="Казань", relocation_ready=None)).reasons == ["relocation_unknown"]
    assert strict_filter(target, candidate(city="Казань", relocation_ready=True)).passed
    assert not strict_filter(target, candidate(city="Казань", relocation_ready=True,
                                               relocation_cities=["Тюмень"])).passed
    assert strict_filter(target, candidate(city="Казань", relocation_ready=True,
                                          relocation_cities=["  мОСКВА "])).passed
    assert strict_filter(target, candidate(city="  МОСКВА  ", relocation_ready=None)).passed
    assert strict_filter(target, candidate(city="Moscow", relocation_ready=None)).passed


def test_remote_ignores_distance_but_not_required_city_or_work_consent() -> None:
    assert strict_filter(vacancy(type="remote"), candidate(city="Казань", relocation_ready=False)).passed
    assert "city" in missing_requirements(eligible_vacancy(city=None))
    assert strict_filter(vacancy(), candidate(), excluded=True).reasons == ["selection_state_excluded"]


@pytest.mark.parametrize("status,confidence", [
    ("confirmed", 1), ("partial", .8), ("self_reported", .6), ("pending", .5), ("rejected", 0),
])
def test_unconfirmed_levels_participate_with_explicit_confidence(status, confidence) -> None:
    value = candidate()
    value.strict_vector = [0.0] * 37
    evidence = value.strict_evidence[15]
    evidence.status, evidence.level = status, 7
    value.strict_vector[15] = 0.0 if status == "rejected" else 1.0
    result = score_candidate(vacancy(stacks={"python": 7}), value, ScoringPolicy())
    assert result.strict_score == confidence
    assert result.top_matched_features[0].status == status


def test_zero_target_is_distinct_from_unknown_and_uniform_underqualification() -> None:
    value = candidate()
    value.strict_vector = [0.0] * 37
    evidence = value.strict_evidence[15]
    evidence.status, evidence.level = None, 0
    assert score_candidate(vacancy(stacks={"python": 0}), value, ScoringPolicy()).strict_score == 0
    evidence.status = "confirmed"
    assert score_candidate(vacancy(stacks={"python": 0}), value, ScoringPolicy()).strict_score == 1
    value.strict_vector[15], evidence.level = 1 / 7, 1
    result = score_candidate(vacancy(stacks={"python": 7}), value, ScoringPolicy())
    assert result.strict_score == pytest.approx(1 / 7)
    # A direction-only cosine would incorrectly award one to this underlevel profile.


def test_class_only_and_optional_semantics_are_bounded_and_explained() -> None:
    target, value = vacancy(), candidate(rating_score=.5)
    policy = ScoringPolicy()
    plain = score_candidate(target, value, policy)
    assert plain.strict_score is None and plain.score == .5
    assert plain.semantic_unavailable_reason == "vacancy_bio_absent"
    target.semantic_vector, target.semantic_space_id = value.semantic_vector, "test-space"
    plus = score_candidate(target, value, policy)
    assert plus.score == pytest.approx(.55) and plus.semantic_contribution == pytest.approx(.05)
    target.semantic_vector = [-1.0] + [0.0] * 639
    minus = score_candidate(target, value, policy)
    assert minus.score == pytest.approx(.45)
    target.semantic_space_id = "different-model"
    mismatch = score_candidate(target, value, policy)
    assert mismatch.score == .5 and mismatch.semantic_unavailable_reason == "semantic_space_mismatch"


def test_top_k_has_stable_order_no_hard_tail_cutoff_and_filter_counts() -> None:
    target = vacancy()
    values = [candidate(candidate_id=UUID(int=index), rating_score=score)
              for index, score in ((3, .3), (2, .8), (1, .8))]
    excluded = candidate(city="")
    result = select_candidates(target, [*values, excluded], ScoringPolicy(), 2)
    assert [card.candidate_id.int for card in result.items] == [1, 2]
    assert result.scanned_count == 4 and result.passed_count == 3
    assert result.filter_counts == {"location_unknown": 1}
    assert "salary_overlap" in result.items[0].filter_factors
    assert "remote_location" in result.items[0].filter_factors
    assert [card.position for card in result.items] == [1, 2]
    assert select_candidates(target, [], ScoringPolicy(), 50).items == []


def test_policy_hash_and_malformed_artifacts_are_explicit() -> None:
    policy = ScoringPolicy()
    assert policy_hash(policy) == policy_hash(ScoringPolicy())
    assert policy_hash(policy) != policy_hash(ScoringPolicy(semantic_correction=.05))
    with pytest.raises(ValidationError):
        ScoringPolicy(rating_weight=.2, strict_weight=.3)
    with pytest.raises(ValidationError):
        ScoringPolicy(confidence={"confirmed": float("nan")})
    value = candidate()
    value.strict_vector.pop()
    with pytest.raises(ValueError, match="dimensions"):
        score_candidate(vacancy(), value, policy)


def test_missing_work_fields_preserve_legacy_input_without_inventing_consent() -> None:
    profile = eligible_profile()
    assert profile.work_types is None and profile.relocation_ready is None
    with pytest.raises(ValidationError):
        eligible_profile().model_validate({**profile.model_dump(), "relocation_ready": "yes"})
