"""Unjudgeable answers and deterministic publication rejections cannot pin a candidate forever."""
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from testing_engine.judge import CodeJudgeClient
from testing_engine.main import create_testing_app
from testing_engine.publisher import TrustedGradePublisher
from testing_engine.rubric import RubricJudge
from testing_engine.store import TestingStore


def test_well_formed_uncertain_rubric_is_distinct_and_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "synthetic")
    judge = RubricJudge(tmp_path)
    calls = []
    def respond(*args):
        calls.append(True)
        return {"answers": {"0": {"classprobs": {"0": .45, "1": .55}}}}
    monkeypatch.setattr(judge, "_request", respond)
    task = {"prompt": "question", "rubric": ["criterion"]}
    assert judge.judge(task, "partially correct", owner="candidate") == (None, "rubric:uncertain")
    assert judge.judge(task, "partially correct", owner="candidate") == (None, "rubric:uncertain")
    assert len(calls) == 1
    monkeypatch.setattr(judge, "_request", lambda *args: {"answers": {"0": {"classprobs": {"0": .2, "1": .2}}}})
    assert judge.judge(task, "different answer", owner="candidate") == (None, "text_judge_unavailable")


@pytest.mark.parametrize("reason", ["code_judge_unavailable", "rubric:uncertain"])
def test_three_unresolved_attempts_end_unjudged_without_grade_or_cooldown(tmp_path, monkeypatch, reason):
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "synthetic")
    monkeypatch.setenv("TESTING_RECORDING_MODE", "off")
    monkeypatch.setattr(CodeJudgeClient, "ready", lambda self: True)
    monkeypatch.setattr("testing_engine.main.judge_answer", lambda *args, **kwargs: (None, reason))
    headers = {"x-internal-token": "synthetic", "x-candidate-id": "candidate"}
    client = TestClient(create_testing_app(tmp_path), headers=headers)
    request = {"specialization": "python", "consents": {"processing": True, "version": "testing-media-v1", "screen_recording": False}}
    sid = client.post("/testing/sessions", json=request).json()["session_id"]
    first = client.get(f"/testing/sessions/{sid}/next").json()["task"]
    client.post(f"/testing/sessions/{sid}/answer", json={"item_id": first["item_id"], "answer": 0})
    task = client.get(f"/testing/sessions/{sid}/next").json()["task"]
    store = TestingStore(tmp_path)
    before = store.read_session(sid)["state"]
    body = {"item_id": task["item_id"], "answer": "unresolved synthetic code"}
    assert client.post(f"/testing/sessions/{sid}/answer", json=body).status_code == 503
    # Counter and original answer survive process recreation.
    client = TestClient(create_testing_app(tmp_path), headers=headers)
    assert client.post(f"/testing/sessions/{sid}/answer", json=body).status_code == 503
    ended = client.post(f"/testing/sessions/{sid}/answer", json=body)
    assert ended.status_code == 200
    assert ended.json()["status"] == "abandoned" and ended.json()["correct"] is None
    assert client.post(f"/testing/sessions/{sid}/answer", json=body).json() == ended.json()
    saved = store.read_session(sid)
    assert saved["state"] == before and saved["decided_grade"] is None
    assert saved["items"][-1]["judge_attempts"] == 3
    assert saved["items"][-1]["judge_status"] == "unjudged:" + reason
    result = client.get(f"/testing/sessions/{sid}/result").json()
    assert result["next_attempt_at"] is None and result["failure_reason"] == "judge_unavailable"
    assert result["grade_publication"] is None
    assert client.get("/testing/preflight?specialization=python").json()["eligible"]
    assert client.post("/testing/sessions", json=request).status_code == 200
    # An older completed attempt still imposes its legitimate cooldown.
    for path in store.sessions_dir.glob("*.json"):
        row = json.loads(path.read_text())
        if row["status"] == "active":
            row.update(status="abandoned", failure_reason="judge_unavailable")
            store.write_session(row)
    completed = store.create_session("candidate", "python", None)
    row = store.read_session(completed)
    row.update(status="finished", capture_finalized=True, created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat())
    store.write_session(row)
    preflight = client.get("/testing/preflight?specialization=python").json()
    assert not preflight["eligible"] and preflight["next_attempt_at"]
    assert client.post("/testing/sessions", json=request).status_code == 409


def test_http_error_preserves_status_detail_and_upgrade_date(monkeypatch):
    date = "2030-02-01T12:00:00Z"
    def reject(*args, **kwargs):
        raise HTTPError("https://api.example.test", 409, "Conflict", {}, BytesIO(json.dumps({"detail": "grade_upgrade_cooldown", "upgrade_at": date}).encode()))
    monkeypatch.setattr("testing_engine.publisher.urllib.request.urlopen", reject)
    publisher = TrustedGradePublisher("https://api.example.test", "synthetic")
    assert publisher.publish({"candidate_key": "12345678-1234-4234-8234-123456789abc"}) == (False, "api_status:409")
    assert publisher.failure_detail == "grade_upgrade_cooldown" and publisher.retry_at == date


@pytest.mark.parametrize("status,terminal_calls", [("api_status:409", 1), ("api_unreachable", 3)])
def test_result_publication_is_terminal_or_bounded(tmp_path, monkeypatch, status, terminal_calls):
    app = create_testing_app(tmp_path, allow_unauthenticated=True)
    store = TestingStore(tmp_path)
    sid = store.create_session("candidate", "react", None)
    row = store.read_session(sid)
    row.update(status="finished", decided_grade="middle", capture_finalized=True)
    store.write_session(row)
    calls = []
    def publish(self, session):
        calls.append(session["session_id"])
        self.failure_detail = "grade_upgrade_cooldown" if status.endswith("409") else None
        return False, status
    monkeypatch.setattr(TrustedGradePublisher, "publish", publish)
    client = TestClient(app)
    for _ in range(6):
        result = client.get(f"/testing/sessions/{sid}/result").json()
        row = store.read_session(sid)
        if row.get("grade_publication"):
            row["grade_publication"]["next_retry_at"] = None
            store.write_session(row)
    assert len(calls) == terminal_calls
    assert result["grade_publication"]["retryable"] is False


def test_abandon_while_judge_is_unavailable_does_not_charge_a_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING_ENGINE_TOKEN", "synthetic")
    monkeypatch.setenv("TESTING_RECORDING_MODE", "off")
    monkeypatch.setattr(CodeJudgeClient, "ready", lambda self: True)
    client = TestClient(create_testing_app(tmp_path), headers={"x-internal-token": "synthetic", "x-candidate-id": "candidate"})
    store = TestingStore(tmp_path)
    sid = store.create_session("candidate", "python", None)
    row = store.read_session(sid)
    row["items"] = [{"item_id": "failed-item", "level": 2, "answer": None, "correct": None,
                     "judge_status": "presented", "judge_attempts": 1, "pending_answer": "unjudged"}]
    store.write_session(row)
    assert client.post(f"/testing/sessions/{sid}/abandon").status_code == 200
    assert client.get(f"/testing/sessions/{sid}/result").json()["failure_reason"] == "judge_unavailable"
    assert client.get("/testing/preflight?specialization=python").json()["eligible"]
