"""Candidate-owned testing data deletion and tombstone tests."""

import json

from testing_engine.store import TestingStore as SessionStore
from testing_engine.store import _atomic_write


def test_delete_candidate_removes_sessions_and_media_idempotently(tmp_path):
    """Deletion fences new sessions and removes both JSON state and captures."""
    store = SessionStore(tmp_path)
    session_id = store.create_session("candidate-1", "python", None)
    session = store.read_session(session_id)
    store.write_session(session)
    media_dir = tmp_path / "media" / session_id
    media_dir.mkdir(parents=True)
    _atomic_write(media_dir / "manifest.json", {
        "session_id": session_id,
        "candidate_key": "candidate-1",
        "created_at": session["created_at"],
    })
    (media_dir / "chunk-0000-webcam.bin").write_bytes(b"private")

    assert store.delete_candidate("candidate-1") == 1
    assert not store.session_path(session_id).exists()
    assert not media_dir.exists()
    assert store.is_deleted("candidate-1")
    assert store.delete_candidate("candidate-1") == 0

    # A different owner remains isolated from the tombstone.
    other = store.create_session("candidate-2", "python", None)
    assert json.loads(store.session_path(other).read_text())["candidate_key"] == "candidate-2"


def test_delete_candidate_retries_cleanup_after_tombstone_was_written(tmp_path):
    """A crash after fencing must not make a later retry skip remaining data."""
    store = SessionStore(tmp_path)
    _atomic_write(store._deleted_marker("candidate-1"), {"deleted_at": "2026-01-01T00:00:00+00:00"})
    session_id = store.create_session("candidate-1", "python", None)

    assert store.delete_candidate("candidate-1") == 1
    assert not store.session_path(session_id).exists()
