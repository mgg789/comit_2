"""Owner-scoped conversation clear, idle guards and durable worker fences."""

from __future__ import annotations

import os
from contextlib import nullcontext
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agents_service.database import connect
from agents_service.organization_models import (
    OrganizationChat,
    OrganizationMessagesClearRequest,
    OrganizationTaskRequest,
)
from agents_service.organization_routes import register_organization_routes
from agents_service.organization_store import OrganizationConflict, OrganizationStore


class _Rows:
    """Synthetic query results; no database or user records are touched."""

    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ClearConnection:
    """Exercise the production transactional clear against explicit in-memory SQL responses."""

    def __init__(self, owner, chat_id, *, turn_status=None, task_status=None, ambiguous=None):
        self.owner = owner
        self.chat_id = chat_id
        self.context = {"owner_id": owner, "revision": 7, "current_summary": {"last_answer": "old"}}
        self.turn_status = turn_status
        self.task_status = task_status
        self.ambiguous = ambiguous
        self.mutations = []

    def transaction(self):
        return nullcontext()

    def execute(self, statement, params):
        sql = " ".join(statement.split())
        if sql.startswith("SELECT"):
            assert params == (self.owner,), "Every clear read must use its owner key"
            if "SELECT chat_id" in sql:
                return _Rows([{"chat_id": self.chat_id}])
            if "organization_context" in sql:
                return _Rows([dict(self.context)])
            if "SELECT status FROM agents_service.organization_turns" in sql:
                return _Rows([{"status": self.turn_status}] if self.turn_status else [])
            if "SELECT status FROM agents_service.organization_tasks" in sql:
                return _Rows([{"status": self.task_status}] if self.task_status else [])
            if "organization_usage_reservations" in sql:
                return _Rows([{"reservation_id": uuid4()}] if self.ambiguous == "reservation" else [])
            if "organization_provider_calls" in sql:
                return _Rows([{"request_hash": "synthetic"}] if self.ambiguous == "provider" else [])
            if "organization_tool_journal" in sql:
                return _Rows([{"call_id": "synthetic"}] if self.ambiguous == "tool" else [])
            raise AssertionError(sql)
        self.mutations.append((sql, params))
        if "UPDATE agents_service.organization_context" in sql:
            self.context = {"owner_id": self.owner, "revision": 8, "current_summary": {}}
            return _Rows([dict(self.context)])
        return _Rows([])


@pytest.mark.parametrize("turn_status,task_status,ambiguous", [
    ("queued", None, None), ("processing", None, None), ("retrying", None, None),
    (None, "queued", None), (None, "processing", None), (None, "waiting", None),
    (None, "waiting_employer", None), (None, "retrying", None),
    (None, None, "reservation"), (None, None, "provider"), (None, None, "tool"),
])
def test_clear_busy_is_read_only(monkeypatch, turn_status, task_status, ambiguous):
    """Busy turns, waiting background tasks and ambiguous effects cannot be erased."""
    owner, chat_id = uuid4(), uuid4()
    db = _ClearConnection(owner, chat_id, turn_status=turn_status, task_status=task_status, ambiguous=ambiguous)
    store = OrganizationStore("unused", connection=db)
    monkeypatch.setattr(store, "get_or_create_chat", lambda _: OrganizationChat(owner_id=owner, chat_id=chat_id))
    with pytest.raises(OrganizationConflict, match="organization_chat_busy"):
        store.clear_messages(owner, 7)
    assert db.mutations == []
    assert db.context["revision"] == 7


def test_clear_context_cas_precedes_every_mutation(monkeypatch):
    """A confirmation for an older context cannot erase newer conversation state."""
    owner, chat_id = uuid4(), uuid4()
    db = _ClearConnection(owner, chat_id)
    store = OrganizationStore("unused", connection=db)
    monkeypatch.setattr(store, "get_or_create_chat", lambda _: OrganizationChat(owner_id=owner, chat_id=chat_id))
    with pytest.raises(OrganizationConflict, match="context_revision_conflict"):
        store.clear_messages(owner, 6)
    assert db.mutations == []


def test_clear_contract_rejects_unknown_fields_and_coerced_revisions():
    """The request needs a real positive CAS revision and carries no model authorization boolean."""
    assert OrganizationMessagesClearRequest(expected_context_revision=7).expected_context_revision == 7
    for body in ({}, {"expected_context_revision": "7"}, {"expected_context_revision": True},
                 {"expected_context_revision": 0}, {"expected_context_revision": 7, "owner_id": str(uuid4())},
                 {"expected_context_revision": 7, "human_confirmation": True}):
        with pytest.raises(ValueError):
            OrganizationMessagesClearRequest.model_validate(body)


