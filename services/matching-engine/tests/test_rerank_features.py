"""Raw feature semantics, registry compatibility and private structured-fact boundaries."""

from io import BytesIO

import pytest
from matching_engine.combine_models import ScoringPolicy, project_narrative_facts
from matching_engine.combine_scoring import select_candidates
from matching_engine.embeddings import EMBEDDING_SPACE_ID, build_semantic_text
from matching_engine.models import Coordinate, NarrativeClaim
from matching_engine.rerank_features import build_features, feature_schema
from matching_engine.resume import render_resume
from matching_engine.strict import initial_schema, schema_hash, validate_coordinates
from pydantic import ValidationError
from pypdf import PdfReader
from test_combine_scoring import candidate, vacancy
from test_contracts import eligible_profile


def features(target=None, value=None):
    """Use actual main cards and strict registry as the runtime feature contract."""
    target = target or vacancy()
    value = value or candidate()
    cards = select_candidates(target, [value], ScoringPolicy(), 50).items
    vector = build_features(target, value, cards[0], initial_schema())
    return vector, dict(zip(vector.names, vector.values, strict=True)), dict(zip(vector.names, vector.available, strict=True))


def claim(**overrides):
    """Synthetic facts deliberately include private strings to test the projection."""
    data = {"title": "Private name", "description": "private@example.invalid", "status": "partial",
            "facts": {"domains": [{"key": "sql", "status": "confirmed", "provenance": ["private-link"]}],
                      "stacks": [{"key": "python", "status": "self_reported"}],
                      "duration_months": {"value": 24, "status": "confirmed", "provenance": ["private-link"]},
                      "role": {"value": "developer", "status": "partial"},
                      "production_support": {"value": False, "status": "confirmed"}}}
    return NarrativeClaim.model_validate({**data, **overrides})


def test_absence_is_not_zero_and_class_only_has_no_invented_relevance():
    vector, values, available = features()
    assert not available["stack.coverage"] and not available["semantic_cosine"]
    assert values["available.stack.coverage"] == 0
    assert not available["project.relevance"]
    assert len(vector.names) == len(set(vector.names)) == len(vector.values)
    target = vacancy(stacks={"python": 0})
    _, values, available = features(target)
    assert available["stack.python.target"] and values["stack.python.target"] == 0


@pytest.mark.parametrize("status", ["confirmed", "partial", "self_reported", "pending", "rejected"])
def test_declared_levels_and_status_are_separate_even_when_rejected(status):
    value = candidate()
    value.strict_evidence[15].level = 7
    value.strict_evidence[15].status = status
    value.strict_vector[15] = 0 if status == "rejected" else 1
    _, values, available = features(vacancy(stacks={"python": 7}), value)
    assert values["stack.python.level"] == 1 and available["stack.python.level"]
    assert values[f"stack.python.{status}"] == 1
    assert values["stack.coverage"] == (0 if status == "rejected" else 1)


def test_safe_facts_and_individual_status_drive_relevance_without_text_or_private_strings():
    value = candidate(narrative_facts=project_narrative_facts({"projects": [claim()]}))
    safe = str(value.narrative_facts[0].model_dump())
    for private in ("private-link", "Private name", "private@example.invalid", "provenance"):
        assert private not in safe
    _, values, available = features(vacancy(stacks={"python": 4}, domens={"sql": 4}), value)
    assert values["project.relevance"] == 1
    assert values["project.duration"] == .2
    assert values["project.evidence"] == pytest.approx(.7)  # min(parent .8, refs 1/.6)
    assert values["project.production"] == 0 and available["project.production"]
    value.narrative_facts[0].facts.duration_months.status = "rejected"
    value.narrative_facts[0].facts.stacks[0].status = "rejected"
    _, values, available = features(vacancy(stacks={"python": 4}), value)
    assert values["project.relevance"] == 0 and not available["project.duration"]
    value.narrative_facts[0].status = "rejected"
    _, _, available = features(vacancy(stacks={"python": 4}), value)
    assert not available["project.relevance"]


