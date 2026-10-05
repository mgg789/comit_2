from fastapi.testclient import TestClient

from integration_engine.main import create_app


def test_healthz_reports_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "integration-engine"}


def test_known_participant_is_served() -> None:
    client = TestClient(create_app())
    response = client.get("/fsp/participants/fsp-001")

    assert response.status_code == 200
    assert response.json()["rank"] == "I"


def test_missing_fsp_history_is_404_not_500() -> None:
    client = TestClient(create_app())
    response = client.get("/fsp/participants/fsp-unknown")

    assert response.status_code == 404


def test_verify_accepts_only_matching_rating() -> None:
    client = TestClient(create_app())

    ok = client.post("/fsp/verify", json={"fsp_id": "fsp-002", "claimed_rating": 2043})
    bad = client.post("/fsp/verify", json={"fsp_id": "fsp-002", "claimed_rating": 1000})

    assert ok.json()["verified"] is True
    assert bad.json()["verified"] is False