def test_clear_route_is_owner_scoped_and_available_without_provider(monkeypatch):
    """Clear resolves authenticated owner, validates CAS and forwards stable busy errors."""
    import agents_service.organization_routes as routes

    owner = uuid4()
    seen = []

    class Store:
        def clear_messages(self, owner_id, revision):
            seen.append((owner_id, revision))
            if revision == 9:
                raise OrganizationConflict("organization_chat_busy")
            return {"owner_id": owner_id, "revision": revision + 1, "current_summary": {}}

    monkeypatch.setattr(routes, "_store", lambda _: Store())
    app = FastAPI()

    def offline(_):
        raise AssertionError("Clearing history must not invoke a provider")

    register_organization_routes(app, lambda: owner, lambda: object(), capability_check=offline)
    client = TestClient(app)
    response = client.request("DELETE", "/internal/organization-agent/messages", json={"expected_context_revision": 7})
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(owner)
    assert response.json()["revision"] == 8
    assert client.request("DELETE", "/internal/organization-agent/messages", json={"expected_context_revision": 9}).json() == {"detail": "organization_chat_busy"}
    assert client.request("DELETE", "/internal/organization-agent/messages", json={"expected_context_revision": 7, "owner_id": str(uuid4())}).status_code == 422
    assert seen == [(owner, 7), (owner, 9)]
    locked = FastAPI()

    def unauthenticated():
        raise HTTPException(401, "unauthenticated")

    register_organization_routes(locked, unauthenticated, lambda: object())
    assert TestClient(locked).request("DELETE", "/internal/organization-agent/messages", json={"expected_context_revision": 7}).status_code == 401


@pytest.mark.integration
def test_clear_keeps_finances_memory_documents_tasks_and_foreign_history():
    """Only random synthetic owners in the explicit integration database are created and removed."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    from agents_service.organization_documents import DocumentStore, DocumentUpload

    owner, foreign = uuid4(), uuid4()
    store = OrganizationStore(database_url)
    try:
        store.get_or_create_chat(owner)
        store.append_message(foreign, role="user", content="foreign synthetic history")
        memory = store.patch_memory(owner, 1, {"company": "synthetic"})
        document = DocumentStore(database_url).upload(owner, DocumentUpload(name="synthetic.md", content_base64="c3ludGhldGlj", idempotency_key="clear-document"))
        turn = store.create_turn(owner, "synthetic question", "clear-turn")
        turn = store.claim_turn_by_id(owner, turn.turn_id)
        store.journal_start(turn, "synthetic-call", "taxonomy", {})
        store.journal_finish(turn.turn_id, "synthetic-call", status="completed", result={})
        store.append_message(owner, role="assistant", turn_id=turn.turn_id, content="synthetic answer")
        store.update_turn(owner, turn.turn_id, status="succeeded")
        context = store.set_context(owner, {"last_answer": "synthetic answer"})
        task = store.create_task(owner, OrganizationTaskRequest(kind="inquiry", instruction="synthetic task",
            conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key="clear-task"))
        task = store.claim_task(owner_id=owner)
        assert task is not None
        stale_task = task
        store.stop_task(owner, task.task_id)
        reservation = store.reserve_usage(owner, turn.turn_id, Decimal(2), input_bound=Decimal(1), output_bound=Decimal(1))
        store.provider_begin(owner, "synthetic-clear-response", reservation)
        store.provider_complete(owner, "synthetic-clear-response", {"synthetic": True}, input_tokens=1, output_tokens=1)
        store.capture_usage(owner, reservation, input_tokens=1, output_tokens=1,
            input_micro_usd=Decimal(1), output_micro_usd=Decimal(1), rate_config={"synthetic": True})
        budget = store.get_budget(owner, pricing_configured=True)
        result = store.clear_messages(owner, context.revision)
        assert result.revision == context.revision + 1 and result.current_summary == {}
        assert store.list_messages(owner).items == []
        assert store.list_pending_answers(owner) == []
        assert store.get_turn(owner, turn.turn_id) is None
        assert store.journal_get(turn.turn_id, "synthetic-call") is None
        assert store.get_memory(owner) == memory
        assert DocumentStore(database_url).read(owner, document.document_id).text == "synthetic"
        assert store.get_budget(owner, pricing_configured=True).spent_micro_usd == budget.spent_micro_usd
        assert store.get_task(owner, task.task_id).status == "stopped"
        assert store.get_task(owner, task.task_id).lease_token is None
        assert store.list_messages(foreign).items[0].content == "foreign synthetic history"
        assert store.provider_begin(owner, "synthetic-clear-response", reservation) == {"synthetic": True}
        with pytest.raises(OrganizationConflict, match="organization_turn_not_found"):
            store.append_message(owner, role="assistant", turn_id=turn.turn_id, content="stale answer")
        with pytest.raises(OrganizationConflict, match="organization_task_lease_lost"):
            store.append_message(owner, role="assistant", content="stale task report", task_id=task.task_id, task_lease_token=stale_task.lease_token)
        with pytest.raises(OrganizationConflict, match="organization_task_lease_lost"):
            store.ask_owner(stale_task, "stale question")
        with pytest.raises(OrganizationConflict, match="organization_lease_lost"):
            store.create_task(owner, OrganizationTaskRequest(kind="inquiry", instruction="stale task",
                conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key="stale-task"), source_turn_id=turn.turn_id)
        assert store.list_messages(owner).items == []
        new = store.create_turn(owner, "new synthetic question", "clear-turn")
        assert new.turn_id != turn.turn_id
    finally:
        with connect(database_url) as db:
            db.execute("DELETE FROM agents_service.organization_provider_calls WHERE owner_id IN (%s,%s)", (owner, foreign))
            db.execute("DELETE FROM agents_service.organization_documents WHERE owner_id IN (%s,%s)", (owner, foreign))
            db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id IN (%s,%s)", (owner, foreign))


@pytest.mark.integration
@pytest.mark.parametrize("busy", ["turn", "task", "reservation", "provider", "tool"])
def test_clear_busy_and_stale_revision_preserve_synthetic_pg_history(busy):
    """The real transaction leaves synthetic history intact on CAS or work conflicts."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner = uuid4()
    store = OrganizationStore(database_url)
    try:
        turn = store.create_turn(owner, "synthetic preserved question", "busy-clear")
        turn = store.claim_turn_by_id(owner, turn.turn_id)
        context = store.set_context(owner, {"synthetic": "retained"})
        if busy == "tool":
            store.journal_start(turn, "ambiguous-call", "taxonomy", {})
        if busy != "turn":
            store.update_turn(owner, turn.turn_id, status="failed")
        if busy == "task":
            store.create_task(owner, OrganizationTaskRequest(kind="inquiry", instruction="synthetic pending task",
                conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key="busy-task"))
        if busy in {"reservation", "provider"}:
            reservation = store.reserve_usage(owner, turn.turn_id, Decimal(2), input_bound=Decimal(1), output_bound=Decimal(1))
            if busy == "provider":
                store.provider_begin(owner, "synthetic-ambiguous-response", reservation)
                store.release_usage(owner, reservation)
        before = store.list_messages(owner)
        with pytest.raises(OrganizationConflict, match="context_revision_conflict"):
            store.clear_messages(owner, context.revision - 1)
        with pytest.raises(OrganizationConflict, match="organization_chat_busy"):
            store.clear_messages(owner, context.revision)
        assert store.list_messages(owner) == before
        assert store.get_context(owner).current_summary == {"synthetic": "retained"}
        assert store.get_context(owner).revision == context.revision
    finally:
        with connect(database_url) as db:
            db.execute("DELETE FROM agents_service.organization_provider_calls WHERE owner_id=%s", (owner,))
            db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner,))


