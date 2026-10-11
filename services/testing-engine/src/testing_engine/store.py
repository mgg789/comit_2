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


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _days_between(from_date: str, to_date: str) -> int:
    from datetime import date

    try:
        start = date.fromisoformat(from_date[:10])
        end = date.fromisoformat(to_date[:10])
    except ValueError:
        return 0
    return max(0, (end - start).days)


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
        """Insert bank items; existing item_ids are never overwritten.

        Seed items carry origin="seed": they are immune to the age-based
        moratorium (they are the permanent baseline bank), while generated
        items rotate out per the instance moratorium policy.
        """
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8")) if self.items_path.exists() else []
            known = {row["item_id"] for row in bank}
            inserted = 0
            for index, item in enumerate(items, 1):
                item_id = f'{item["family"]}-L{item["level"]}-{index}'
                if item_id in known:
                    # Repair the historical flatten fixture's positional-args bug
                    # only for the exact immutable seed, never generated content.
                    for row in bank:
                        if (row["item_id"] == item_id and row.get("origin") == "seed"
                                and row["payload"].get("function_name") == "flatten"
                                and row["payload"].get("tests", [{}])[0].get("args") == [[1, 2], [3]]):
                            row["payload"]["tests"] = item["tests"]
                    continue
                bank.append({"item_id": item_id, "family": item["family"], "level": item["level"],
                             "type": item["type"], "payload": item, "exposure_count": 0, "retired": False,
                             "origin": "seed", "created_date": _today(), "last_served_date": None})
                inserted += 1
            _atomic_write(self.items_path, bank)
        return inserted

    def add_generated_item(self, item: dict) -> str | None:
        """Append one generator-produced item; duplicate prompts are rejected."""
        prompt_key = " ".join(str(item.get("prompt", "")).lower().split())
        item_id = f'gen-{item["family"]}-L{item["level"]}-{_now().replace("-", "").replace(":", "").replace("+00:00", "Z")}'
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8")) if self.items_path.exists() else []
            for row in bank:
                if row["family"] != item["family"] or row["level"] != item["level"]:
                    continue
                existing_key = " ".join(str(row["payload"].get("prompt", "")).lower().split())
                if existing_key == prompt_key:
                    return None
            record = {"item_id": item_id, "family": item["family"], "level": item["level"],
                      "type": item["type"], "payload": item, "exposure_count": 0, "retired": False,
                      "origin": "generated", "created_date": _today(), "last_served_date": None}
            bank.append(record)
            _atomic_write(self.items_path, bank)
        return item_id

    def mark_served(self, item_id: str) -> None:
        """Record that the item was presented today (drives the moratorium)."""
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
            for row in bank:
                if row["item_id"] == item_id:
                    row["last_served_date"] = _today()
            _atomic_write(self.items_path, bank)

    def retire_expired(self, *, max_exposure: int = 3, max_age_days: int = 1) -> int:
        """Retire generated items past the exposure or age moratorium; seeds stay."""
        today = _today()
        retired = 0
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
            for row in bank:
                if row["retired"] or row.get("origin") != "generated":
                    continue
                age = _days_between(row.get("created_date", today), today)
                if row["exposure_count"] >= max_exposure or age >= max_age_days:
                    row["retired"] = True
                    retired += 1
            if retired:
                _atomic_write(self.items_path, bank)
        return retired

    def count_active(self, family: str, level: int, *, today: str | None = None) -> int:
        """Active, not-served-today items available for new sessions."""
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
        day = today or _today()
        return sum(1 for row in bank
                   if row["family"] == family and row["level"] == level
                   and not row["retired"] and row.get("last_served_date") != day)

    def list_items(self, family: str, level: int, limit: int = 5, *,
                   task_types: set[str] | None = None,
                   exclude_ids: set[str] | None = None) -> list[dict]:
        """Usable items for a new presentation; items served today rotate out.

        When everything valid was served today, the strict filter is relaxed
        (better a repeated variant than no session) - repeats are still ordered
        by lowest exposure first. Type and session exclusions apply BEFORE that
        rotation, so unused MCQs cannot hide a required previously served code
        item. Retired items and session repeats are never fallback candidates.
        """
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
        usable = [row for row in bank
                  if row["family"] == family and row["level"] == level
                  and not row["retired"]
                  and (task_types is None or row["type"] in task_types)
                  and (exclude_ids is None or row["item_id"] not in exclude_ids)]
        strict = [row for row in usable if row.get("last_served_date") != _today()]
        chosen = strict or usable
        chosen.sort(key=lambda row: (row["exposure_count"], row["item_id"]))
        return [{**row["payload"], "item_id": row["item_id"], "exposure_count": row["exposure_count"]}
                for row in chosen[:limit]]


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
