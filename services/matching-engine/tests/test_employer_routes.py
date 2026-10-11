"""Employer deletion HTTP contract and authentication regression tests."""

from uuid import UUID

from fastapi.testclient import TestClient
from matching_engine.main import create_app
from matching_engine.store import Store
from matching_engine.vacancy_store import VacancyStore


class CapturingVacancyStore(VacancyStore):
    """Capture lifecycle calls without connecting to a database."""

    def __init__(self) -> None:
        super().__init__("unused-test-dsn")
        self.deleted: list[UUID] = []

    def delete_employer(self, owner_id: UUID) -> dict[str, object]:
        """Return the production receipt shape for a typed owner UUID."""
        self.deleted.append(owner_id)
        return {"owner_id": str(owner_id), "deleted": True}


def test_employer_delete_authentication_uuid_and_idempotent_receipt() -> None:
    """Missing/wrong token and malformed IDs never reach the deletion store."""
    store = CapturingVacancyStore()
    client = TestClient(create_app(store=Store("unused-test-dsn"), vacancy_store=store,
                                   internal_token="synthetic-test-token"))
    owner_id = UUID(int=100)
    path = f"/internal/employers/{owner_id}"
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers={"X-Internal-Token": "wrong"}).status_code == 401
    headers = {"X-Internal-Token": "synthetic-test-token"}
    assert client.delete("/internal/employers/invalid", headers=headers).status_code == 422
    assert store.deleted == []
    for _ in range(2):
        response = client.delete(path, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"owner_id": str(owner_id), "deleted": True}
    assert store.deleted == [owner_id, owner_id]


def test_employer_delete_without_database_is_unavailable(monkeypatch) -> None:
    """Configured authentication cannot substitute for missing durable storage."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(internal_token="synthetic-test-token"))
    assert client.delete(f"/internal/employers/{UUID(int=100)}",
                         headers={"X-Internal-Token": "synthetic-test-token"}).status_code == 503
