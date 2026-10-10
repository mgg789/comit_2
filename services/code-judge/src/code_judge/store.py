"""Submission storage for the code-judge service: atomic JSON files.

One file per submission under <data_dir>/submissions/. Lifecycle:
stored -> judged (when a runner is configured) ; the runner module is
delivered separately (sandboxed execution is the service's core and is
reviewed as such). Same API the PostgreSQL migration will implement.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class SubmissionStore:
    """File-backed submissions; thread-safe via a process-global lock."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.submissions_dir = self.data_dir / "submissions"
        self._lock = threading.Lock()
        self.submissions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, submission_id: str) -> Path:
        if not submission_id.replace("-", "").isalnum() or len(submission_id) > 64:
            raise ValueError("invalid submission id")
        return self.submissions_dir / (submission_id + ".json")

    def create(self, item_id: str, language: str, code: str) -> dict:
        submission_id = str(uuid.uuid4())
        record = {"submission_id": submission_id, "item_id": item_id,
                  "language": language, "code": code, "status": "stored",
                  "verdict_kind": "deferred", "correct": None,
                  "created_at": _now(), "judged_at": None}
        with self._lock:
            _atomic_write(self._path(submission_id), record)
        return record

    def read(self, submission_id: str) -> dict:
        return json.loads(self._path(submission_id).read_text(encoding="utf-8"))

    def save(self, record: dict) -> None:
        with self._lock:
            _atomic_write(self._path(record["submission_id"]), record)

    def mark_judged(self, submission_id: str, correct: bool | None, verdict_kind: str) -> dict:
        record = self.read(submission_id)
        record.update(status="judged", correct=correct, verdict_kind=verdict_kind,
                      judged_at=_now())
        self.save(record)
        return record
