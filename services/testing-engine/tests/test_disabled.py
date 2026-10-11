"""Explicit local disabled mode cannot judge, consume or publish attempts."""

from fastapi.testclient import TestClient
from testing_engine.judge import CodeJudgeClient
from testing_engine.main import create_testing_app


def test_disabled_blocks_all_operations_without_judge_or_attempt(tmp_path, monkeypatch):
    """All subjects and existing-session URLs stay closed, including simple MCQ."""
    monkeypatch.setenv("TESTING_ENABLED", "false")
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "synthetic-test-token")
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled testing must never contact the judge")
    monkeypatch.setattr(CodeJudgeClient, "ready", forbidden)
    app = create_testing_app(data_dir=tmp_path)
    headers = {"x-internal-token": "synthetic-test-token", "x-candidate-id": "owner"}
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        for subject in ("algorithms", "python", "react", "sql", "teamwork", "docker", "rest_api"):
            response = client.get("/testing/preflight", params={"specialization": subject}, headers=headers)
            assert response.status_code == 200
            assert response.json()["eligible"] is False
            assert response.json()["unavailable_reason"] == "testing_disabled"
            start = client.post("/testing/sessions", json={"specialization": subject}, headers=headers)
            assert start.status_code == 503
            assert start.json() == {"detail": "testing_disabled"}
        for method, suffix in (("get", "next"), ("get", "result"), ("post", "answer"),
                               ("post", "finish"), ("post", "abandon"), ("post", "media"), ("post", "supervision")):
            response = getattr(client, method)("/testing/sessions/s-existing/" + suffix, headers=headers)
            assert response.status_code == 503
        assert not list((tmp_path / "sessions").glob("*.json"))


def test_disabled_still_requires_owner_authentication(tmp_path, monkeypatch):
    """Local disable never makes private preflight or sessions public."""
    monkeypatch.setenv("TESTING_ENABLED", "false")
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "synthetic-test-token")
    with TestClient(create_testing_app(data_dir=tmp_path)) as client:
        assert client.get("/testing/preflight?specialization=react").status_code == 401