@pytest.mark.integration
def test_task_start_fences_replaced_and_expired_source_leases():
    """A stale turn executor cannot delegate work after ownership changes."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner = uuid4()
    store = OrganizationStore(database_url)
    try:
        turn = store.create_turn(owner, "synthetic delegation", "lease-fence-turn")
        claimed = store.claim_turn_by_id(owner, turn.turn_id)
        assert claimed is not None
        request = OrganizationTaskRequest(kind="inquiry", instruction="synthetic task",
            conversation_refs={"process_ids": [str(uuid4())]}, idempotency_key="lease-fence-task")
        with connect(database_url) as db:
            db.execute("UPDATE agents_service.organization_turns SET lease_token=%s WHERE turn_id=%s", (uuid4(), turn.turn_id))
        with pytest.raises(OrganizationConflict, match="organization_lease_lost"):
            store.create_task(owner, request, source_turn_id=turn.turn_id, source_lease_token=claimed.lease_token)
        with connect(database_url) as db:
            db.execute("UPDATE agents_service.organization_turns SET lease_token=%s,lease_until=now()-interval '1 second' WHERE turn_id=%s", (claimed.lease_token, turn.turn_id))
        with pytest.raises(OrganizationConflict, match="organization_lease_lost"):
            store.create_task(owner, request, source_turn_id=turn.turn_id, source_lease_token=claimed.lease_token)
        assert store.list_tasks(owner) == []
    finally:
        with connect(database_url) as db:
            db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner,))


def test_clear_openapi_documents_conflicts_and_preserves_budget_schema():
    """Generated consumers see clear conflicts without unrelated money-schema drift."""
    from agents_service.main import create_app

    spec = create_app().openapi()
    assert "409" in spec["paths"]["/internal/organization-agent/messages"]["delete"]["responses"]
    schemas = spec["components"]["schemas"]
    assert set(schemas["OrganizationClearConflict"]["properties"]["detail"]["enum"]) == {
        "organization_chat_busy", "context_revision_conflict",
    }
    for field in ("limit", "spent", "reserved", "available"):
        assert schemas["OrganizationBudget"]["properties"][field + "_micro_usd"]["pattern"]
