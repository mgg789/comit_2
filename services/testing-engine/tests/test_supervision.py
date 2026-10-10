"""Tests of the panoptikon: badge math, supervision endpoint, media gating."""

from pathlib import Path

from fastapi.testclient import TestClient

from testing_engine.main import create_testing_app, maintenance
from testing_engine.supervision import presence_badge


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_testing_app(data_dir=tmp_path / "testing-data"))


def _session(client: TestClient, *, screen: bool = True) -> str:
    created = client.post("/testing/sessions", json={
        "candidate_key": "candidate-1", "specialization": "algorithms",
        "consents": {"video_analysis": True, "screen_recording": screen}}).json()
    return created["session_id"]


def test_badge_boundaries_match_the_design_table() -> None:
    """Ratios map to the five badges; multi-face caps at средний."""
    assert presence_badge(camera_granted=True, total_seconds=100,
                          in_frame_seconds=96, multi_face_events=0) == "хороший"
    assert presence_badge(camera_granted=True, total_seconds=100,
                          in_frame_seconds=90, multi_face_events=0) == "нормальный"
    assert presence_badge(camera_granted=True, total_seconds=100,
                          in_frame_seconds=80, multi_face_events=3) == "средний"
    assert presence_badge(camera_granted=True, total_seconds=100,
                          in_frame_seconds=50, multi_face_events=0) == "низкий"
    assert presence_badge(camera_granted=False, total_seconds=100,
                          in_frame_seconds=100, multi_face_events=0) == "не определён"
    assert presence_badge(camera_granted=True, total_seconds=100,
                          in_frame_seconds=99, multi_face_events=1) == "средний"


def test_supervision_upload_stores_badge(tmp_path: Path) -> None:
    """Aggregates are accepted, bounded and turned into a stored badge."""
    client = _client(tmp_path)
    session_id = _session(client)
    uploaded = client.post(f"/testing/sessions/{session_id}/supervision", json={
        "total_seconds": 600, "in_frame_seconds": 580,
        "focus_lost_seconds": 20, "multi_face_events": 0, "camera": "granted"})
    assert uploaded.json()["badge"] == "хороший"
    result = client.get(f"/testing/sessions/{session_id}/result").json()
    assert result["supervision"] == "хороший"
    assert result["consents"]["video_analysis"] is True


def test_supervision_rejects_inflated_counters(tmp_path: Path) -> None:
    """A tampered client cannot upload negative or inflated counters."""
    client = _client(tmp_path)
    session_id = _session(client)
    bad = client.post(f"/testing/sessions/{session_id}/supervision", json={
        "total_seconds": 600, "in_frame_seconds": 99999,
        "multi_face_events": 0, "camera": "granted"})
    assert bad.status_code == 400
    result = client.get(f"/testing/sessions/{session_id}/result").json()
    assert result["supervision"] is None


def test_media_endpoint_is_gated_by_env_and_consent(tmp_path: Path, monkeypatch) -> None:
    """Disabled by default (409); with env on it still requires screen consent."""
    client = _client(tmp_path)
    session_id = _session(client)
    monkeypatch.delenv("TESTING_RECORDING_MODE", raising=False)
    chunk = {"kind": "screen", "data_base64": "aGVsbG8="}
    assert client.post(f"/testing/sessions/{session_id}/media", json=chunk).status_code == 409
    monkeypatch.setenv("TESTING_RECORDING_MODE", "full")
    without_consent = _session(client, screen=False)
    assert client.post(f"/testing/sessions/{without_consent}/media", json=chunk).status_code == 403
    stored = client.post(f"/testing/sessions/{session_id}/media", json=chunk)
    assert stored.status_code == 200 and stored.json()["retention_days"] == 90
    assert client.post(f"/testing/sessions/{session_id}/media",
                       json={"kind": "screen", "data_base64": "!!!"}).status_code == 400


def test_maintenance_cleanup_reports_removed_media(tmp_path: Path) -> None:
    """The retention pass runs on a data dir without media and reports zero."""
    report = maintenance(tmp_path / "data")
    assert report == {"removed": 0, "retention_days": 90}
