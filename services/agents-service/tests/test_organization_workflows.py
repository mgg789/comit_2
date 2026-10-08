"""Unit coverage for the organization worker and durable workflow handoff."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents_service.database import connect
from agents_service.organization_models import OrganizationTaskRequest
from agents_service.organization_store import (
    OrganizationConflict,
    OrganizationNotFound,
    OrganizationStore,
)
from agents_service.organization_worker import OrganizationWorker
from agents_service.organization_workflows import (
    OrganizationWorkflows,
    WorkflowToolRegistry,
)


class BaseRegistry:
    """Minimal base registry proving the worker installs its workflow wrapper."""

    def catalog(self):
        return []

    def schemas(self, _names):
        return []

    def dispatch(self, *_args, **_kwargs):
        return {"result": {}}


class FakeRuntime:
    """Injectable runtime that can hold a worker lease open for heartbeat tests."""

    def __init__(self, started: threading.Event | None = None, release: threading.Event | None = None, error: BaseException | None = None):
        self.registry = None
        self.started = started
        self.release = release
        self.error = error

    def run_turn(self, _turn):
        if self.error is not None:
            raise self.error
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(2)


class FakeWorkflows:
    """Injected workflow runner used to assert task status ownership stays in workflows."""

    def __init__(self, started: threading.Event | None = None, release: threading.Event | None = None, error: BaseException | None = None):
        self.started = started
        self.release = release
        self.error = error
        self.calls = []

    def process(self, task):
        self.calls.append(task)
        if self.started is not None:
            self.started.set()
        if self.error is not None:
            raise self.error
        if self.release is not None:
            self.release.wait(2)


class FakeStore:
    """Small lease-aware store double; every heartbeat call remains observable."""

    database_url = "postgresql://unused"

    def __init__(self, *, turn=None, task=None, current_task=None, current_turn=None):
        self.turn = turn
        self.task = task
        self.current_task = current_task or task
        self.current_turn = current_turn
        self.heartbeats = []
        self.updates = []

    def claim_turn(self, *, lease_seconds):
        value, self.turn = self.turn, None
        return value

    def claim_task(self, *, lease_seconds):
        value, self.task = self.task, None
        return value

    def heartbeat(self, owner_id, resource_id, lease_token, *, task=False, lease_seconds=300):
        self.heartbeats.append((owner_id, resource_id, lease_token, task, lease_seconds))
        return True

    def get_task(self, _owner_id, _task_id):
        return self.current_task

    def get_turn(self, _owner_id, _turn_id):
        return self.current_turn

    def update_task(self, owner_id, task_id, **values):
        self.updates.append((owner_id, task_id, values))

    def update_turn(self, owner_id, turn_id, **values):
        self.updates.append((owner_id, turn_id, values))


def _resource(*, result=None):
    return SimpleNamespace(owner_id=uuid4(), task_id=uuid4(), turn_id=uuid4(), lease_token=uuid4(),
                           status="processing", result=result)


def _wait_for(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def test_worker_installs_workflow_registry_and_does_not_fake_task_completion():
    """Task status is advanced by OrganizationWorkflows, not a synthetic turn result."""
    task = _resource(result={})
    store = FakeStore(task=task)
    runtime = FakeRuntime()
    workflows = FakeWorkflows()
    worker = OrganizationWorker(store, runtime, lease_seconds=30, registry=BaseRegistry(), workflows=workflows)

    assert worker.runtime is runtime
    assert runtime.registry is worker.workflow_registry
    assert worker.process_once() is True
    assert workflows.calls == [task]
    assert store.updates == []


def test_normal_turn_heartbeat_refreshes_the_processing_lease():
    """A provider call longer than the polling interval keeps its turn lease alive."""
    started = threading.Event()
    release = threading.Event()
    turn = _resource()
    store = FakeStore(turn=turn)
    worker = OrganizationWorker(store, FakeRuntime(started, release), lease_seconds=30, registry=BaseRegistry())
    worker.heartbeat_interval = 0.01
    thread = threading.Thread(target=worker.process_once)
    thread.start()
    _wait_for(started.is_set)
    _wait_for(lambda: any(not item[3] for item in store.heartbeats))
    release.set()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_normal_turn_failure_uses_stable_error_code():
    """An unexpected turn exception cannot stop the worker or persist its text."""
    turn = _resource()
    store = FakeStore(turn=turn)
    error = RuntimeError("provider response contains private data")
    worker = OrganizationWorker(store, FakeRuntime(error=error), lease_seconds=30, registry=BaseRegistry())
    assert worker.process_once() is True
    assert store.updates[0][2]["error_code"] == "organization_workflow_failed"
    assert "private data" not in repr(store.updates)


def test_task_heartbeat_refreshes_task_and_associated_processing_turn():
    """Workflow processing extends both the task lease and its active turn lease."""
    turn = _resource()
    task = _resource(result={"processes": {"process": {"turn_id": str(turn.turn_id)}}})
    store = FakeStore(task=task, current_task=task, current_turn=turn)
    started = threading.Event()
    release = threading.Event()
    workflows = FakeWorkflows(started, release)
    worker = OrganizationWorker(store, FakeRuntime(), lease_seconds=30, registry=BaseRegistry(), workflows=workflows)
    worker.heartbeat_interval = 0.01
    thread = threading.Thread(target=worker.process_once)
    thread.start()
    _wait_for(started.is_set)
    _wait_for(lambda: any(item[3] for item in store.heartbeats) and any(not item[3] for item in store.heartbeats))
    release.set()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_worker_persists_stable_workflow_error_code_without_exception_text():
    """Durable failures use a stable code and never persist arbitrary exception text."""
    task = _resource(result={})
    error = RuntimeError("provider secret must never be persisted")
    error.code = "workflow_provider_unavailable"
    store = FakeStore(task=task)
    worker = OrganizationWorker(store, FakeRuntime(), lease_seconds=30, registry=BaseRegistry(),
                                workflows=FakeWorkflows(error=error))
    assert worker.process_once() is True
    assert store.updates[0][2] == {"status": "retrying", "error_code": "workflow_provider_unavailable", "lease_token": task.lease_token}
    assert "secret" not in repr(store.updates)

    task = _resource(result={})
    store = FakeStore(task=task)
    worker = OrganizationWorker(store, FakeRuntime(), lease_seconds=30, registry=BaseRegistry(),
                                workflows=FakeWorkflows(error=RuntimeError("private payload")))
    assert worker.process_once() is True
    assert store.updates[0][2]["error_code"] == "organization_workflow_failed"
    assert "private payload" not in repr(store.updates)


def _cleanup_owner(database_url: str, owner_id):
    """Remove only this test's owner graph; never truncate shared workflow tables."""
    with connect(database_url) as connection:
        connection.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner_id,))


