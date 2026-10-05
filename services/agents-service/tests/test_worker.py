"""PostgreSQL fault injection proves atomic tools, replay, fencing and manual-edit safety."""

import json
import os
from uuid import uuid4

import pytest
from agents_service.agent_bridge import AtomicAgentStore
from agents_service.agent_runtime import load_vacancy_definition
from agents_service.agent_store import AgentStore
from agents_service.database import connect, migrate
from agents_service.main import create_app
from agents_service.models import SchemaSnapshot
from agents_service.providers import ProviderError
from agents_service.store import Store
from agents_service.worker import Worker
from fastapi.testclient import TestClient


class Registry:
    """Explicit unit transport; production acceptance uses real matching HTTP."""

    def schema(self):
        return SchemaSnapshot(version=1, schema_hash="a" * 64,
                              coordinates=[{"index": 0, "group": "stack", "key": "python"}])


class NoLaya:
    """Any Laya invocation from the chat path is a regression."""

    def derive(self, *_args):
        raise AssertionError("agent must not call Laya")


class ScriptedProvider:
    """Deterministic fault-test responses, never used by product runtime."""

    model = "fault-test"

    def __init__(self, messages, before=None):
        self.messages = list(messages)
        self.before = before
        self.calls = 0

    def complete(self, messages, tools, max_tokens=1536):
        self.calls += 1
        if self.before:
            self.before()
            self.before = None
        # Every provider tool message must have its corresponding assistant call.
        ids = set()
        for message in messages:
            ids.update(item["id"] for item in message.get("tool_calls", []))
            if message["role"] == "tool":
                assert message["tool_call_id"] in ids
        return {"choices": [{"message": self.messages.pop(0)}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def tool(name, args, call_id):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def setup():
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL required")
    migrate(url)
    with connect(url) as connection:
        connection.execute("TRUNCATE agents_service.drafts CASCADE")
    store = Store(url)
    owner = uuid4()
    draft = store.create(owner, {"title": "Backend", "city": "Москва", "daily": "5/2", "type": "remote",
        "zp_min": 100, "zp_max": 200, "class": "backend"}, Registry().schema())
    with connect(url) as connection:
        agent = AgentStore(connection)
        session = agent.create_session(owner_id=owner, draft_id=draft.draft_id, draft_revision=1,
                                       definition=load_vacancy_definition())
        turn = agent.append_turn(session_id=session.session_id, owner_id=owner, draft_id=draft.draft_id,
            message="Fill and approve", expected_revision=1, idempotency_key="first")
        job = store.enqueue_in_connection(connection, owner, "agent_turn", {"turn_id": str(turn.turn_id)},
            draft_id=draft.draft_id, session_id=session.session_id, idempotency_key="first")
    return store, owner, draft, turn, job


def responses():
    return [tool("set_fields", {"fields": {"zp_min": 120}}, "patch"),
            tool("approve", {"expected_revision": 2}, "approve"), {"role": "assistant", "content": "Summary ready"}]


def test_agent_shares_form_fields_and_never_publishes(setup):
    store, owner, draft, _turn, job = setup
    provider = ScriptedProvider(responses())
    assert Worker(store, Registry(), NoLaya(), provider).process_once()
    result = store.get(owner, draft.draft_id)
    assert result.state == "summary" and result.revision == 2
    assert result.fields.zp_min == 120 and result.publication is None
    assert store.get_job(owner, job.job_id).state == "succeeded"
    assert provider.calls == 3


class SimulatedCrash(BaseException):
    """Bypass normal error handling to emulate loss of the worker process."""


def test_restart_after_atomic_effect_before_tool_message(setup, monkeypatch):
    store, owner, draft, _turn, job = setup
    provider = ScriptedProvider(responses())
    worker = Worker(store, Registry(), NoLaya(), provider)
    original = AtomicAgentStore.append_message

    def crash(self, *args, **kwargs):
        if kwargs.get("role") == "tool":
            raise SimulatedCrash()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AtomicAgentStore, "append_message", crash)
    with pytest.raises(SimulatedCrash):
        worker.process_once()
    assert store.get(owner, draft.draft_id).revision == 2
    monkeypatch.setattr(AtomicAgentStore, "append_message", original)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE agents_service.jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s", (job.job_id,))
    assert worker.process_once()
    result = store.get(owner, draft.draft_id)
    assert result.state == "summary" and result.revision == 2, "patch must not run twice"
    assert store.get_job(owner, job.job_id).attempts == 2
    assert provider.calls == 3


def test_lost_lease_cannot_write_provider_result_or_draft(setup):
    store, owner, draft, turn, job = setup
    replacement = uuid4()

    def revoke():
        with connect(store.database_url) as connection:
            connection.execute("UPDATE agents_service.jobs SET lease_token=%s WHERE job_id=%s", (replacement, job.job_id))

    provider = ScriptedProvider(responses(), revoke)
    assert Worker(store, Registry(), NoLaya(), provider).process_once()
    assert store.get(owner, draft.draft_id).revision == 1
    assert store.get_job(owner, job.job_id).lease_token == replacement
    with connect(store.database_url) as connection:
        saved = AgentStore(connection).get_turn(turn.turn_id, owner)
        assert saved.provider_result is None


def test_manual_edit_during_provider_call_wins(setup):
    store, owner, draft, _turn, job = setup

    def edit():
        store.patch(owner, draft.draft_id, {"expected_revision": 1, "fields": {"zp_min": 150}}, Registry().schema())

    provider = ScriptedProvider(responses(), edit)
    assert Worker(store, Registry(), NoLaya(), provider).process_once()
    assert store.get(owner, draft.draft_id).fields.zp_min == 150
    failed = store.get_job(owner, job.job_id)
    assert failed.state == "failed" and failed.error == "revision_conflict"


def test_changed_knowledge_fails_before_provider(setup):
    store, owner, _draft, _turn, job = setup
    with connect(store.database_url) as connection:
        connection.execute("UPDATE agents_service.agent_sessions SET knowledge_hash=%s", ("0" * 64,))
    provider = ScriptedProvider(responses())
    assert Worker(store, Registry(), NoLaya(), provider).process_once()
    assert store.get_job(owner, job.job_id).error == "knowledge_version_mismatch"
    assert provider.calls == 0


def test_provider_outage_records_error_without_fabricating_fields(setup):
    store, owner, draft, _turn, job = setup

    def outage():
        raise ProviderError("provider_unavailable")

    assert Worker(store, Registry(), NoLaya(), ScriptedProvider([], outage)).process_once()
    value = store.get_job(owner, job.job_id)
    assert value.state == "retrying" and value.error == "provider_unavailable"
    assert store.get(owner, draft.draft_id).revision == 1


def ask_slider(store):
    """Produce a real persisted interaction through the leased tool executor."""
    messages = [tool("show_interaction", {"kind": "slider", "prompt": "Уровень Python?",
        "field_target": "stacks.python", "min_value": 0, "max_value": 7}, "question"),
        {"role": "assistant", "content": "Выберите уровень"}]
    assert Worker(store, Registry(), NoLaya(), ScriptedProvider(messages)).process_once()


def test_interaction_applies_same_fields_and_replays_response(setup):
    store, owner, draft, turn, _job = setup
    ask_slider(store)
    with connect(store.database_url) as connection:
        interaction = AgentStore(connection).list_interactions(turn.session_id)[0]
    client = TestClient(create_app(store, Registry(), token="synthetic"))
    path = f"/internal/agents/sessions/{turn.session_id}/interactions/{interaction.interaction_id}"
    headers = {"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)}
    body = {"expected_revision": 1, "value": 5}
    first = client.post(path, headers=headers, json=body)
    second = client.post(path, headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    assert store.get(owner, draft.draft_id).fields.stacks == {"python": 5}
    assert store.get(owner, draft.draft_id).revision == 2


def test_old_interaction_cannot_overwrite_new_manual_revision(setup):
    store, owner, draft, turn, _job = setup
    ask_slider(store)
    with connect(store.database_url) as connection:
        interaction = AgentStore(connection).list_interactions(turn.session_id)[0]
    store.patch(owner, draft.draft_id, {"expected_revision": 1, "fields": {"bio": "new manual"}}, Registry().schema())
    client = TestClient(create_app(store, Registry(), token="synthetic"))
    path = f"/internal/agents/sessions/{turn.session_id}/interactions/{interaction.interaction_id}"
    headers = {"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)}
    assert client.post(path, headers=headers, json={"expected_revision": 2, "value": 5}).status_code == 409
    assert store.get(owner, draft.draft_id).fields.stacks == {}


def test_retry_cannot_activate_two_turns_in_one_session(setup):
    """An old failed job must not restart alongside a newer queued request."""
    store, owner, draft, turn, job = setup
    with connect(store.database_url) as connection:
        agent = AgentStore(connection)
        agent.update_turn(turn.turn_id, owner, status="failed", error_code="provider_unavailable")
        agent.append_turn(session_id=turn.session_id, owner_id=owner, draft_id=draft.draft_id,
                          message="new request", expected_revision=1, idempotency_key="second")
        connection.execute("UPDATE agents_service.jobs SET state='failed' WHERE job_id=%s", (job.job_id,))
    client = TestClient(create_app(store, Registry(), token="synthetic"))
    headers = {"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)}
    response = client.post(f"/internal/agents/jobs/{job.job_id}/retry", headers=headers)
    assert response.status_code == 409
    assert store.get_job(owner, job.job_id).state == "failed"


def test_new_chat_turn_after_create_is_rejected_before_provider(setup):
    """Existing conversations cannot reopen a frozen publication snapshot."""
    store, owner, draft, turn, _job = setup
    with connect(store.database_url) as connection:
        AgentStore(connection).update_turn(turn.turn_id, owner, status="succeeded")
    approved = store.approve(owner, draft.draft_id, {"expected_revision": 1}, Registry().schema())
    store.submit(owner, draft.draft_id, {"expected_revision": 1,
        "summary_hash": approved.summary.summary_hash}, Registry().schema())
    client = TestClient(create_app(store, Registry(), token="synthetic"))
    headers = {"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)}
    response = client.post(f"/internal/agents/sessions/{turn.session_id}/turns", headers=headers,
        json={"expected_revision": 1, "idempotency_key": "after-create", "message": "Change salary"})
    assert response.status_code == 409
    assert response.json()["detail"] == "draft_is_frozen"


def test_create_between_runtime_read_and_turn_insert_cannot_queue_work(setup, monkeypatch):
    """The transactional row guard closes the form/Create versus chat TOCTOU gap."""
    store, owner, draft, turn, _job = setup
    with connect(store.database_url) as connection:
        AgentStore(connection).update_turn(turn.turn_id, owner, status="succeeded")
    original = AgentStore.append_turn

    def publish_before_insert(agent, **kwargs):
        approved = store.approve(owner, draft.draft_id, {"expected_revision": 1}, Registry().schema())
        store.submit(owner, draft.draft_id, {"expected_revision": 1,
            "summary_hash": approved.summary.summary_hash}, Registry().schema())
        return original(agent, **kwargs)

    monkeypatch.setattr(AgentStore, "append_turn", publish_before_insert)
    client = TestClient(create_app(store, Registry(), token="synthetic"))
    response = client.post(f"/internal/agents/sessions/{turn.session_id}/turns",
        headers={"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)},
        json={"expected_revision": 1, "idempotency_key": "racing-create", "message": "Change salary"})
    assert response.status_code == 409
    with connect(store.database_url) as connection:
        assert AgentStore(connection).get_turn_by_idempotency(turn.session_id, owner, "racing-create") is None
    assert response.json()["detail"] == "draft_is_frozen"
