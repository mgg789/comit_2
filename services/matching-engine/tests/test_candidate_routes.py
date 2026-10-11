"""Candidate deletion route contract tests without a database."""

from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from matching_engine.candidate_routes import candidate_router


class StubStore:
    """Capture the validated UUID passed to the deletion repository."""

    def __init__(self) -> None:
        self.deleted: list[UUID] = []

    def delete(self, candidate_id: UUID) -> dict[str, object]:
        self.deleted.append(candidate_id)
        return {"candidate_id": str(candidate_id), "deleted": True}


def test_candidate_delete_route_validates_uuid_and_returns_idempotent_receipt() -> None:
    """The internal route forwards only a typed UUID and returns the store receipt."""
    store = StubStore()
    app = FastAPI()

    def authenticated() -> object:
        return object()

    app.include_router(candidate_router(store, authenticated))
    client = TestClient(app)
    candidate_id = "00000000-0000-4000-8000-000000000001"

    response = client.delete(f"/internal/candidates/{candidate_id}")
    assert response.status_code == 200
    assert response.json() == {"candidate_id": candidate_id, "deleted": True}
    assert store.deleted == [UUID(candidate_id)]
    assert client.delete("/internal/candidates/not-a-uuid").status_code == 422
