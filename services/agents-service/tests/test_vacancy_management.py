"""Managed vacancy lifecycle and immutable-key paging verified against disposable PostgreSQL."""
from uuid import uuid4

import pytest
import test_store
from agents_service.models import CreateRequest, DraftPatch, RevisionRequest
from agents_service.store import ConflictError, NotFoundError, StaleRevisionError
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
    closed = store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2))
    assert closed.business_status == "closed" and closed.state == "creating"
    job = store.claim_job("publication")
    assert job.payload["source_revision"] == 3 and job.payload["status"] == "closed"
    assert job.payload["city"] == "Москва"  # unpublished edit is not leaked into a closure
    store.succeed_job(job.job_id, job.lease_token, {"status": "closed"})
    assert store.get(owner, draft.draft_id).business_status == "closed"
    with pytest.raises(StaleRevisionError):
        store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2))


def test_republish_same_vacancy_and_archive_preserve_source_and_audit(store, schema):
    owner = uuid4()
    draft = publish(store, owner, store.create(owner, complete_fields(), schema), schema)
    changed = store.revise(owner, draft.draft_id, DraftPatch(expected_revision=1, fields={"city": "Тула"}), schema)
    changed = publish(store, owner, changed, schema)
    assert changed.vacancy_id == draft.vacancy_id and changed.revision == 2
    archived = store.close(owner, draft.draft_id, RevisionRequest(expected_revision=2), archive=True)
    assert archived.archived and archived.business_status == "closed"
    assert store.list(owner) == [] and store.page(owner).items == []
    assert store.get(owner, draft.draft_id).archived
    with pytest.raises(ConflictError):
        store.revise(owner, draft.draft_id, DraftPatch(expected_revision=3, fields={}), schema)


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
