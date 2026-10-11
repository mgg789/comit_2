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
        context = SimpleNamespace(current_summary={"obsolete_summary": "stale-fact-" * 500}, revision=7)

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
    assert "Не обещай фоновое выполнение без подтверждённой сохранённой задачи" in messages[0]["content"]
    assert "не выдумывай результат" in messages[0]["content"]
    assert "ожидание, работа, запрос уточнения и ошибка не означают завершение" in messages[0]["content"]
    compact_context_payload = json.loads(messages[0]["content"].split("\n", 1)[1])
    assert len(messages) == 1
    assert compact_context_payload["authoritative_context_revision"] == 8
    assert compact_context_payload["owner_id"] == str(owner_id)
    assert compact_context_payload["task_id"] is None
    assert "stale-fact-" not in messages[0]["content"]


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


def test_history_retains_active_tool_rounds_across_pages_and_excludes_future_turn() -> None:
    """Recovery crosses page boundaries but cannot read a newer queued user prompt."""
    active = uuid4()
    future = uuid4()
    pages = {
        None: SimpleNamespace(items=[SimpleNamespace(sequence=199, role="user", turn_id=active)], next_sequence=199),
        199: SimpleNamespace(items=[SimpleNamespace(sequence=200, role="assistant", turn_id=active),
                                   SimpleNamespace(sequence=201, role="tool", turn_id=active)], next_sequence=201),
        201: SimpleNamespace(items=[SimpleNamespace(sequence=202, role="user", turn_id=future),
                                   SimpleNamespace(sequence=203, role="assistant", turn_id=future)], next_sequence=None),
    }
    runtime = object.__new__(OrganizationRuntime)
    runtime.store = SimpleNamespace(list_messages=lambda _owner, **kwargs: pages[kwargs["after_sequence"]])
    history = runtime._history_until_turn(SimpleNamespace(owner_id=uuid4(), turn_id=active))
    assert [item.sequence for item in history] == [199, 200, 201]


class _JournalStore:
    """Small journal double records terminal receipts without a database."""

    def __init__(self, previous=None):
        self.previous = previous
        self.starts = 0
        self.finishes = 0

    def journal_get(self, _turn_id, _call_id):
        return self.previous

    def journal_start(self, _turn, _call_id, _name, _arguments):
        self.starts += 1
        self.previous = SimpleNamespace(status="started")

    def journal_finish(self, _turn_id, _call_id, **values):
        self.finishes += 1
        self.previous = SimpleNamespace(**values)


@pytest.mark.parametrize("arguments", ['{"name":', '[]', '{"name":"x","arguments":[]}',
                                        '{"name":3}', '{"name":"x","extra":true}'])
def test_invalid_tool_envelope_returns_durable_correctable_error(arguments) -> None:
    """Invalid JSON/wrapper shapes never dispatch and replay the same error receipt."""
    runtime = object.__new__(OrganizationRuntime)
    runtime.store = _JournalStore()
    runtime.call_tool = lambda *_args: pytest.fail("invalid arguments must not dispatch")
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4())
    result = runtime._tool_result(turn, "call-invalid", "call_tool", arguments)
    assert result["error_code"] == "invalid_tool_arguments" and result["recoverable"] is True
    assert runtime._tool_result(turn, "call-invalid", "call_tool", arguments) == result
    assert runtime.store.starts == runtime.store.finishes == 1


@pytest.mark.parametrize("saved_result", [{}, {"ok": False, "error_code": "bridge_timeout", "recoverable": True}])
def test_failed_tool_journal_is_replayed_without_repeating_effect(saved_result) -> None:
    """A crash after a failed tool receipt cannot repeat an external side effect."""
    runtime = object.__new__(OrganizationRuntime)
    runtime.store = _JournalStore(SimpleNamespace(status="failed", result=saved_result, error_code="bridge_timeout"))
    runtime.call_tool = lambda *_args: pytest.fail("failed receipt must not dispatch again")
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4())
    result = runtime._tool_result(turn, "call-failed", "call_tool", '{"name":"send"}')
    assert result == {"ok": False, "error_code": "bridge_timeout", "recoverable": True}
    assert runtime.store.starts == runtime.store.finishes == 0


