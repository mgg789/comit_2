"""Real PostgreSQL regressions for atomic delegated completion and replay fencing."""

import os
from uuid import uuid4

import psycopg
import pytest
from agents_service.database import connect
from agents_service.organization_models import OrganizationTaskRequest
from agents_service.organization_store import OrganizationConflict, OrganizationStore


@pytest.fixture
def completion():
    """Create one isolated owner with a claimed inquiry task and assigned process turn."""
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    store = OrganizationStore(url)
    owner, process = uuid4(), uuid4()
    task = store.create_task(owner, OrganizationTaskRequest(kind="inquiry", instruction="Ask about availability",
                             conversation_refs={"process_ids": [str(process)]}, idempotency_key=str(uuid4())))
    task = store.claim_task(owner_id=owner)
    assert task is not None
    turn = store.create_turn(owner, "Ask candidate", str(uuid4()), task_id=task.task_id,
                             task_lease_token=task.lease_token)
    turn = store.claim_turn_by_id(owner, turn.turn_id)
    store.update_task(owner, task.task_id, status="processing", lease_token=task.lease_token,
                      result={"processes": {str(process): {"turn_id": str(turn.turn_id)}}})
    try:
        yield store, task, turn, process
    finally:
        with connect(url) as db:
            db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner,))


@pytest.mark.integration
@pytest.mark.parametrize("outcome,status,questions", [("completed", "succeeded", 0),
                                                     ("needs_employer", "waiting_employer", 1)])
def test_completion_atomically_reports_and_replays(completion, outcome, status, questions):
    """A terminal retry returns the same durable receipt with no duplicate reports or questions."""
    store, task, turn, process = completion
    values = {"process_id": process, "outcome": outcome, "report": "Candidate answered; employer report", "idempotency_key": "finish"}
    result = store.complete_task(turn, **values)
    assert result["result"]["status"] == status
    assert store.get_task(task.owner_id, task.task_id).status == status
    count = len(store.list_messages(task.owner_id).items)
    assert sum(message.role == "assistant" and message.content == values["report"]
               for message in store.list_messages(task.owner_id).items) == 1
    assert len(store.list_pending_answers(task.owner_id)) == questions
    assert store.complete_task(turn, **values) == result
    assert len(store.list_messages(task.owner_id).items) == count
    assert len(store.list_pending_answers(task.owner_id)) == questions
    with pytest.raises(OrganizationConflict, match="idempotency_key_conflict"):
        store.complete_task(turn, **{**values, "report": "Changed argument"})


@pytest.mark.integration
def test_report_insert_failure_rolls_back_task_completion(completion):
    """A database failure while writing the report leaves the task and receipt untouched."""
    store, task, turn, process = completion
    store.append_message(task.owner_id, role="assistant", content="Existing collision",
                         idempotency_key=f"task:{task.task_id}:report:collision")
    before = store.get_task(task.owner_id, task.task_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.complete_task(turn, process_id=process, outcome="completed", report="Report", idempotency_key="collision")
    after = store.get_task(task.owner_id, task.task_id)
    assert after.status == before.status
    assert after.result == before.result


@pytest.mark.integration
def test_multi_process_completion_preserves_unfinished_processes(completion):
    """One candidate's completion keeps a multi-candidate inquiry processing."""
    store, task, turn, process = completion
    other = uuid4()
    with connect(store.database_url) as db:
        db.execute("""UPDATE agents_service.organization_tasks
            SET conversation_refs=jsonb_set(conversation_refs,'{process_ids}',%s::jsonb)
            WHERE task_id=%s""", (psycopg.types.json.Jsonb([str(process), str(other)]), task.task_id))
    response = store.complete_task(turn, process_id=process, outcome="completed", report="First report", idempotency_key="first")
    assert response["result"]["status"] == "processing"
    current = store.get_task(task.owner_id, task.task_id)
    assert current.result["processes"][str(process)]["completed"] is True
    assert str(other) not in current.result["processes"]


@pytest.mark.integration
@pytest.mark.parametrize("fence", ["task_expired", "turn_expired", "grant_expired", "stopped", "process_mismatch", "turn_mismatch", "owner_mismatch"])
def test_completion_rejects_invalid_authority(completion, fence):
    """Expired leases, cancellation and unassigned processes cannot mutate completion state."""
    store, task, turn, process = completion
    with connect(store.database_url) as db:
        if fence == "task_expired":
            db.execute("UPDATE agents_service.organization_tasks SET lease_until=now()-interval '1 second' WHERE task_id=%s", (task.task_id,))
        elif fence == "turn_expired":
            db.execute("UPDATE agents_service.organization_turns SET lease_until=now()-interval '1 second' WHERE turn_id=%s", (turn.turn_id,))
        elif fence == "grant_expired":
            db.execute("UPDATE agents_service.organization_tasks SET conversation_refs=jsonb_set(conversation_refs,'{expires_at}',to_jsonb((now()-interval '1 second')::text)) WHERE task_id=%s", (task.task_id,))
        elif fence == "turn_mismatch":
            db.execute("UPDATE agents_service.organization_tasks SET result=jsonb_set(result,%s,to_jsonb(%s::text)) WHERE task_id=%s", (["processes", str(process), "turn_id"], str(uuid4()), task.task_id))
    if fence == "stopped":
        store.stop_task(task.owner_id, task.task_id)
    if fence == "process_mismatch":
        process = uuid4()
    if fence == "owner_mismatch":
        turn = turn.model_copy(update={"owner_id": uuid4()})
    before = store.get_task(task.owner_id, task.task_id)
    with pytest.raises(OrganizationConflict):
        store.complete_task(turn, process_id=process, outcome="completed", report="Report", idempotency_key="fenced")
    assert store.get_task(task.owner_id, task.task_id).result == before.result


@pytest.mark.integration
def test_stopped_task_rejects_previously_saved_receipt(completion):
    """Owner cancellation prevents a receipt from authorizing later task replay."""
    store, task, turn, process = completion
    values = {"process_id": process, "outcome": "needs_employer", "report": "Need owner", "idempotency_key": "finish"}
    store.complete_task(turn, **values)
    store.stop_task(task.owner_id, task.task_id)
    with pytest.raises(OrganizationConflict, match="organization_task_lease_lost"):
        store.complete_task(turn, **values)
