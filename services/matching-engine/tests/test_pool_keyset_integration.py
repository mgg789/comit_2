"""Keyset continuations preserve ranking and refuse publication drift."""
from uuid import UUID

import pytest
from fastapi import HTTPException
from matching_engine.database import connect
from test_contracts import eligible_profile
from test_pipeline_integration import worker

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


def test_pool_keyset_has_no_duplicates_and_refuses_source_change(store):
    for index in range(1, 4):
        store.intake(UUID(int=index), eligible_profile())
        assert worker(store).process_once()
    first = store.pool(1, 0)
    second = store.pool(1, 0, first.next_cursor)
    third = store.pool(1, 0, second.next_cursor)
    assert first.has_more and second.has_more and not third.has_more
    assert len({page.items[0].candidate_id for page in (first, second, third)}) == 3
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET state='draft' WHERE candidate_id=%s", (UUID(int=3),))
    with pytest.raises(HTTPException) as error:
        store.pool(1, 0, first.next_cursor)
    assert error.value.status_code == 409
