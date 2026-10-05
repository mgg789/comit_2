"""Disposable PostgreSQL acceptance tests for owner, CAS, snapshots and leases."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from agents_service.database import connect, migrate
from agents_service.models import SchemaSnapshot
from agents_service.store import (
    ConflictError,
    LeaseError,
    NotFoundError,
    StaleRevisionError,
    Store,
)

DATABASE_URL = os.environ.get("AGENTS_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.fixture()
def store() -> Store:
    """Use only the explicitly supplied disposable agents database."""
    if DATABASE_URL is None:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(DATABASE_URL)
    with connect(DATABASE_URL) as connection:
        connection.execute("TRUNCATE agents_service.jobs, agents_service.drafts CASCADE")
    return Store(DATABASE_URL)


@pytest.fixture()
def schema() -> SchemaSnapshot:
    """Small registry sufficient for the synthetic draft cases."""
    return SchemaSnapshot(
        version=37,
        schema_hash="a" * 64,
        coordinates=[
            {"index": 0, "group": "domain", "key": "sql"},
            {"index": 1, "group": "stack", "key": "python"},
            {"index": 2, "group": "soft_skill", "key": "teamwork"},
        ],
    )


def complete_fields() -> dict[str, object]:
    """Return a complete vacancy input without an owner supplied by the client."""
    return {
        "title": "Backend engineer",
        "zp_min": 100,
        "zp_max": 200,
        "daily": "5/2",
        "city": "Москва",
        "type": "remote",
        "class": "backend",
        "stacks": {"python": 4},
        "soft_skills": {"teamwork": 2},
    }


def test_owner_scope_and_stale_cas(store: Store, schema: SchemaSnapshot) -> None:
    """A foreign owner cannot read a draft and stale revisions cannot overwrite it."""
    owner, other = uuid4(), uuid4()
    draft = store.create(owner, complete_fields(), schema)
    with pytest.raises(NotFoundError):
        store.get(other, draft.draft_id)
    changed = store.patch(owner, draft.draft_id, {"expected_revision": 1, "fields": {"city": "Казань"}}, schema)
    assert changed.revision == 2 and changed.fields.city == "Казань"
    with pytest.raises(StaleRevisionError):
        store.patch(owner, draft.draft_id, {"expected_revision": 1, "fields": {"city": "Тула"}}, schema)


def test_approval_snapshot_submit_is_idempotent_and_freezes_fields(store: Store, schema: SchemaSnapshot) -> None:
    """Approval hashes the exact fields and duplicate human confirmation returns one job."""
    owner = uuid4()
    draft = store.create(owner, complete_fields(), schema)
    approved = store.approve(owner, draft.draft_id, {"expected_revision": 1}, schema)
    assert approved.summary is not None
    submitted = store.submit(owner, draft.draft_id, {"expected_revision": 1, "summary_hash": approved.summary.summary_hash}, schema)
    again = store.submit(owner, draft.draft_id, {"expected_revision": 1, "summary_hash": approved.summary.summary_hash}, schema)
    assert submitted.state == again.state == "creating"
    assert submitted.publication and submitted.publication.job_id == again.publication.job_id
    with pytest.raises(ConflictError):
        store.patch(owner, draft.draft_id, {"expected_revision": 1, "fields": {"bio": "changed"}}, schema)


def test_laya_does_not_overwrite_manual_group(store: Store, schema: SchemaSnapshot) -> None:
    """Laya suggestions retain manually sourced fields while recording their own provenance."""
    owner = uuid4()
    draft = store.create(owner, complete_fields(), schema)
    changed = store.apply_enrichment(
        owner, draft.draft_id, 1, {"stacks": {"python": 1}, "bio": "generated"}, schema,
        input_hash="input", model_version="laya-v1", schema_version=37, schema_hash="a" * 64,
    )
    assert changed.fields.stacks == {"python": 4}
    assert changed.fields.bio == "generated"
    assert changed.field_sources["bio"].source == "laya"


def test_job_lease_guard_retry_and_publication_transition(store: Store, schema: SchemaSnapshot) -> None:
    """Wrong lease tokens are rejected and a successful publication marks matching intake accepted."""
    owner = uuid4()
    draft = store.create(owner, complete_fields(), schema)
    approved = store.approve(owner, draft.draft_id, {"expected_revision": 1}, schema)
    submitted = store.submit(owner, draft.draft_id, {"expected_revision": 1, "summary_hash": approved.summary.summary_hash}, schema)
    assert submitted.publication is not None
    claimed = store.claim_job("publication", lease_seconds=30)
    assert claimed is not None
    with pytest.raises(LeaseError):
        store.heartbeat_job(claimed.job_id, uuid4())
    done = store.succeed_job(claimed.job_id, claimed.lease_token, {"status": "accepted"})
    assert done.state == "succeeded"
    assert store.get(owner, draft.draft_id).state == "created"