def test_started_tool_exception_has_same_durable_error_as_first_execution() -> None:
    """Started receipt recovery persists tool failures through the same error path."""
    runtime = object.__new__(OrganizationRuntime)
    runtime.store = _JournalStore(SimpleNamespace(status="started"))

    def fail(*_args):
        raise TimeoutError("private connection details")

    runtime.call_tool = fail
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4())
    result = runtime._tool_result(turn, "call-started", "call_tool", '{"name":"send"}')
    assert result == {"ok": False, "error_code": "tool_failed", "recoverable": True}
    assert runtime.store.previous.status == "failed" and runtime.store.previous.result == result
    assert runtime._tool_result(turn, "call-started", "call_tool", '{}') == result
    assert runtime.store.starts == 0 and runtime.store.finishes == 1


@pytest.mark.parametrize("calls", [[{"id": "x", "function": {"arguments": "{}"}}],
                                  [{"id": 1, "function": {"name": "x"}}],
                                  [{"id": "x", "function": {"name": "x"}}] * 2])
def test_provider_rejects_ambiguous_tool_identity(calls) -> None:
    """Missing names, non-string ids and duplicate ids cannot safely identify receipts."""
    with pytest.raises(OrganizationRuntimeError, match="provider_response_invalid"):
        OrganizationRuntime._parse_provider({"choices": [{"message": {"tool_calls": calls}}]})


