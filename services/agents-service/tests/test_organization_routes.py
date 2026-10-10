"""ASGI contract checks for owner-scoped organization agent routes."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from agents_service.database import connect
from agents_service.organization_routes import register_organization_routes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class DatabaseDependency:
    """Minimal existing API database dependency consumed by organization routes."""

    def __init__(self, database_url: str):
        self.database_url = database_url


def _cleanup_owner(database_url: str, owner_id: UUID) -> None:
    """Remove only this test owner's graph from the shared isolated database."""
    with connect(database_url) as connection:
        connection.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner_id,))


def _provider_unavailable(_capability: str) -> None:
    """Represent an unavailable configured provider capability at the API boundary."""
    raise HTTPException(503, "agent_provider_unavailable")


def _client(database_url: str, owner_ref: dict, *, capability_check=None) -> TestClient:
    """Build the production route registration with explicit test dependencies."""
    app = FastAPI()

    def owner_dependency() -> UUID:
        value = owner_ref["owner"]
        if isinstance(value, BaseException):
            raise value
        return value

    register_organization_routes(
        app,
        owner_dependency,
        lambda: DatabaseDependency(database_url),
        capability_check=capability_check,
    )
    return TestClient(app)


@pytest.mark.integration
def test_organization_routes_auth_scope_capability_and_task_validation():
    """Routes enforce authentication, owner isolation, provider availability and grant scope."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    owner_id = uuid4()
    other_owner = uuid4()
    owner_ref = {"owner": owner_id}
    allowed = _client(database_url, owner_ref, capability_check=lambda _capability: None)
    try:
        unauthenticated = _client(database_url, {"owner": HTTPException(401, "unauthenticated")})
        assert unauthenticated.get("/internal/organization-agent/chat").status_code == 401

        created = allowed.post("/internal/organization-agent/tasks", json={
            "kind": "inquiry",
            "instruction": "Review candidate context",
            "conversation_refs": {"process_ids": [str(uuid4())]},
            "idempotency_key": "route-scope-task",
        })
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        owner_ref["owner"] = other_owner
        assert allowed.get("/internal/organization-agent/tasks").status_code == 200
        assert allowed.get("/internal/organization-agent/tasks").json() == []
        assert allowed.delete(f"/internal/organization-agent/tasks/{task_id}").status_code == 404

        owner_ref["owner"] = owner_id
        offline = _client(database_url, owner_ref, capability_check=_provider_unavailable)
        message = offline.post("/internal/organization-agent/messages", json={
            "message": "Please investigate this candidate",
            "idempotency_key": "offline-message",
        })
        assert message.status_code == 503

        assert allowed.post("/internal/organization-agent/presence", json={"present": True}).status_code == 200
        standin = allowed.post("/internal/organization-agent/tasks", json={
            "kind": "stand_in",
            "instruction": "Reply while I am unavailable",
            "conversation_refs": {"process_ids": [str(uuid4())]},
            "idempotency_key": "online-stand-in",
        })
        assert standin.status_code == 202
        assert standin.json()["status"] == "queued"
        assert standin.json()["conversation_refs"]["process_ids"]

        invalid_process = str(uuid4())
        invalid = allowed.post("/internal/organization-agent/tasks", json={
            "kind": "inquiry",
            "instruction": "Reject duplicate references",
            "conversation_refs": {"process_ids": [invalid_process, invalid_process]},
            "idempotency_key": "invalid-duplicate-processes",
        })
        assert invalid.status_code == 422
    finally:
        _cleanup_owner(database_url, owner_id)
        _cleanup_owner(database_url, other_owner)
