"""Typed interaction validation and optional PostgreSQL persistence checks."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from agents_service.agent_models import (
    AgentInteraction,
    InteractionOption,
    InteractionResponse,
)
from agents_service.agent_runtime import load_vacancy_definition
from agents_service.agent_store import AgentStore
from agents_service.database import connect, migrate
from agents_service.models import SchemaSnapshot
from agents_service.store import Store


def _interaction(**overrides):
    payload = {
        "interaction_id": uuid4(),
        "session_id": uuid4(),
        "turn_id": uuid4(),
        "draft_revision": 3,
        "kind": "single_select",
        "prompt": "Какой формат?",
        "options": [
            {"value": {"type": "remote"}, "label": "Удалённо"},
            {"value": {"type": "hybrid"}, "label": "Гибрид"},
        ],
    }
    payload.update(overrides)
    return AgentInteraction.model_validate(payload)


def test_structured_values_and_canonical_duplicate_rejection() -> None:
    value = {"group": "stack", "key": "python", "level": 7}
    option = InteractionOption(value=value, label="Python")
    assert option.value == value
    with pytest.raises(ValueError):
        _interaction(options=[{"value": {"a": 1, "b": 2}, "label": "A"}, {"value": {"b": 2, "a": 1}, "label": "B"}])


def test_option_value_json_budget_is_enforced() -> None:
    with pytest.raises(ValueError):
        InteractionOption(value={"text": "x" * 2_100}, label="large")


def test_revision_is_required_and_slider_bounds_are_strict() -> None:
    with pytest.raises(ValueError):
        AgentInteraction.model_validate({"interaction_id": uuid4(), "session_id": uuid4(), "turn_id": uuid4(), "kind": "slider", "prompt": "level", "min_value": True, "max_value": 7})
    slider = _interaction(kind="slider", options=[], min_value=0, max_value=7)
    assert slider.draft_revision == 3
    assert InteractionResponse(expected_revision=3, value=7).value == 7


@pytest.mark.integration
def test_store_persists_revision_and_rejects_duplicate_response() -> None:
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(url)
    owner_id = uuid4()
    schema = SchemaSnapshot(version=1, schema_hash="interaction-test", coordinates=[{"index": 0, "group": "domain", "key": "python"}])
    draft = Store(url).create(owner_id, {"title": "T"}, schema)
    with connect(url) as connection:
        store = AgentStore(connection)
        session = store.create_session(owner_id=owner_id, draft_id=draft.draft_id, draft_revision=1, definition=load_vacancy_definition())
        turn = store.append_turn(session_id=session.session_id, owner_id=owner_id, draft_id=draft.draft_id, message="?", expected_revision=1, idempotency_key=f"interaction-test-{uuid4()}")
        interaction = _interaction(session_id=session.session_id, turn_id=turn.turn_id, draft_revision=1)
        stored = store.save_interaction(interaction)
        assert stored.draft_revision == 1
        assert store.get_interaction(interaction.interaction_id, session.session_id).draft_revision == 1
        answered = store.respond_interaction(interaction.interaction_id, session.session_id, {"type": "remote"}, expected_draft_revision=1)
        assert answered.responded is True
        with pytest.raises(RuntimeError):
            store.respond_interaction(interaction.interaction_id, session.session_id, {"type": "hybrid"}, expected_draft_revision=1)