def test_postgres_invalid_tool_arguments_can_be_corrected_in_same_turn() -> None:
    """The next paid round receives the persisted validation error and can answer normally."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)

    class CorrectingProvider(_FinalProvider):
        def complete(self, messages, tools, *, max_tokens):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {"content": "", "tool_calls": [{
                    "id": "call-invalid-json", "function": {"name": "call_tool", "arguments": "{"}
                }]}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
            errors = [json.loads(item["content"]) for item in messages if item.get("role") == "tool"]
            assert errors[-1]["error_code"] == "invalid_tool_arguments"
            return {"choices": [{"message": {"content": "Please clarify the requested action."}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}

    provider = CorrectingProvider()
    try:
        turn = store.create_turn(owner_id, "help me", "runtime-invalid-correction")
        runtime = OrganizationRuntime(store, provider=provider, registry=_EmptyRegistry(), max_rounds=2)
        result = runtime.run_turn(store.claim_turn_by_id(owner_id, turn.turn_id))
        assert result.status == "succeeded" and result.rounds == 2 and provider.calls == 2
        journal = store.journal_get(turn.turn_id, "call-invalid-json")
        assert journal is not None and journal.status == "completed"
        assert journal.result["error_code"] == "invalid_tool_arguments"
        assert sum(item.role == "tool" for item in store.list_messages(owner_id, limit=200).items) == 1
    finally:
        _delete_owner(database_url, owner_id)


@pytest.mark.parametrize("finish_reason,content", [("length", "partial answer"),
                                                   ("content_filter", "filtered"),
                                                   ("stop", ""), (None, "  ")])
def test_incomplete_provider_answer_cannot_succeed(finish_reason, content) -> None:
    """Truncation, filtering and empty final responses fail without another paid call."""
    with pytest.raises(OrganizationRuntimeError, match="provider_response_incomplete"):
        OrganizationRuntime._parse_provider({"choices": [{"finish_reason": finish_reason,
                                                         "message": {"content": content}}]})


def test_tool_validation_error_contains_only_bounded_fields_types_and_schema() -> None:
    """Correction metadata omits submitted values and raw exception messages."""
    from pydantic import BaseModel, Field

    class RequiredArgs(BaseModel):
        count: int = Field(gt=0)

    class SchemaRegistry:
        def schemas(self, names):
            assert names == ["task_start"]
            return [{"name": "task_start", "parameters": {"required": ["count"]}}]

    runtime = object.__new__(OrganizationRuntime)
    runtime.store = _JournalStore()
    runtime.registry = SchemaRegistry()

    def invalid(*_args):
        RequiredArgs.model_validate({"count": "private submitted data"})
        pytest.fail("model must reject invalid args")

    runtime.call_tool = invalid
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4())
    result = runtime._tool_result(turn, "invalid-schema", "call_tool", '{"name":"task_start"}')
    assert result["error_code"] == "invalid_tool_arguments"
    assert result["validation_errors"] == [{"field": "count", "type": "int_parsing"}]
    assert result["tool_schemas"][0]["name"] == "task_start"
    assert "private submitted data" not in json.dumps(result)
    assert runtime.store.previous.status == "failed"


def test_registry_argument_error_supplies_local_schema_without_second_dispatch() -> None:
    """Registry errors that suppress Pydantic details still expose the selected schema."""
    from agents_service.organization_tools import OrganizationToolError

    runtime = object.__new__(OrganizationRuntime)
    runtime.store = _JournalStore()
    runtime.registry = SimpleNamespace(schemas=lambda names: [{"name": names[0], "parameters": {}}])
    calls = 0

    def invalid(*_args):
        nonlocal calls
        calls += 1
        raise OrganizationToolError("invalid_tool_arguments", status=422)

    runtime.call_tool = invalid
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4())
    result = runtime._tool_result(turn, "registry-invalid", "call_tool", '{"name":"task_start"}')
    assert result["error_code"] == "invalid_tool_arguments"
    assert result["tool_schemas"] == [{"name": "task_start", "parameters": {}}]
    assert runtime._tool_result(turn, "registry-invalid", "call_tool", '{}') == result
    assert calls == 1


@pytest.mark.parametrize("content,finish_reason", [("", "stop"), ("unfinished", "length")])
def test_postgres_incomplete_answer_stops_without_paid_retry(content, finish_reason) -> None:
    """A billable incomplete response fails the turn rather than silently retrying or succeeding."""
    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)

    class IncompleteProvider(_FinalProvider):
        def complete(self, messages, tools, *, max_tokens):
            raw = super().complete(messages, tools, max_tokens=max_tokens)
            raw["choices"][0]["finish_reason"] = finish_reason
            return raw

    provider = IncompleteProvider(content)
    try:
        turn = store.create_turn(owner_id, "help me", "runtime-incomplete-answer")
        runtime = OrganizationRuntime(store, provider=provider, registry=_EmptyRegistry(), max_rounds=2)
        result = runtime.run_turn(store.claim_turn_by_id(owner_id, turn.turn_id))
        assert result.status == "failed" and result.error_code == "provider_response_incomplete"
        assert provider.calls == 1
        assert all(item.role != "assistant" for item in store.list_messages(owner_id, limit=200).items)
    finally:
        _delete_owner(database_url, owner_id)


@pytest.mark.parametrize("crash_after_ack", [False, True])
@pytest.mark.parametrize("independent_batch_calls", [False, True])
@pytest.mark.parametrize("candidate_first", [False, True])
@pytest.mark.parametrize("candidate_operation,wrapped", [
    ("candidate_chat_send", False), ("interview_propose", True), ("interview_accept", True),
])
def test_postgres_saved_background_task_finishes_foreground_without_more_paid_calls(
        monkeypatch: pytest.MonkeyPatch, crash_after_ack: bool, independent_batch_calls: bool,
        candidate_first: bool, candidate_operation: str, wrapped: bool) -> None:
    """Durable delegation closes foreground work and replays without duplicate tasks/effects."""
    from agents_service.organization_workflows import WorkflowToolRegistry

    database_url = _integration_url()
    owner_id = uuid4()
    store = OrganizationStore(database_url)

    class TaskProvider(_FinalProvider):
        def complete(self, messages, tools, *, max_tokens):
            self.calls += 1
            assert self.calls == 1, "saved task must not require another paid completion"
            calls = [
                {"id": "save-task", "function": {"name": "call_tool", "arguments": json.dumps({
                    "name": "task_start", "arguments": {"kind": "schedule_interview", "instruction": "Schedule in background",
                                                          "process_ids": [str(uuid4())], "availability": []}})}},
                {"id": "must-not-send", "function": {"name": "call_tool" if wrapped else candidate_operation,
                    "arguments": json.dumps({"name": candidate_operation, "arguments": {}}) if wrapped else "{}"}},
            ]
            if candidate_first:
                calls.reverse()
            if independent_batch_calls:
                calls.extend([
                    {"id": "remember-team", "function": {"name": "call_tool", "arguments": json.dumps({
                        "name": "memory_write", "arguments": {"expected_revision": 1, "value": {"team": "Synthetic"}}})}},
                    {"id": "save-second-task", "function": {"name": "call_tool", "arguments": json.dumps({
                        "name": "task_start", "arguments": {"kind": "inquiry", "instruction": "Ask in background",
                                                              "process_ids": [str(uuid4())]}})}},
                ])
            return {"choices": [{"message": {"content": "", "tool_calls": calls}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}

    provider = TaskProvider()
    try:
        turn = store.create_turn(owner_id, "Schedule in background", "runtime-background-handoff")
        runtime = OrganizationRuntime(store, provider=provider, registry=WorkflowToolRegistry(_EmptyRegistry(), store), max_rounds=1)
        claimed = store.claim_turn_by_id(owner_id, turn.turn_id)
        if crash_after_ack:
            original = store.append_message

            def crash_once(*args, **kwargs):
                appended = original(*args, **kwargs)
                if kwargs.get("idempotency_key", "").startswith("handoff:"):
                    raise RuntimeError("simulated crash after durable acknowledgment")
                return appended

            monkeypatch.setattr(store, "append_message", crash_once)
            with pytest.raises(RuntimeError, match="durable acknowledgment"):
                runtime._run(claimed)
            monkeypatch.setattr(store, "append_message", original)
            current = store.get_turn(owner_id, turn.turn_id)
            store.update_turn(owner_id, turn.turn_id, expected_lease_token=current.lease_token, status="retrying")
            claimed = store.claim_turn_by_id(owner_id, turn.turn_id)
        result = runtime.run_turn(claimed)
        assert result.status == "succeeded" and result.rounds == 1 and provider.calls == 1
        tasks = store.list_tasks(owner_id)
        assert len(tasks) == (2 if independent_batch_calls else 1) and all(task.status == "queued" for task in tasks)
        if independent_batch_calls:
            assert store.get_memory(owner_id).value == {"team": "Synthetic"}
            assert store.journal_get(turn.turn_id, "remember-team").status == "completed"
        messages = store.list_messages(owner_id, limit=200).items
        acknowledgments = [item for item in messages if item.idempotency_key and item.idempotency_key.startswith("handoff:")]
        assert len(acknowledgments) == 1
        assert "Задача сохранена для фоновой работы" in acknowledgments[0].content
        assert "выполнена" not in acknowledgments[0].content
        skipped = next(item for item in messages if item.tool_call_id == "must-not-send")
        assert json.loads(skipped.content)["error_code"] == "delegation_handoff"
        assert store.journal_get(turn.turn_id, "must-not-send") is None
    finally:
        _delete_owner(database_url, owner_id)


def test_unconfirmed_or_foreign_task_receipt_cannot_finish_foreground() -> None:
    """Model-shaped metadata is insufficient without this turn's owner-scoped receipt."""
    runtime = object.__new__(OrganizationRuntime)
    turn = SimpleNamespace(owner_id=uuid4(), turn_id=uuid4(), task_id=None, source="employer")
    for receipt in ({}, {"authorization_source_turn_id": str(uuid4()), "result": {"owner_id": str(turn.owner_id)}},
                    {"authorization_source_turn_id": str(turn.turn_id), "result": {"owner_id": str(uuid4())}}):
        assert runtime._delegated_task(turn, "task_start", {}, receipt) is None