def _workflow(database_url: str, store: OrganizationStore, base: BaseRegistry, runtime=None) -> OrganizationWorkflows:
    """Build the production workflow wrapper around a deterministic test registry."""
    return OrganizationWorkflows(store, runtime or FakeRuntime(), WorkflowToolRegistry(base, store), poll_seconds=30)


@pytest.mark.integration
def test_pg_waiting_schedule_asks_once_without_calling_llm():
    """Missing availability becomes one durable owner question and a waiting task."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    process_id = uuid4()
    store = OrganizationStore(database_url)
    try:
        store.create_task(owner_id, OrganizationTaskRequest(kind="schedule_interview", instruction="Arrange interviews",
            conversation_refs={"process_ids": [str(process_id)]}, idempotency_key=str(uuid4())))
        task = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert task is not None
        _workflow(database_url, store, BaseRegistry()).process(task)
        current = store.get_task(owner_id, task.task_id)
        assert current.status == "waiting_employer"
        assert len(store.list_pending_answers(owner_id)) == 1
        # A second wake-up sees the non-processing lease and cannot create a second question.
        _workflow(database_url, store, BaseRegistry()).process(current)
        assert len(store.list_pending_answers(owner_id)) == 1
    finally:
        _cleanup_owner(database_url, owner_id)


@pytest.mark.integration
def test_pg_multi_inquiry_scheduled_report_is_durable():
    """A multi-process schedule closes only after every real interview is scheduled."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    process_ids = [uuid4(), uuid4()]
    base = BaseRegistry()
    base.dispatch = lambda _owner, name, arguments, *_args: {"interviews": [{"kind": "general", "state": "scheduled"}]} if name == "recruitment_get" else {}
    store = OrganizationStore(database_url)
    starts = datetime.now(UTC) + timedelta(days=1)
    try:
        store.create_task(owner_id, OrganizationTaskRequest(kind="schedule_interview", instruction="Schedule both",
            conversation_refs={"process_ids": [str(value) for value in process_ids], "availability": [{
                "starts_at": starts.isoformat(), "ends_at": (starts + timedelta(hours=1)).isoformat(),
                "time_zone": "UTC", "duration_minutes": 30, "format": "remote", "location": "online", "kind": "general",
            }]}, idempotency_key=str(uuid4())))
        task = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert task is not None
        _workflow(database_url, store, base).process(task)
        current = store.get_task(owner_id, task.task_id)
        assert current.status == "succeeded"
        messages = store.list_messages(owner_id, limit=20).items
        report = [message.content for message in messages if message.role == "assistant" and "Собеседования подтверждены" in message.content]
        assert report and all(str(value) in report[-1] for value in process_ids)
    finally:
        _cleanup_owner(database_url, owner_id)


