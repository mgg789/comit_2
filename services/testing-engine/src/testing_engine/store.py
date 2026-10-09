"""Skeleton storage for the testing contour: atomic JSON files, no SQL.

The store keeps the same API the PostgreSQL migration (integration pass) will
implement, so callers never change. Layout under the data dir:
- items.json — the bank: [{item_id, family, level, type, payload, exposure_count, retired}]
- sessions/<session_id>.json — one file per session (state, items, answers, result)
Writes are atomic (tmp + rename); a process-global lock serializes writers.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class TestingStore:
    """File-backed bank + sessions; thread-safe via a process-global lock."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.items_path = self.data_dir / "items.json"
        self._lock = threading.Lock()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        if not self.items_path.exists():
            _atomic_write(self.items_path, [])

    def seed_items(self, items: list[dict]) -> int:
        """Insert bank items; existing item_ids are never overwritten."""
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8")) if self.items_path.exists() else []
            known = {row["item_id"] for row in bank}
            inserted = 0
            for index, item in enumerate(items, 1):
                item_id = f'{item["family"]}-L{item["level"]}-{index}'
                if item_id in known:
                    continue
                bank.append({"item_id": item_id, "family": item["family"], "level": item["level"],
                             "type": item["type"], "payload": item, "exposure_count": 0, "retired": False})
                inserted += 1
            _atomic_write(self.items_path, bank)
        return inserted

    def list_items(self, family: str, level: int, limit: int = 5) -> list[dict]:
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
        usable = [row for row in bank
                  if row["family"] == family and row["level"] == level
                  and not row["retired"]]
        usable.sort(key=lambda row: (row["exposure_count"], row["item_id"]))
        return [{**row["payload"], "item_id": row["item_id"], "exposure_count": row["exposure_count"]}
                for row in usable[:limit]]

    def bump_exposure(self, item_id: str) -> None:
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
            for row in bank:
                if row["item_id"] == item_id:
                    row["exposure_count"] += 1
            _atomic_write(self.items_path, bank)

    def create_session(self, candidate_key: str, specialization: str, declared_grade: str | None) -> str:
        session_id = "s-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        record = {"session_id": session_id, "candidate_key": candidate_key,
                  "specialization": specialization, "declared_grade": declared_grade,
                  "status": "active", "state": {}, "tasks_given": 0,
                  "decided_grade": None, "items": [], "created_at": _now()}
        with self._lock:
            _atomic_write(self.session_path(session_id), record)
        return session_id

    def session_path(self, session_id: str) -> Path:
        if "/" in session_id or "\\" in session_id or ".." in session_id or not session_id:
            raise ValueError("invalid session id")
        return self.sessions_dir / (session_id + ".json")

    def read_session(self, session_id: str) -> dict:
        return json.loads(self.session_path(session_id).read_text(encoding="utf-8"))

    def write_session(self, record: dict) -> None:
        with self._lock:
            _atomic_write(self.session_path(record["session_id"]), record)