def test_interactions_depend_on_observed_requirement_signals():
    _, values, available = features(vacancy(stacks={"python": 4}))
    assert values["coverage_grade"] == pytest.approx(2 / 3)
    assert available["salary_technical_fit"]
    _, _, absent = features()
    assert not absent["coverage_grade"] and not absent["salary_technical_fit"]


def test_empty_facts_and_duration_without_references_are_unknown_relevance():
    for facts in ({}, {"duration_months": {"value": 12, "status": "confirmed"}}):
        value = candidate(narrative_facts=project_narrative_facts({"projects": [claim(facts=facts)]}))
        _, _, available = features(vacancy(stacks={"python": 4}), value)
        assert not available["project.relevance"]
        assert not available["project_relevance_evidence"]


def test_semantics_only_compatible_pinned_space_is_available():
    value = candidate(semantic_space_id=EMBEDDING_SPACE_ID)
    target = vacancy()
    target.semantic_vector = value.semantic_vector
    target.semantic_space_id = EMBEDDING_SPACE_ID
    _, values, available = features(target, value)
    assert available["semantic_cosine"] and values["semantic_cosine"] == 1
    target.semantic_space_id = "different-space"
    _, _, available = features(target, value)
    assert not available["semantic_cosine"]


def test_appending_registry_changes_feature_hash_and_cannot_pad_old_snapshot():
    old = initial_schema()
    newer = old.model_copy(deep=True)
    newer.version = 2
    newer.coordinates.append(Coordinate(index=37, group="stack", key="rust"))
    newer.schema_hash = schema_hash(newer.coordinates)
    assert feature_schema(old).schema_hash != feature_schema(newer).schema_hash
    target, value = vacancy(), candidate()
    card = select_candidates(target, [value], ScoringPolicy(), 1).items[0]
    with pytest.raises(ValueError, match="dimensions"):
        build_features(target, value, card, newer)
    bad = initial_schema()
    bad.coordinates.reverse()
    with pytest.raises(ValueError, match="order/hash"):
        feature_schema(bad)


def test_legacy_facts_null_preserve_canonical_payload_and_frozen_semantics():
    profile = eligible_profile()
    raw = profile.model_dump()
    raw["projects"][0].pop("facts")
    assert profile.model_validate(raw).model_dump() == profile.model_dump()
    before = build_semantic_text(profile)
    profile.projects[0].facts = claim().facts
    assert build_semantic_text(profile) == before
    validate_coordinates(profile, initial_schema())
    profile.projects[0].facts.stacks[0].key = "unknown_stack"
    with pytest.raises(ValueError, match="unknown narrative coordinate"):
        validate_coordinates(profile, initial_schema())


@pytest.mark.parametrize("fact", [
    {"duration_months": {"value": -1, "status": "confirmed"}},
    {"duration_months": {"value": 1201, "status": "confirmed"}},
    {"duration_months": {"value": True, "status": "confirmed"}},
    {"production_support": {"value": "yes", "status": "confirmed"}},
    {"role": {"value": "made_up", "status": "confirmed"}},
    {"stacks": [{"key": "python", "status": "confirmed"}, {"key": "python", "status": "pending"}]},
])
def test_typed_facts_reject_invalid_values(fact):
    with pytest.raises(ValidationError):
        claim(facts=fact)


def test_pdf_displays_facts_and_statuses_without_private_provenance():
    profile = eligible_profile(projects=[claim().model_dump()])
    pdf = render_resume(profile, candidate().category)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    for expected in ("Длительность, месяцев: 24", "Эксплуатация: нет", "python", "самооценка"):
        assert expected in text
    assert "private-link" not in text and "private@example.invalid" not in text


def test_wrong_card_identity_and_nonfinite_score_are_rejected():
    target, value = vacancy(), candidate()
    card = select_candidates(target, [value], ScoringPolicy(), 1).items[0]
    card.explanation.score = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        build_features(target, value, card, initial_schema())
    card.profile_revision = 99
    with pytest.raises(ValueError, match="snapshot"):
        build_features(target, value, card, initial_schema())