@pytest.mark.integration
def test_pg_standin_presence_stops_task_and_fences_expired_lease():
    """A returned employer stops stand-in work; an expired task cannot be advanced."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    store = OrganizationStore(database_url)
    try:
        store.get_or_create_chat(owner_id)
        store.set_presence(owner_id, True)
        task = store.create_task(owner_id, OrganizationTaskRequest(kind="stand_in", instruction="Reply while I am away",
            conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key=str(uuid4())))
        claimed = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert claimed is not None
        _workflow(database_url, store, BaseRegistry()).process(claimed)
        waiting = store.get_task(owner_id, task.task_id)
        assert waiting.status == "waiting"
        assert not (waiting.result or {}).get("activated")

        # Arm the stand-in only after one offline wake-up, then stop it when
        # the employer returns during the same delegated task.
        store.set_presence(owner_id, False)
        with connect(database_url) as connection:
            connection.execute("UPDATE agents_service.organization_tasks SET next_run_at=now() WHERE task_id=%s", (task.task_id,))
        armed_claim = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert armed_claim is not None
        _workflow(database_url, store, BaseRegistry()).process(armed_claim)
        armed = store.get_task(owner_id, task.task_id)
        assert armed.status == "waiting"
        assert (armed.result or {}).get("activated") is True

        store.set_presence(owner_id, True)
        with connect(database_url) as connection:
            connection.execute("UPDATE agents_service.organization_tasks SET next_run_at=now() WHERE task_id=%s", (task.task_id,))
        returned_claim = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert returned_claim is not None
        _workflow(database_url, store, BaseRegistry()).process(returned_claim)
        assert store.get_task(owner_id, task.task_id).stop_reason == "employer_returned"

        expired = store.create_task(owner_id, OrganizationTaskRequest(kind="inquiry", instruction="Wait for candidate",
            conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key=str(uuid4())))
        expired_claim = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert expired_claim is not None
        with connect(database_url) as connection:
            connection.execute("UPDATE agents_service.organization_tasks SET lease_until=now()-interval '1 second' WHERE task_id=%s", (expired.task_id,))
        _workflow(database_url, store, BaseRegistry()).process(expired_claim)
        assert store.get_task(owner_id, expired.task_id).status == "processing"
    finally:
        _cleanup_owner(database_url, owner_id)


@pytest.mark.integration
def test_pg_candidate_event_cursor_is_saved_after_one_processing_page():
    """Candidate event pages advance a durable cursor for the next workflow wake-up."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    process_id = uuid4()
    candidate_id = uuid4()
    store = OrganizationStore(database_url)

    class EventBase(BaseRegistry):
        def dispatch(self, _owner, name, arguments, *_args):
            if name == "recruitment_get":
                return {"candidate_id": str(candidate_id), "interviews": []}
            if name == "candidate_chat_read":
                return {"items": [{"sequence": 7, "sender_id": str(candidate_id), "text": "I am available"}]}
            return {}

    class FinishingRuntime(FakeRuntime):
        def run_turn(self, turn):
            self.store.update_turn(turn.owner_id, turn.turn_id, expected_lease_token=turn.lease_token, status="succeeded")

    runtime = FinishingRuntime()
    runtime.store = store
    try:
        task = store.create_task(owner_id, OrganizationTaskRequest(kind="inquiry", instruction="Answer the candidate",
            conversation_refs={"process_ids": [str(process_id)]}, idempotency_key=str(uuid4())))
        claimed = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert claimed is not None
        _workflow(database_url, store, EventBase(), runtime).process(claimed)
        current = store.get_task(owner_id, task.task_id)
        assert current.status == "waiting"
        assert current.result and current.result["processes"][str(process_id)]["cursor"] == 7
    finally:
        _cleanup_owner(database_url, owner_id)


def _availability(start: datetime | None = None) -> dict:
    """Return a valid employer slot shared by the persistence integration cases."""
    starts_at = start or (datetime.now(UTC) + timedelta(days=1))
    return {
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
        "time_zone": "UTC",
        "duration_minutes": 30,
        "format": "remote",
        "location": "online",
        "kind": "general",
    }


