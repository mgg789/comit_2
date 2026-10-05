"""Unit checks for the public vacancy draft contracts."""

import pytest
from agents_service.models import CreateRequest, DraftFields, DraftPatch, SchemaSnapshot
from pydantic import ValidationError


def test_draft_fields_normalize_aliases_and_reject_reversed_salary() -> None:
    """Upstream aliases normalize while salary and unknown fields remain strict."""
    fields = DraftFields(**{"title": "Backend", "type": "дистант", "class": "back", "zp_min": 0, "zp_max": 1})
    assert fields.type == "remote"
    assert fields.class_key == "backend"
    with pytest.raises(ValidationError):
        DraftFields(zp_min=2, zp_max=1)
    with pytest.raises(ValidationError):
        DraftFields(title=" ")
    with pytest.raises(ValidationError):
        DraftFields.model_validate({"unknown": 1})
    with pytest.raises(ValidationError):
        DraftFields.model_validate({"type": ["remote"]})
    with pytest.raises(ValidationError):
        DraftFields.model_validate({"class": {"value": "backend"}})


def test_patch_preserves_unset_fields_and_create_hash_is_strict() -> None:
    """Patch model field sets let the store distinguish omission from an explicit update."""
    patch = DraftPatch(expected_revision=2, fields={"city": "Москва"})
    assert patch.fields.model_fields_set == {"city"}
    assert patch.fields.model_dump(exclude_unset=True, by_alias=True) == {"city": "Москва"}
    with pytest.raises(ValidationError):
        CreateRequest(expected_revision=1, summary_hash="not-a-sha")


def test_schema_snapshot_rejects_duplicate_coordinates() -> None:
    """Registry coordinates are an ordered identity and cannot be ambiguous."""
    with pytest.raises(ValidationError):
        SchemaSnapshot(
            version=37,
            schema_hash="a" * 64,
            coordinates=[
                {"index": 0, "group": "stack", "key": "python"},
                {"index": 0, "group": "domain", "key": "sql"},
            ],
        )
