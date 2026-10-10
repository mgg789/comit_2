"""Managed vacancy lifecycle and immutable-key paging verified against disposable PostgreSQL."""
from uuid import uuid4

import pytest
import test_store
from agents_service.models import CreateRequest, DraftPatch, RevisionRequest
from agents_service.database import connect
from agents_service.store import ConflictError, LeaseError, NotFoundError, StaleRevisionError
from test_store import complete_fields


@pytest.fixture
def store():
    """Reuse the existing explicitly disposable database fixture."""
    return test_store.store.__wrapped__()


@pytest.fixture
def schema():
    """Reuse the existing small synthetic registry fixture."""
    return test_store.schema.__wrapped__()


def publish(store, owner, draft, schema):
    """Complete the real durable publication lease without substituting lifecycle state."""
    approved = store.approve(owner, draft.draft_id, {"expected_revision": draft.revision}, schema)
    submitted = store.submit(owner, draft.draft_id, CreateRequest(expected_revision=draft.revision,
        summary_hash=approved.summary.summary_hash), schema)
    job = store.claim_job("publication")
    assert job.job_id == submitted.publication.job_id
    store.succeed_job(job.job_id, job.lease_token, {"status": "open"})
    return store.get(owner, draft.draft_id)


def test_published_revision_requires_new_approval_and_close_uses_confirmed_snapshot(store, schema):
    owner = uuid4()
    draft = publish(store, owner, store.create(owner, complete_fields(), schema), schema)
    assert draft.business_status == "open"
    with pytest.raises(NotFoundError):
        store.revise(uuid4(), draft.draft_id, DraftPatch(expected_revision=1, fields={"city": "Тула"}), schema)
    revised = store.revise(owner, draft.draft_id, DraftPatch(expected_revision=1, fields={"city": "Тула"}), schema)
    assert revised.state == "editing" and revised.revision == 2
    with pytest.raises(ConflictError):
        store.submit(owner, draft.draft_id, CreateRequest(expected_revision=2, summary_hash="a" * 64), schema)
    # An in-flight edited publication must lose its lease when closure supersedes it.
    approved = store.approve(owner, draft.draft_id, RevisionRequest(expected_revision=2), schema)
    pending = store.submit(owner, draft.draft_id, CreateRequest(expected_revision=2,
        summary_hash=approved.summary.summary_hash), schema)
    stale_job = store.claim_job("publication")
    assert stale_job.job_id == pending.publication.job_id
    with pytest.raises(NotFoundError):
        store.close(uuid4(), draft.draft_id, RevisionRequest(expected_revision=2))
    closed = store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2))
    assert closed.business_status == "closed" and closed.state == "editing" and closed.revision == 3
    with pytest.raises(LeaseError):
        store.succeed_job(stale_job.job_id, stale_job.lease_token, {"status": "open"})
    job = store.claim_job("publication")
    assert job.job_id == closed.publication.job_id
    assert job.payload["source_revision"] == 3 and job.payload["status"] == "closed"
    assert job.payload["city"] == "Москва"  # unpublished edit is not leaked into a closure
    store.succeed_job(job.job_id, job.lease_token, {"status": "closed"})
    final = store.get(owner, draft.draft_id)
    assert final.business_status == "closed" and final.state == "editing"
    # Exact CAS replay remains the acknowledged closure even after job completion.
    assert store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2)) == closed
    assert store.lifecycle_receipt(owner, draft.draft_id, 2, "close") == closed
    with pytest.raises(NotFoundError):
        store.lifecycle_receipt(uuid4(), draft.draft_id, 2, "close")
    with pytest.raises(StaleRevisionError):
        store.close(owner, draft.draft_id, RevisionRequest(expected_revision=1))
    with pytest.raises(ConflictError):
        store.close(owner, draft.draft_id, RevisionRequest(expected_revision=3))
    with pytest.raises(ConflictError):
        store.revise(owner, draft.draft_id, DraftPatch(expected_revision=3, fields={"city": "Тула"}), schema)
    with pytest.raises(ConflictError):
        store.patch(owner, draft.draft_id, DraftPatch(expected_revision=3, fields={"city": "Тула"}), schema)
    with pytest.raises(ConflictError):
        store.approve(owner, draft.draft_id, RevisionRequest(expected_revision=3), schema)
    with pytest.raises(ConflictError):
        store.submit(owner, draft.draft_id, CreateRequest(expected_revision=3, summary_hash="a" * 64), schema)