@pytest.mark.integration
def test_pg_pending_availability_answer_resumes_once_and_is_owner_scoped():
    """One availability answer atomically wakes a task and fences conflicting retries."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    other_owner = uuid4()
    store = OrganizationStore(database_url)
    try:
        store.create_task(owner_id, OrganizationTaskRequest(
            kind="schedule_interview", instruction="Find a time",
            conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key=str(uuid4())))
        claimed = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert claimed is not None
        _workflow(database_url, store, BaseRegistry()).process(claimed)
        waiting = store.get_task(owner_id, claimed.task_id)
        assert waiting.status == "waiting_employer"
        pending = store.list_pending_answers(owner_id)
        assert len(pending) == 1

        answer = {"availability": [_availability()]}
        resumed_answer = store.answer_pending(owner_id, pending[0].answer_id, answer)
        resumed = store.get_task(owner_id, claimed.task_id)
        assert resumed_answer.answered is True
        assert resumed.status == "waiting"
        assert resumed.next_run_at is not None
        assert resumed.next_run_at <= datetime.now(UTC) + timedelta(seconds=5)
        assert (resumed.result or {}).get("generation") == 1
        assert (resumed.conversation_refs or {}).get("availability")

        repeated_answer = store.answer_pending(owner_id, pending[0].answer_id, answer)
        repeated = store.get_task(owner_id, claimed.task_id)
        assert repeated_answer.answer == resumed_answer.answer
        assert (repeated.result or {}).get("generation") == 1
        with pytest.raises(OrganizationConflict, match="pending_answer_conflict"):
            store.answer_pending(owner_id, pending[0].answer_id, {"availability": [_availability(datetime.now(UTC) + timedelta(days=2))]})
        with pytest.raises(OrganizationNotFound, match="organization_chat_not_found|pending_answer_not_found"):
            store.answer_pending(other_owner, pending[0].answer_id, answer)
    finally:
        _cleanup_owner(database_url, owner_id)
        _cleanup_owner(database_url, other_owner)


@pytest.mark.integration
def test_pg_inquiry_task_complete_is_per_process_and_report_is_durable():
    """Each inquiry process needs its live bound turn; the final report survives restart reads."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    process_ids = [uuid4(), uuid4()]
    store = OrganizationStore(database_url)
    try:
        created = store.create_task(owner_id, OrganizationTaskRequest(
            kind="inquiry", instruction="Collect candidate answers",
            conversation_refs={"process_ids": [str(value) for value in process_ids]}, idempotency_key=str(uuid4())))
        task = store.claim_task(owner_id=owner_id, lease_seconds=30)
        assert task is not None

        registry = WorkflowToolRegistry(BaseRegistry(), store)
        first = store.create_turn(owner_id, "Process first", str(uuid4()), task_id=task.task_id,
                                  task_lease_token=task.lease_token)
        first = store.claim_turn_by_id(owner_id, first.turn_id, lease_seconds=30)
        first = store.bind_task_turn(first, task)
        store.update_task(owner_id, task.task_id, status="processing", result={
            **(task.result or {}), "processes": {str(process_ids[0]): {"turn_id": str(first.turn_id)}}},
            lease_token=task.lease_token)
        registry.bind_turn(first)
        first_result = registry.dispatch(str(owner_id), "task_complete", {
            "outcome": "completed", "report": "First candidate report", "process_id": process_ids[0]},
            "complete:first")
        assert first_result["result"]["status"] == "processing"
        after_first = store.get_task(owner_id, task.task_id)
        assert after_first.status == "processing"
        assert (after_first.result or {}).get("processes", {}).get(str(process_ids[0]), {}).get("completed") is True
        store.update_turn(owner_id, first.turn_id, expected_lease_token=first.lease_token, status="succeeded")

        second = store.create_turn(owner_id, "Process second", str(uuid4()), task_id=task.task_id,
                                   task_lease_token=task.lease_token)
        second = store.claim_turn_by_id(owner_id, second.turn_id, lease_seconds=30)
        second = store.bind_task_turn(second, after_first)
        progress = dict(after_first.result or {})
        processes = dict(progress.get("processes", {}))
        processes[str(process_ids[1])] = {"turn_id": str(second.turn_id)}
        progress["processes"] = processes
        store.update_task(owner_id, task.task_id, status="processing", result=progress, lease_token=task.lease_token)
        registry.bind_turn(second)
        second_result = registry.dispatch(str(owner_id), "task_complete", {
            "outcome": "completed", "report": "Second candidate report", "process_id": process_ids[1]},
            "complete:second")
        assert second_result["result"]["status"] == "succeeded"
        finished = store.get_task(owner_id, task.task_id)
        assert finished.status == "succeeded"
        assert all((finished.result or {}).get("processes", {}).get(str(value), {}).get("completed") for value in process_ids)
        reports = [message.content for message in store.list_messages(owner_id, limit=50).items
                   if message.role == "assistant" and "candidate report" in message.content]
        assert reports == ["First candidate report", "Second candidate report"]
        assert created.task_id == task.task_id
    finally:
        _cleanup_owner(database_url, owner_id)
