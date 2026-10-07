"""Agent draft operations share the receipt transaction, including publication outbox."""

import os
from uuid import uuid4

import pytest
from agents_service.database import connect, migrate
from agents_service.models import DraftPatch, SchemaSnapshot
from agents_service.store import NotFoundError, Store


@pytest.mark.integration
def test_draft_and_publication_rollback_with_receipt_transaction():
    """A failed tool receipt cannot leave a draft or frozen publication job behind."""
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(url)
    owner = uuid4()
    store = Store(url)
    schema = SchemaSnapshot(version=1, schema_hash="test", coordinates=[{"index": 0, "group": "stack", "key": "python"}])
    fields = {"title": "Test engineer", "zp_min": 100, "zp_max": 200, "daily": "5/2",
          "city": "Москва", "type": "remote", "class": "backend"}
    with pytest.raises(RuntimeError, match="receipt_failed"), connect(url) as connection:
        created = store.create_in_connection(connection, owner, fields, schema)
        raise RuntimeError("receipt_failed")
    with pytest.raises(NotFoundError):
        store.get(owner, created.draft_id)
    draft = store.create(owner, fields, schema)
    with pytest.raises(RuntimeError, match="receipt_failed"), connect(url) as connection:
        revised = store.revise_in_connection(connection, owner, draft.draft_id,
                                             DraftPatch(expected_revision=1, fields={"city": "Казань"}), schema)
        approved = store.approve_in_connection(connection, owner, draft.draft_id, revised.revision, schema)
        store.submit_in_connection(connection, owner, draft.draft_id,
                                   {"expected_revision": approved.revision, "summary_hash": approved.summary.summary_hash}, schema)
        raise RuntimeError("receipt_failed")
    unchanged = store.get(owner, draft.draft_id)
    assert unchanged.revision == 1 and unchanged.fields.city == "Москва" and unchanged.publication is None
    with connect(url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM agents_service.jobs WHERE owner_id=%s", (owner,)).fetchone()["n"] == 0
