"""Wire regression for publishing the actual persisted adaptive answer count."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request
from uuid import uuid4

import pytest

from testing_engine.publisher import PUBLISH_TIMEOUT_SECONDS, TrustedGradePublisher
from testing_engine.store import TestingStore as SessionStore


@pytest.mark.parametrize("answered", [1, 6])
def test_publisher_sends_persisted_answer_count(tmp_path: Path, monkeypatch, answered: int) -> None:
    """The API wire count comes from tasks_given, which submit_answer persists."""
    session = {
        "candidate_key": str(uuid4()), "session_id": str(uuid4()),
        "specialization": "backend", "status": "finished", "decided_grade": "middle",
        "state": {"answered": answered}, "tasks_given": answered,
        "supervision": {"badge": "нормальный"},
    }
    store = SessionStore(tmp_path)
    store.write_session(session)
    requests: list[Request] = []

    @contextmanager
    def capture(request: Request, *, timeout: int):
        requests.append(request)
        assert timeout == PUBLISH_TIMEOUT_SECONDS
        yield SimpleNamespace(status=200)

    monkeypatch.setattr("testing_engine.publisher.urllib.request.urlopen", capture)
    result = TrustedGradePublisher("https://api.example.test/", "synthetic-token").publish(
        store.read_session(session["session_id"]))
    assert result == (True, "api_status:200")
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://api.example.test/internal/testing/session-result"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-internal-token") == "synthetic-token"
    assert json.loads(request.data) == {
        "candidate_id": session["candidate_key"], "session_id": session["session_id"],
        "specialization": "backend", "decided_grade": "middle",
        "supervision_badge": "нормальный", "tasks_answered": answered,
    }
