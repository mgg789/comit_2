"""Exercise mandatory ownership at the real HTTP dependency boundary without a database."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from matching_engine.combine_routes import combine_router

OWNER = UUID("00000000-0000-4000-8000-000000000003")
VACANCY = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("method,suffix,payload", [
    ("POST", "combine", {}), ("GET", "combine", None), ("POST", "combine/retry", None),
    ("GET", "selections", None),
    ("PUT", "selection-state/00000000-0000-4000-8000-000000000002", {"state": "visited"}),
])
def test_owner_is_required_and_checked_before_any_store_access(monkeypatch, method, suffix, payload):
    """Missing/malformed/foreign owners never reach storage; correct owners do reach it."""
    ownership_calls = []
    store_calls = []

    def assert_owner(database_url, vacancy_id, owner_id):
        ownership_calls.append((database_url, vacancy_id, owner_id))
        if owner_id != OWNER:
            raise HTTPException(status_code=404, detail="owner_record_not_found")

    def store_action(*args):
        store_calls.append(args)
        raise HTTPException(status_code=409, detail="synthetic_not_ready")

    monkeypatch.setattr("matching_engine.combine_routes.assert_vacancy_owner", assert_owner)
    repository = SimpleNamespace(database_url="synthetic-database", request=store_action, status=store_action,
                                 retry=store_action, selections=store_action, set_selection_state=store_action)
    app = FastAPI()
    app.include_router(combine_router(repository, lambda: None))
    client = TestClient(app)
    url = f"/internal/vacancies/{VACANCY}/{suffix}"
    for invalid in [None, "invalid", "", f"{OWNER},{OWNER}"]:
        headers = {} if invalid is None else {"X-Owner-ID": invalid}
        assert client.request(method, url, json=payload, headers=headers).status_code == 422
    assert ownership_calls == [] and store_calls == []
    assert client.request(method, url, json=payload,
                          headers={"X-Owner-ID": "00000000-0000-4000-8000-000000000004"}).status_code == 404
    assert store_calls == []
    assert client.request(method, url, json=payload, headers={"X-Owner-ID": str(OWNER)}).status_code == 409
    assert ownership_calls[-1] == ("synthetic-database", UUID(VACANCY), OWNER)
    assert len(store_calls) == 1