def test_postgres_delegated_prompt_keeps_candidate_acceptance_in_own_interface() -> None:
    """A scheduling worker gives the model an explicit proposal/acceptance boundary."""
    from datetime import UTC, datetime, timedelta

    from agents_service.organization_models import OrganizationTaskRequest
    from agents_service.organization_workflows import (
        OrganizationWorkflows,
        WorkflowToolRegistry,
    )

    database_url = _integration_url()
    owner_id, process_id = uuid4(), uuid4()
    store = OrganizationStore(database_url)
    starts = datetime.now(UTC) + timedelta(days=1)

    class Bridge(_EmptyRegistry):
        def dispatch(self, _owner, name, arguments, *_args):
            if name == "recruitment_get":
                return {"candidate_id": str(uuid4()), "interviews": []}
            assert name == "candidate_chat_read"
            return {"messages": []}

    class BoundaryProvider(_FinalProvider):
        def complete(self, messages, tools, *, max_tokens):
            payload = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "user"))
            assert "Do not invoke interview_accept" in payload["rules"]
            assert "accept in their own interface and end this turn" in payload["rules"]
            assert "observe the real scheduled state" in payload["rules"]
            return super().complete(messages, tools, max_tokens=max_tokens)

    try:
        task = store.create_task(owner_id, OrganizationTaskRequest(kind="schedule_interview", instruction="Arrange interview",
            conversation_refs={"process_ids": [str(process_id)], "availability": [{
                "starts_at": starts.isoformat(), "ends_at": (starts + timedelta(hours=1)).isoformat(),
                "time_zone": "UTC", "duration_minutes": 30, "format": "remote", "location": "online"}]},
            idempotency_key="runtime-candidate-boundary"))
        registry = WorkflowToolRegistry(Bridge(), store)
        provider = BoundaryProvider()
        runtime = OrganizationRuntime(store, provider=provider, registry=registry)
        claimed = store.claim_task(owner_id=owner_id)
        OrganizationWorkflows(store, runtime, registry).process(claimed)
        assert provider.calls == 1
        assert store.get_task(owner_id, task.task_id).status == "waiting"
    finally:
        _delete_owner(database_url, owner_id)
