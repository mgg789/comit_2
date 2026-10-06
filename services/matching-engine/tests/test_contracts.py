"""Validation and strict-registry invariants independent of ML and PostgreSQL."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.main import create_app
from matching_engine.models import (
    CandidateProfile,
    category_for,
    confirmed_strength,
    missing_requirements,
)
from matching_engine.strict import (
    build_strict_vector,
    initial_schema,
    validate_coordinates,
)
from matching_engine.strict_evidence import build_strict_evidence
from pydantic import ValidationError


def eligible_profile(**overrides) -> CandidateProfile:
    """Build only synthetic candidate data; callers vary one contract requirement."""
    data = {
        "source_revision": 1,
        "display_name": "Synthetic Candidate",
        "email": "synthetic@example.invalid",
        "email_verified": True,
        "city": "Москва",
        "salary": {"from_rub": 100000, "to_rub": 150000},
        "primary_direction": "backend",
        "grade_result": {"grade": "middle", "status": "approved",
                         "test_session_id": str(uuid4()), "approved_at": datetime.now(UTC)},
        "consents": {"processing": True, "pool": True, "accepted_at": datetime.now(UTC),
                     "version": "privacy-v1", "training": True, "training_version": "training-v1",
                     "training_accepted_at": datetime.now(UTC)},
        "domains": [{"key": "algorithms", "level": 7, "status": "confirmed",
                     "provenance": ["synthetic-test"]}],
        "stacks": [{"key": "python", "level": 4, "status": "partial"}],
        "soft_skills": [{"key": "teamwork", "level": 5, "status": "self_reported"}],
        "projects": [{"title": "Synthetic project", "description": "Python service",
                      "status": "partial", "details": {"my_role": "developer"}}],
    }
    data.update(overrides)
    return CandidateProfile.model_validate(data)


def test_eligibility_and_category_are_based_on_approved_test() -> None:
    profile = eligible_profile()
    assert missing_requirements(profile) == []
    assert category_for(profile).model_dump() == {
        "specialization": "backend", "grade": "middle", "grade_status": "approved",
    }
    profile.grade_result.status = "pending"
    assert "approved_grade_result" in missing_requirements(profile)
    with pytest.raises(ValueError):
        category_for(profile)


def test_self_reported_grade_is_pool_ready_but_preserves_declared_status() -> None:
    profile = eligible_profile(
        grade_result={"grade": "middle", "status": "self_reported"},
    )
    assert missing_requirements(profile) == []
    assert category_for(profile).grade_status == "self_reported"
    profile.consents.pool = False
    assert missing_requirements(profile) == ["pool_consent"]


@pytest.mark.parametrize("missing", ["test_session_id", "approved_at"])
def test_approved_grade_requires_real_test_evidence(missing: str) -> None:
    grade = {"grade": "middle", "status": "approved",
             "test_session_id": str(uuid4()), "approved_at": datetime.now(UTC)}
    grade[missing] = None
    with pytest.raises(ValidationError, match="approved grade requires"):
        eligible_profile(grade_result=grade)


def test_draft_checklist_and_consent_revocation() -> None:
    assert len(missing_requirements(CandidateProfile(source_revision=1))) == 10
    profile = eligible_profile()
    profile.consents.pool = False
    assert missing_requirements(profile) == ["pool_consent"]


@pytest.mark.parametrize("status", ["confirmed", "partial", "self_reported", "pending", "rejected"])
def test_all_declared_hard_and_soft_statuses_preserve_levels(status: str) -> None:
    profile = eligible_profile(
        domains=[{"key": "algorithms", "level": 7, "status": status}],
        stacks=[{"key": "python", "level": 7, "status": status}],
        soft_skills=[{"key": "teamwork", "level": 7, "status": status}],
    )
    schema = initial_schema()
    vector = build_strict_vector(profile, schema)
    assert len(vector) == 37
    expected = 0.0 if status == "rejected" else 1.0
    assert vector[0] == vector[15] == vector[31] == expected
    evidence = build_strict_evidence(profile, schema)
    assert evidence[15]["status"] == status and evidence[15]["level"] == 7
    assert "provenance" not in evidence[15]
    assert evidence[1]["status"] is None


def test_strict_normalization_does_not_l2_renormalize() -> None:
    vector = build_strict_vector(eligible_profile(), initial_schema())
    assert vector[0] == 1.0
    assert vector[15] == 4 / 7
    assert vector[31] == 5 / 7
    assert confirmed_strength(eligible_profile()) == 7


def test_unknown_and_duplicate_coordinates_are_rejected() -> None:
    profile = eligible_profile(stacks=[{"key": "future_stack", "level": 5, "status": "pending"}])
    with pytest.raises(ValueError, match="unknown coordinate"):
        validate_coordinates(profile, initial_schema())
    with pytest.raises(ValidationError, match="duplicate"):
        eligible_profile(stacks=[{"key": "python", "level": 2, "status": "pending"}] * 2)


@pytest.mark.parametrize("override", [
    {"salary": {"from_rub": 2, "to_rub": 1}},
    {"stacks": [{"key": "python", "level": 8, "status": "confirmed"}]},
    {"source_revision": 0},
    {"unexpected_field": True},
])
def test_malformed_contracts_fail_before_persistence(override: dict) -> None:
    with pytest.raises(ValidationError):
        eligible_profile(**override)


def test_internal_authentication_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CANDIDATE_PIPELINE_TOKEN", raising=False)
    client = TestClient(create_app(encoder_ready=lambda: False))
    assert client.get("/healthz").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.get("/internal/pool").status_code == 503
    client = TestClient(create_app(internal_token="synthetic-test-token", encoder_ready=lambda: False))
    assert client.get("/internal/pool").status_code == 401
    assert client.get("/internal/pool", headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert client.get("/internal/pool", headers={"X-Internal-Token": "synthetic-test-token"}).status_code == 503


def test_registry_exactly_matches_context_35() -> None:
    schema = initial_schema()
    assert [c.index for c in schema.coordinates] == list(range(37))
    assert len([c for c in schema.coordinates if c.group == "domain"]) == 15
    assert len([c for c in schema.coordinates if c.group == "stack"]) == 16
    assert len([c for c in schema.coordinates if c.group == "soft_skill"]) == 6


def test_training_is_optional_for_pool_and_needs_its_own_acceptance() -> None:
    """Legacy consent admits a candidate without granting a training purpose."""
    profile = eligible_profile(consents={"processing": True, "pool": True,
                                       "version": "profile-v1", "accepted_at": datetime.now(UTC)})
    assert missing_requirements(profile) == []
    assert profile.consents.training is False
    with pytest.raises(ValidationError, match="explicit versioned consent"):
        eligible_profile(consents={"processing": True, "pool": True, "training": True})
