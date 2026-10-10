"""Unit checks for replay-safe provider parsing and tool protocol behavior."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agents_service.database import connect
from agents_service.organization_runtime import (
    OrganizationRuntime,
    OrganizationRuntimeError,
)
from agents_service.organization_store import OrganizationStore


def _integration_url() -> str:
    value = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not value:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    return value


def _delete_owner(database_url: str, owner_id: object) -> None:
    with connect(database_url) as db:
        db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner_id,))


class _FinalProvider:
    model = "deepseek-chat"

    def __init__(self, content: str = "done") -> None:
        self.calls = 0
        self.content = content

    def complete(self, messages, tools, *, max_tokens):
        self.calls += 1
        return {"choices": [{"message": {"content": self.content, "tool_calls": []}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


class _ToolThenFinalProvider(_FinalProvider):
    def complete(self, messages, tools, *, max_tokens):
        self.calls += 1
        if self.calls == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "call-budget", "function": {"name": "discover_tools", "arguments": "{}"}
            }]}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        return super().complete(messages, tools, max_tokens=max_tokens)


class _EmptyRegistry:
    def catalog(self):
        return []

    def schemas(self, names):
        return []


def test_provider_usage_requires_exact_counts() -> None:
    """A response without exact usage cannot be accepted as billable."""
    runtime = object.__new__(OrganizationRuntime)
    with pytest.raises(OrganizationRuntimeError, match="provider_usage_missing"):
        runtime._usage({"choices": []})
    with pytest.raises(OrganizationRuntimeError, match="provider_usage_invalid"):
        runtime._usage({"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4}})


def test_provider_tool_calls_are_structurally_parsed() -> None:
    """Tool ids and function envelopes survive parsing for idempotent replay."""
    runtime = object.__new__(OrganizationRuntime)
    content, calls = runtime._parse_provider({"choices": [{"message": {
        "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "call_tool", "arguments": json.dumps({"name": "x"})}}]
    }}]})
    assert content == "" and calls[0]["id"] == "call-1"


def test_messages_use_plain_russian_and_authoritative_context_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt favors plain Russian, and clear-history revisions change provider input metadata."""
    owner_id = uuid4()

    class ContextStore:
        context = SimpleNamespace(current_summary={}, revision=7)

        def get_context(self, _owner_id):
            return self.context

        def get_memory(self, _owner_id):
            return SimpleNamespace(value={})

    store = ContextStore()
    runtime = object.__new__(OrganizationRuntime)
    runtime.store = store
    runtime._history_until_turn = lambda _turn: []
    runtime._authoritative_task_refs = lambda _owner_id: []

    def compact_context(*_args):
        store.context = SimpleNamespace(current_summary={"covered_sequence": 0}, revision=8)

    runtime._compact_context = compact_context
    turn = SimpleNamespace(owner_id=owner_id, turn_id=uuid4(), task_id=None, reasoning_history={}, provider_result=None)
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_BYTES", "1")

    messages = runtime._messages(turn, pricing=None)

    assert "По умолчанию отвечай работодателю на русском простыми словами" in messages[0]["content"]
    assert "Не показывай внутренние названия инструментов и переменных, UUID" in messages[0]["content"]
    initial_context = json.loads(messages[0]["content"].split("\n", 1)[1])
    compact_context_payload = json.loads(messages[1]["content"])
    assert initial_context["authoritative_context_revision"] == 7
    assert compact_context_payload["authoritative_context_revision"] == 8


def test_round_budget_is_total_across_one_turn() -> None:
    """A tool loop cannot reset the paid completion round ceiling on each retry."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    provider = _ToolThenFinalProvider()
    try:
        turn = store.create_turn(owner_id, "one round", "runtime-round-budget")
        claimed = store.claim_turn_by_id(owner_id, turn.turn_id)
        runtime = OrganizationRuntime(store, provider=provider, registry=_EmptyRegistry(), max_rounds=1)
        result = runtime.run_turn(claimed)
        assert result.status == "failed" and result.error_code == "round_budget_exceeded"
        assert provider.calls == 1
    finally:
        _delete_owner(database_url, owner_id)


def test_postgres_crash_after_provider_receipt_replays_without_second_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker crash between provider settlement and assistant append reuses the cached receipt."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    provider = _FinalProvider()
    try:
        turn = store.create_turn(owner_id, "replay me", "runtime-crash")
        claimed = store.claim_turn_by_id(owner_id, turn.turn_id)
        runtime = OrganizationRuntime(store, provider=provider, registry=object(), max_rounds=2)
        original_append = store.append_message
        crashed = False

        def append_once(*args, **kwargs):
            nonlocal crashed
            if kwargs.get("role") == "assistant" and not crashed:
                crashed = True
                raise RuntimeError("simulated worker crash")
            return original_append(*args, **kwargs)

        monkeypatch.setattr(store, "append_message", append_once)
        with pytest.raises(RuntimeError, match="simulated worker crash"):
            runtime._run(claimed)
        monkeypatch.setattr(store, "append_message", original_append)
        current = store.get_turn(owner_id, turn.turn_id)
        assert current is not None and current.provider_result is not None
        store.update_turn(owner_id, turn.turn_id, expected_lease_token=current.lease_token, status="retrying")
        resumed = store.claim_turn_by_id(owner_id, turn.turn_id)
        result = runtime.run_turn(resumed)
        assert result.status == "succeeded"
        assert provider.calls == 1
        messages = store.list_messages(owner_id, limit=200).items
        assert sum(item.role == "assistant" for item in messages) == 1
    finally:
        _delete_owner(database_url, owner_id)


def test_postgres_long_history_compacts_context_and_keeps_memory_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long transcripts are summarized through a metered strict-JSON call before completion."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    provider = _CompactionProvider()
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_BYTES", "1200")
    try:
        store.get_or_create_chat(owner_id)
        for index in range(130):
            store.append_message(owner_id, role="user", content=f"old user fact {index} " + ("x" * 100),
                                 idempotency_key=f"history-user-{index}")
            store.append_message(owner_id, role="assistant", content=f"old assistant fact {index} " + ("y" * 100),
                                 idempotency_key=f"history-assistant-{index}")
        turn = store.create_turn(owner_id, "current request", "runtime-compaction")
        claimed = store.claim_turn_by_id(owner_id, turn.turn_id)
        runtime = OrganizationRuntime(store, provider=provider, registry=object(), max_rounds=2)
        result = runtime.run_turn(claimed)
        assert result.status == "succeeded"
        assert provider.calls == 2
        summary = store.get_context(owner_id).current_summary
        assert summary["preserved"] is True and summary["last_turn_id"] == str(turn.turn_id)
        assert summary["covered_sequence"] >= 200
    finally:
        _delete_owner(database_url, owner_id)


class _CompactionProvider(_FinalProvider):
    def complete(self, messages, tools, *, max_tokens):
        self.calls += 1
        if messages and "Summarize only durable employer context" in str(messages[0].get("content", "")):
            content = json.dumps({"current_summary": {"preserved": True}})
        else:
            content = "compacted answer"
        return {"choices": [{"message": {"content": content, "tool_calls": []}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
