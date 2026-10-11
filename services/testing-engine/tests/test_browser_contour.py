"""Authenticated browser attempts, privacy, finalization and media retention."""
import base64
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from testing_engine.main import create_testing_app, maintenance


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "isolated-test-token")
    monkeypatch.setenv("TESTING_RECORDING_MODE", "full")
    client = TestClient(create_testing_app(data_dir=tmp_path))
    client.headers.update({"x-internal-token": "isolated-test-token", "x-candidate-id": "candidate-one"})
    return client


def start(client):
    return client.post("/testing/sessions", json={"specialization": "react", "consents": {
        "version": "testing-media-v1", "processing": True, "screen_recording": True, "video_analysis": False}})


def test_attempt_auth_owner_consent_and_resume(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    assert client.get("/testing/preflight?specialization=react").json()["eligible"]
    assert client.post("/testing/sessions", json={"specialization": "react"}).status_code == 422
    sid = start(client).json()["session_id"]
    assert client.get("/testing/preflight?specialization=react").json()["active_session_id"] == sid
    assert start(client).status_code == 409
    first = client.get(f"/testing/sessions/{sid}/next").json()
    second = client.get(f"/testing/sessions/{sid}/next").json()
    assert first["task"] == second["task"]
    assert not set(first["task"]) & {"tests", "answer_index", "rubric"}
    client.headers["x-candidate-id"] = "candidate-two"
    assert client.get(f"/testing/sessions/{sid}/result").status_code == 404
    client.headers["x-internal-token"] = "wrong"
    assert client.get(f"/testing/sessions/{sid}/result").status_code == 401


def test_media_idempotence_consent_manifest_and_retention(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    sid = start(client).json()["session_id"]
    path = f"/testing/sessions/{sid}/media"
    body = {"kind": "screen", "data_base64": base64.b64encode(b"recording").decode(), "sequence": 0}
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json={**body, "data_base64": "bmV3"}).status_code == 409
    assert client.post(path, json={**body, "kind": "webcam"}).status_code == 403
    manifest_path = tmp_path / "media" / sid / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["retention_days"] == 90
    assert manifest["consents"]["accepted_at"]
    manifest["created_at"] = (datetime.now(UTC) - timedelta(days=91)).isoformat()
    manifest_path.write_text(json.dumps(manifest))
    assert maintenance(tmp_path)["removed"] == 1
    assert not manifest_path.exists()


def test_abandon_records_cooldown_without_publishing(tmp_path, monkeypatch):
    from testing_engine.publisher import TrustedGradePublisher
    def unexpected_publish(self, session):
        raise AssertionError("Interrupted attempt must never publish a grade")
    monkeypatch.setattr(TrustedGradePublisher, "publish", unexpected_publish)
    client = client_for(tmp_path, monkeypatch)
    sid = start(client).json()["session_id"]
    task = client.get(f"/testing/sessions/{sid}/next").json()["task"]
    stored = next(row["payload"] for row in json.loads((tmp_path / "items.json").read_text()) if row["item_id"] == task["item_id"])
    answer = task["options"].index(stored["options"][stored["answer_index"]]) if task["type"] == "mcq" else stored["expected_answers"][0]
    first = client.post(f"/testing/sessions/{sid}/answer", json={"item_id": task["item_id"], "answer": answer}).json()
    assert first["correct"] is True
    assert first["answered"] == 1
    assert first["decided_grade"] is None
    assert client.post(f"/testing/sessions/{sid}/abandon").json()["status"] == "abandoned"
    result = client.get(f"/testing/sessions/{sid}/result").json()
    assert result["decided_grade"] is None
    assert result["grade_publication"] is None
    assert result["answered"] == 1
    assert result["correct"] == 1
    assert client.post(f"/testing/sessions/{sid}/finish").status_code == 409
    assert start(client).status_code == 409


def test_result_publication_waits_for_final_evidence_and_is_retryable(tmp_path, monkeypatch):
    from testing_engine.publisher import TrustedGradePublisher
    published = []
    def publish(self, session):
        published.append(session.copy())
        return True, "published"
    monkeypatch.setattr(TrustedGradePublisher, "publish", publish)
    client = client_for(tmp_path, monkeypatch)
    sid = start(client).json()["session_id"]
    last_item = None
    for _ in range(30):
        next_response = client.get(f"/testing/sessions/{sid}/next")
        if next_response.status_code == 409:
            break
        task = next_response.json()["task"]
        stored = next(row["payload"] for row in json.loads((tmp_path / "items.json").read_text()) if row["item_id"] == task["item_id"])
        answer = task["options"].index(stored["options"][stored["answer_index"]]) if task["type"] == "mcq" else stored["expected_answers"][0] if task["type"] == "short_answer" else "typed response"
        response = client.post(f"/testing/sessions/{sid}/answer", json={"item_id": task["item_id"], "answer": answer})
        assert response.status_code == 200
        retry = client.post(f"/testing/sessions/{sid}/answer", json={"item_id": task["item_id"], "answer": answer})
        assert retry.json() == response.json()
        last_item = task
        if response.json()["status"] == "finished":
            break
    result = client.get(f"/testing/sessions/{sid}/result").json()
    assert result["status"] == "finished"
    assert published == []
    assert client.post(f"/testing/sessions/{sid}/finish").status_code == 409
    report = {"total_seconds": 2, "in_frame_seconds": 0, "focus_lost_seconds": 0,
              "multi_face_events": 0, "camera": "denied"}
    assert client.post(f"/testing/sessions/{sid}/supervision", json=report).status_code == 200
    finished = client.post(f"/testing/sessions/{sid}/finish").json()
    assert finished["grade_publication"]["published"] is True
    assert len(published) == 1
    assert client.post(f"/testing/sessions/{sid}/finish").json()["capture_finalized"] is True
    assert len(published) == 1
    assert published[0]["supervision"]["badge"] == "не определён"
    assert client.get(f"/testing/sessions/{sid}/result").json()["grade_publication"]["published"] is True
    assert len(published) == 1
    assert client.post(f"/testing/sessions/{sid}/supervision", json=report).status_code == 409
    assert last_item


def test_start_fails_closed_without_token_even_with_explicit_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("TESTING_ENGINE_TOKEN", raising=False)
    client = TestClient(create_testing_app(data_dir=tmp_path))
    assert client.get("/testing/preflight?specialization=react").status_code == 503


def test_short_answers_are_actually_checked_without_executing_code():
    from testing_engine.judge import judge_answer
    task = {"type": "short_answer", "expected_answers": ["usestate"]}
    assert judge_answer(task, " useState ") == (True, "auto")
    assert judge_answer(task, "useEffect") == (False, "auto")


def test_resume_preserves_recording_sequence_and_declined_camera(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    sid = start(client).json()["session_id"]
    assert client.post(f"/testing/sessions/{sid}/media", json={"kind": "screen", "sequence": 7, "data_base64": "aGVsbG8="}).status_code == 200
    report = {"total_seconds": 2, "in_frame_seconds": 0, "focus_lost_seconds": 1,
              "multi_face_events": 0, "camera": "denied"}
    assert client.post(f"/testing/sessions/{sid}/supervision", json=report).status_code == 200
    flight = client.get("/testing/preflight?specialization=react").json()
    assert flight["next_sequence"] == 8
    assert flight["active_supervision"]["total_seconds"] == 2
    assert flight["active_session_consents"]["video_analysis"] is False
    assert client.post(f"/testing/sessions/{sid}/supervision", json={**report, "total_seconds": 1}).status_code == 409
    assert client.post(f"/testing/sessions/{sid}/supervision", json={**report, "total_seconds": 4, "camera": "granted"}).json()["badge"] == "не определён"


def test_off_mode_processing_consent_and_frozen_recording(tmp_path, monkeypatch):
    """Processing is explicit, and later enablement cannot enable old capture."""
    client = client_for(tmp_path, monkeypatch)
    monkeypatch.setenv("TESTING_RECORDING_MODE", "off")
    body = {"specialization": "react", "consents": {"version": "testing-media-v1",
        "processing": True, "screen_recording": False, "video_analysis": False}}
    missing = {**body, "consents": {key: value for key, value in body["consents"].items() if key != "processing"}}
    assert client.post("/testing/sessions", json=missing).status_code == 422
    assert client.post("/testing/sessions", json={**body, "consents": {**body["consents"], "screen_recording": True}}).status_code == 422
    sid = client.post("/testing/sessions", json=body).json()["session_id"]
    flight = client.get("/testing/preflight?specialization=react").json()
    assert flight["active_recording_enabled"] is False
    assert flight["active_session_consents"]["processing"] is True
    monkeypatch.setenv("TESTING_RECORDING_MODE", "full")
    flight = client.get("/testing/preflight?specialization=react").json()
    assert flight["recording_enabled"] is True
    assert flight["active_recording_enabled"] is False
    assert client.post(f"/testing/sessions/{sid}/media", json={"kind": "screen", "sequence": 0, "data_base64": "aGVsbG8="}).status_code == 409


def test_telemetry_cannot_override_consent_or_server_elapsed(tmp_path, monkeypatch):
    """A browser claim cannot grant declined video consent or invent test time."""
    client = client_for(tmp_path, monkeypatch)
    sid = start(client).json()["session_id"]
    report = {"total_seconds": 2, "in_frame_seconds": 2, "focus_lost_seconds": 0,
              "multi_face_events": 0, "camera": "granted"}
    assert client.post(f"/testing/sessions/{sid}/supervision", json=report).json()["badge"] == "не определён"
    flight = client.get("/testing/preflight?specialization=react").json()
    assert flight["active_supervision"]["camera"] == "denied"
    rejected = client.post(f"/testing/sessions/{sid}/supervision", json={**report, "total_seconds": 100})
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "supervision_exceeds_elapsed"
    assert client.get("/testing/preflight?specialization=react").json()["active_supervision"]["total_seconds"] == 2
    path = tmp_path / "sessions" / f"{sid}.json"
    session = json.loads(path.read_text())
    del session["recording_mode"]
    session["consents"].pop("processing")
    path.write_text(json.dumps(session))
    assert client.get("/testing/preflight?specialization=react").json()["active_recording_enabled"] is False
    assert client.post(f"/testing/sessions/{sid}/media", json={"kind": "screen", "sequence": 0, "data_base64": "aGVsbG8="}).status_code == 409
