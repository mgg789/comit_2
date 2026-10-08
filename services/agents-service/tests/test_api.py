"""Real PostgreSQL + ASGI contract tests; models/providers are separate acceptance contours."""

import os
from uuid import uuid4

import pytest
from agents_service.database import connect, migrate
from agents_service.main import create_app
from agents_service.models import SchemaSnapshot
from agents_service.store import Store
from fastapi.testclient import TestClient


class TestRegistry:
    """Unit transport replacement with an explicit stable schema, not inference."""

    def schema(self):
        return SchemaSnapshot(version=1, schema_hash="a" * 64,
            coordinates=[{"index": 0, "group": "stack", "key": "python"}])


@pytest.fixture
def client():
    url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTS_TEST_DATABASE_URL required")
    migrate(url)
    with connect(url) as connection:
        connection.execute("TRUNCATE agents_service.drafts CASCADE")
    return TestClient(create_app(Store(url), TestRegistry(), token="synthetic", capability_check=lambda _: None))


def headers(owner=None):
    return {"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner or uuid4())}


def test_api_owner_cas_and_shared_summary(client):
    auth = headers()
    path = "/internal/vacancy-drafts"
    assert client.post(path, json={"fields": {}}).status_code == 401
    created = client.post(path, headers=auth, json={"fields": {}})
    assert created.status_code == 200
    value = created.json()
    path += "/" + value["draft_id"]
    assert client.get(path, headers=headers()).status_code == 404
    assert client.post(path + "/approve", headers=auth, json={"expected_revision": 1}).status_code == 409
    fields = {"title": "Python developer", "zp_min": 100, "zp_max": 200, "daily": "5/2", "city": "Москва",
              "type": "remote", "stacks": {"python": 0}}
    assert client.patch(path, headers=auth, json={"expected_revision": 1, "fields": fields}).status_code == 200
    assert client.patch(path, headers=auth, json={"expected_revision": 1, "fields": {"bio": "late"}}).status_code == 409
    summary = client.post(path + "/approve", headers=auth, json={"expected_revision": 2}).json()
    assert summary["state"] == "summary"
    assert summary["summary"]["fields"]["stacks"] == {"python": 0}
    request = {"expected_revision": 2, "summary_hash": summary["summary"]["summary_hash"]}
    first = client.post(path + "/create", headers=auth, json=request)
    second = client.post(path + "/create", headers=auth, json=request)
    assert first.status_code == second.status_code == 200
    assert first.json()["publication"]["job_id"] == second.json()["publication"]["job_id"]
    assert client.patch(path, headers=auth, json={"expected_revision": 2, "fields": {"bio": "frozen"}}).status_code == 409


def test_api_chat_owner_and_atomic_turn_enqueue(client):
    auth = headers()
    value = client.post("/internal/vacancy-drafts", headers=auth, json={"fields": {}}).json()
    session = client.post("/internal/agents/sessions", headers=auth, json={"draft_id": value["draft_id"]})
    assert session.status_code == 200
    path = "/internal/agents/sessions/" + session.json()["session_id"]
    assert client.get(path, headers=headers()).status_code == 404
    request = {"expected_revision": 1, "idempotency_key": "first", "message": "Нужен Python"}
    first = client.post(path + "/turns", headers=auth, json=request)
    duplicate = client.post(path + "/turns", headers=auth, json=request)
    assert first.status_code == duplicate.status_code == 200
    assert first.json()["job_id"] == duplicate.json()["job_id"]
    assert first.json()["kind"] == "agent_turn"
    assert client.get(path, headers=auth).json()["turn"]["status"] == "queued"