def test_republish_same_vacancy_and_delete_leave_only_owned_tombstone(store, schema):
    """DELETE removes the row and jobs; its minimal receipt remains owner-scoped."""
    owner = uuid4()
    draft = publish(store, owner, store.create(owner, complete_fields(), schema), schema)
    changed = store.revise(owner, draft.draft_id, DraftPatch(expected_revision=1, fields={"city": "Тула"}), schema)
    changed = publish(store, owner, changed, schema)
    assert changed.vacancy_id == draft.vacancy_id and changed.revision == 2
    with pytest.raises(ConflictError):
        store.delete(owner, draft.draft_id, RevisionRequest(expected_revision=2))
    closed = store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2))
    with pytest.raises(NotFoundError):
        store.delete(uuid4(), draft.draft_id, RevisionRequest(expected_revision=3))
    with pytest.raises(StaleRevisionError):
        store.delete(owner, draft.draft_id, RevisionRequest(expected_revision=2))
    removed = store.delete(owner, draft.draft_id, RevisionRequest(expected_revision=3))
    assert removed.archived and removed.business_status == "closed" and removed.revision == 4
    assert removed.vacancy_id == closed.vacancy_id
    assert store.list(owner) == [] and store.page(owner).items == []
    with pytest.raises(NotFoundError):
        store.get(owner, draft.draft_id)
    with pytest.raises(NotFoundError):
        store.lifecycle_receipt(owner, draft.draft_id, 2, "close")
    receipt = store.deletion(owner, draft.draft_id)
    assert receipt.vacancy_id == draft.vacancy_id and receipt.source_revision == 4 and receipt.deleted
    assert set(receipt.model_dump()) == {"vacancy_id", "source_revision", "deleted"}
    with pytest.raises(NotFoundError):
        store.deletion(uuid4(), draft.draft_id)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT job_id FROM agents_service.jobs WHERE draft_id=%s",
                                  (draft.draft_id,)).fetchone() is None
        tombstone = connection.execute("SELECT * FROM agents_service.vacancy_tombstones WHERE vacancy_id=%s",
                                       (draft.draft_id,)).fetchone()
        assert tombstone["owner_id"] == owner and tombstone["source_revision"] == 4
        assert tombstone["deleted_at"] is not None and "fields" not in tombstone


def test_keyset_is_owner_scoped_and_unchanged_by_edits_or_newer_insertions(store, schema):
    owner = uuid4()
    originals = [store.create(owner, {"title": str(index)}, schema) for index in range(4)]
    first = store.page(owner, limit=2)
    assert first.has_more and first.next_cursor
    store.revise(owner, originals[0].draft_id, DraftPatch(expected_revision=1, fields={"title": "changed"}), schema)
    inserted = store.create(owner, {}, schema)
    second = store.page(owner, limit=2, cursor=first.next_cursor)
    combined = first.items + second.items
    assert len({item.draft_id for item in combined}) == 4
    assert inserted.draft_id not in {item.draft_id for item in combined}
    with pytest.raises(ConflictError):
        store.page(uuid4(), cursor=first.next_cursor)
    with pytest.raises(ConflictError):
        store.page(owner, cursor=first.next_cursor, offset=1)
    with pytest.raises(ConflictError):
        store.page(owner, cursor="malformed")
    assert len(store.page(owner, limit=2, offset=2).items) == 2
