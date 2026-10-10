"""Unit checks for strict agent contracts and provider boundary behavior."""

from __future__ import annotations

import pytest
from agents_service.agent_models import InteractionOption
from agents_service.agent_runtime import (
    AgentRuntime,
    AgentRuntimeError,
    InteractionArguments,
    SetFieldsArguments,
    load_vacancy_definition,
)
from agents_service.models import DraftFields


def test_packaged_definition_is_pinned_and_has_no_publish_tool() -> None:
    definition = load_vacancy_definition()
    assert definition.key == "vacancy"
    assert len(definition.knowledge_hash) == 64
    assert len(definition.definition_hash) == 64
    assert "create" not in definition.tools
    assert "publish" not in definition.tools


def test_tool_draft_view_keeps_fields_without_laya_audit_payload() -> None:
    """Provider prompts retain editable requirements while full audit stays in the API."""
    fields = {"title": "Backend", "stacks": {"python": 5}, "metadata": {"opaque": "x" * 60000}}
    draft = {"draft_id": "synthetic", "revision": 2, "state": "summary", "fields": fields,
             "missing_requirements": [], "provenance": {"probabilities": "x" * 60000},
             "summary": {"fields": fields, "summary_hash": "a" * 64, "revision": 2}}
    value = AgentRuntime._draft_tool_view(draft)
    assert value["fields"]["stacks"] == {"python": 5}
    assert value["summary"]["summary_hash"] == "a" * 64
    assert "provenance" not in value and "metadata" not in value["fields"]
    assert "fields" not in value["summary"]


def test_set_fields_rejects_unknown_or_metadata_fields() -> None:
    with pytest.raises(ValueError):
        SetFieldsArguments.model_validate({"fields": {"metadata": {"unsafe": True}}})
    with pytest.raises(ValueError):
        DraftFields.model_validate({"domens": {"python": 8}})


def test_interaction_target_and_options_are_bounded() -> None:
    interaction = InteractionArguments.model_validate(
        {
            "kind": "single_select",
            "prompt": "Формат работы?",
            "field_target": "type",
            "options": [
                {"value": "remote", "label": "Удалённо"},
                {"value": "hybrid", "label": "Гибрид"},
            ],
        }
    )
    assert interaction.options == [
        InteractionOption(value="remote", label="Удалённо"),
        InteractionOption(value="hybrid", label="Гибрид"),
    ]
    with pytest.raises(ValueError):
        InteractionArguments.model_validate({"kind": "slider", "prompt": "Уровень", "field_target": "metadata"})


def test_provider_result_requires_structured_choices() -> None:
    with pytest.raises(AgentRuntimeError) as error:
        AgentRuntime._parse_provider({"choices": []})
    assert error.value.code == "provider_invalid_output"


def test_provider_usage_ignores_non_budget_provider_fields() -> None:
    parsed = AgentRuntime._parse_provider(
        {
            "choices": [{"message": {"role": "assistant", "content": "готово", "tool_calls": []}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "total_tokens": 7,
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 2,
            },
        }
    )
    assert parsed.usage.total_tokens == 7
