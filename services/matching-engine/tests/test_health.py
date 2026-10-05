from fastapi.testclient import TestClient

from matching_engine.main import create_app


def test_healthz_reports_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "matching-engine"}
