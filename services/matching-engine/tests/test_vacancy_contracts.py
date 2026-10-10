"""Vacancy eligibility, lossless explicit targets, and canonical alias contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from matching_engine.strict import initial_schema
from matching_engine.vacancy_features import build_strict_features, semantic_text
from matching_engine.vacancy_models import (
    VacancyInput,
    VacancyReady,
    build_mode,
    missing_requirements,
)


def eligible_vacancy(**overrides) -> VacancyInput:
    """Build an isolated synthetic upstream snapshot with minimum qualifying class."""
    return VacancyInput.model_validate({
        "source_revision": 1, "owner_id": str(uuid4()), "status": "open",
        "zp_min": 100000, "zp_max": 200000, "daily": "5/2", "type": "remote", "city": "Москва",
        "class": "backend", **overrides,
    })


@pytest.mark.parametrize("criteria,expected_mode", [
    ({"class": "backend"}, "class_only"),
    ({"class": None, "domens": {"sql": 3}}, "requirements_only"),
    ({"class": None, "stacks": {"python": 0}}, "requirements_only"),
    ({"class": "ML", "stacks": {"python": 7}, "soft_skills": {"teamwork": 2}}, "mixed"),
    ({"class": "front", "soft_skills": {"teamwork": 4}}, "mixed"),
])
def test_eligible_sparse_requirement_matrix(criteria, expected_mode) -> None:
    vacancy = eligible_vacancy(**criteria)
    assert missing_requirements(vacancy) == []
    assert build_mode(vacancy) == expected_mode


@pytest.mark.parametrize("fields", [{}, {"soft_skills": {"teamwork": 7}}, {"bio": "Описание"}])
def test_classless_soft_or_bio_only_stays_incomplete(fields) -> None:
    vacancy = eligible_vacancy(**{"class": None, **fields})
    assert missing_requirements(vacancy) == ["class_or_domens_or_stacks"]


def test_zero_has_true_mask_and_class_does_not_invent_levels() -> None:
    vacancy = eligible_vacancy(stacks={"python": 0}, domens={"sql": 7})
    schema = initial_schema()
    vector, mask = build_strict_features(vacancy, schema)
    positions = {c.key: c.index for c in schema.coordinates}
    assert vector[positions["python"]] == 0 and mask[positions["python"]]
    assert vector[positions["sql"]] == 1 and mask[positions["sql"]]
    assert vector[positions["go"]] == 0 and not mask[positions["go"]]
    empty_vector, empty_mask = build_strict_features(eligible_vacancy(), schema)
    assert empty_vector == [0.0] * 37 and not any(empty_mask)


def test_optional_semantics_uses_bio_only_and_preserves_alias_wire_contract() -> None:
    vacancy = eligible_vacancy(**{"class": "back", "type": "очно", "bio": "  Реальная роль  "})
    assert vacancy.class_key == "backend" and vacancy.type == "onsite"
    assert vacancy.model_dump(by_alias=True)["class"] == "backend"
    assert semantic_text(vacancy) == "Описание вакансии: Реальная роль"
    assert semantic_text(eligible_vacancy(bio=" \n ")) is None
    cleared = eligible_vacancy(bio=None)
    assert cleared.bio == "" and semantic_text(cleared) is None


@pytest.mark.parametrize("fields", [
    {"source_revision": True}, {"source_revision": 1.5}, {"source_revision": 2**63},
    {"source_revision": 2**53}, {"zp_min": 2**53}, {"zp_max": 2**53},
    {"zp_min": True}, {"zp_max": -1}, {"zp_min": 10, "zp_max": 1},
    {"stacks": {"python": True}}, {"domens": {"sql": 7.1}},
    {"soft_skills": {"teamwork": 8}}, {"owner_id": "bad"}, {"daily": "   "},
    {"type": "unknown"}, {"status": "ready"}, {"metadata": {"x": "x" * 65536}},
])
def test_invalid_inputs_rejected_without_coercing_or_losing_values(fields) -> None:
    with pytest.raises(ValidationError):
        eligible_vacancy(**fields)


def test_unknown_registry_coordinate_is_not_silently_ignored() -> None:
    with pytest.raises(ValueError, match="unknown coordinate"):
        build_strict_features(eligible_vacancy(stacks={"rust": 7}), initial_schema())


def test_maximum_safe_json_integer_survives_round_trip() -> None:
    """Gateway-compatible integers remain exact through canonical JSON serialization."""
    safe = 2**53 - 1
    value = eligible_vacancy(source_revision=safe, zp_min=safe, zp_max=safe)
    restored = VacancyInput.model_validate_json(value.model_dump_json(by_alias=True))
    assert restored.source_revision == restored.zp_min == restored.zp_max == safe


def test_schema_describes_alias_inputs_and_canonical_ready_output() -> None:
    """Gateway validates generated input schema before Python alias normalization runs."""
    import json

    input_schema = VacancyInput.model_json_schema(mode="validation")["properties"]
    output_schema = VacancyReady.model_json_schema(mode="serialization")["properties"]
    for alias in ("очно", "дистант", "гибрид"):
        assert alias in json.dumps(input_schema["type"], ensure_ascii=False)
        assert alias not in json.dumps(output_schema["type"], ensure_ascii=False)
    for alias in ("back", "front", "ML"):
        assert '"' + alias + '"' in json.dumps(input_schema["class"])
        assert '"' + alias + '"' not in json.dumps(output_schema["class"])
