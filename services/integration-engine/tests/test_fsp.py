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


def test_demo_authorization_code_is_one_time_and_fixture_bound() -> None:
    client = TestClient(create_app(demo_internal_token="demo-secret"))
    request = {"state": "state-123", "redirect_uri": "http://localhost:8080/candidate/profile/fsp/demo/callback", "fixture_id": "fsp-001"}
    issued = client.post("/fsp/demo/authorize", json=request)
    assert issued.status_code == 200
    code = issued.json()["code"]
    exchanged = client.post("/fsp/demo/token", headers={"X-Internal-Token": "demo-secret"}, json={"code": code, "state": "state-123"})
    assert exchanged.status_code == 200
    assert exchanged.json()["achievements"][0]["claim_id"]
    replay = client.post("/fsp/demo/token", headers={"X-Internal-Token": "demo-secret"}, json={"code": code, "state": "state-123"})
    assert replay.status_code == 400


def test_demo_authorization_rejects_unallowlisted_callback() -> None:
    client = TestClient(create_app())
    response = client.post("/fsp/demo/authorize", json={
        "state": "state-123", "redirect_uri": "https://attacker.invalid/callback", "fixture_id": "fsp-001",
    })
    assert response.status_code == 400


def test_demo_token_exchange_fails_closed_without_internal_token() -> None:
    client = TestClient(create_app())
    response = client.post("/fsp/demo/token", json={"code": "code", "state": "state"})
    assert response.status_code == 503
