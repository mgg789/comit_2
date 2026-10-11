"""Skeleton storage for the testing contour: atomic JSON files, no SQL.

The store keeps the same API the PostgreSQL migration (integration pass) will
implement, so callers never change. Layout under the data dir:
- items.json — the bank: [{item_id, family, level, type, payload, exposure_count, retired}]
- sessions/<session_id>.json — one file per session (state, items, answers, result)
Writes are atomic (tmp + rename); a process-global lock serializes writers.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
        self.deleted_dir = self.data_dir / "deleted-candidates"
        self.items_path = self.data_dir / "items.json"
        self._lock = threading.Lock()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.deleted_dir.mkdir(parents=True, exist_ok=True)
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

    def claim_item(self, item_id: str) -> bool:
        """Atomically reserve a never-served instance across threads."""
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
            row = next((row for row in bank if row["item_id"] == item_id), None)
            if row is None or row["retired"] or row.get("last_served_date") is not None:
                return False
            row["last_served_date"] = _today()
            _atomic_write(self.items_path, bank)
            return True

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
        return sum(1 for row in bank
                   if row["family"] == family and row["level"] == level
                   and not row["retired"] and row.get("last_served_date") is None)

    def list_items(self, family: str, level: int, limit: int = 5, *,
                   task_types: set[str] | None = None,
                   exclude_ids: set[str] | None = None) -> list[dict]:
        """Usable items for a new presentation; shown instances never return.

        Previously served instances are never recycled. Offline parameterized tasks
        replenish auto-scored slots; unavailable code/text slots stay empty.
        """
        with self._lock:
            bank = json.loads(self.items_path.read_text(encoding="utf-8"))
        usable = [row for row in bank
                  if row["family"] == family and row["level"] == level
                  and not row["retired"]
                  and (task_types is None or row["type"] in task_types)
                  and (exclude_ids is None or row["item_id"] not in exclude_ids)]
        strict = [row for row in usable if row.get("last_served_date") is None]
        if not strict:
            from testing_engine.variants import (
                FAMILIES,
                instantiate,
                instantiate_checked,
            )
            if family in FAMILIES:
                for _ in range(5):
                    if task_types is None or "short_answer" in task_types:
                        self.add_generated_item(instantiate(family, level))
                    else:
                        for task_type in sorted(task_types):
                            generated = instantiate_checked(family, level, task_type)
                            if generated:
                                self.add_generated_item(generated)
                with self._lock:
                    bank = json.loads(self.items_path.read_text(encoding="utf-8"))
                strict = [row for row in bank if row["family"] == family and row["level"] == level
                          and not row["retired"] and row.get("last_served_date") is None
                          and (task_types is None or row["type"] in task_types)
                          and (exclude_ids is None or row["item_id"] not in exclude_ids)]
        chosen = strict
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

    def _deleted_marker(self, candidate_key: str) -> Path:
        """Return a non-PII tombstone path for one candidate owner."""
        digest = hashlib.sha256(candidate_key.encode("utf-8")).hexdigest()
        return self.deleted_dir / f"{digest}.json"

    def is_deleted(self, candidate_key: str) -> bool:
        """Check whether testing data for this owner has been permanently revoked."""
        return self._deleted_marker(candidate_key).exists()

    def delete_candidate(self, candidate_key: str) -> int:
        """Tombstone an owner and remove all sessions plus captured media.

        The marker is created while the store lock is held so a request that
        starts after deletion cannot create a new session for the deleted
        owner. Retries are idempotent and do not recreate personal data.
        """
        if not candidate_key:
            raise ValueError("candidate_key_required")
        removed = 0

        def remove_media(path: Path) -> None:
            """Remove a capture directory and fail if the filesystem kept it."""
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                return
            if path.exists():
                raise OSError(f"candidate_media_delete_incomplete: {path.name}")

        with self._lock:
            marker = self._deleted_marker(candidate_key)
            if not marker.exists():
                _atomic_write(marker, {"deleted_at": _now()})
            for session_path in self.sessions_dir.glob("*.json"):
                try:
                    record = json.loads(session_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if record.get("candidate_key") != candidate_key:
                    continue
                session_id = str(record.get("session_id", session_path.stem))
                session_path.unlink(missing_ok=True)
                if session_path.exists():
                    raise OSError(f"candidate_session_delete_incomplete: {session_id}")
                remove_media(self.data_dir / "media" / session_id)
                removed += 1
            # Remove capture directories whose session file is malformed or
            # was already rotated, when their manifest still names the owner.
            media_dir = self.data_dir / "media"
            if media_dir.exists():
                for session_dir in media_dir.iterdir():
                    manifest = session_dir / "manifest.json"
                    try:
                        payload = json.loads(manifest.read_text(encoding="utf-8"))
                    except (OSError, ValueError, NotADirectoryError):
                        continue
                    if payload.get("candidate_key") == candidate_key:
                        remove_media(session_dir)
        return removed

    def read_session(self, session_id: str) -> dict:
        return json.loads(self.session_path(session_id).read_text(encoding="utf-8"))

    def write_session(self, record: dict) -> None:
        with self._lock:
            _atomic_write(self.session_path(record["session_id"]), record)

    def audit_code_similarity(self, owner: str, item_id: str, code: str, correct: bool, *, function_name: str | None = None) -> dict:
        """Record normalized token similarity; independent judge verdict keeps credit.

        Short/common implementations can collide. This is review evidence only,
        never a cheating verdict. Raw code/owner IDs are not added to the log.
        """
        import io
        import tokenize
        from difflib import SequenceMatcher

        def tokens(source: str) -> list[str]:
            try:
                return [token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)
                        if token.type not in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                                              tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}]
            except (tokenize.TokenError, IndentationError):
                return source.split()

        current = tokens(code)
        maximum = 0.0
        comparisons = 0
        with self._lock:
            for path in sorted(self.sessions_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:1000]:
                prior = json.loads(path.read_text(encoding="utf-8"))
                if prior.get("candidate_key") == owner:
                    continue
                for row in prior.get("items", []):
                    same_problem = row.get("item_id") == item_id or (
                        function_name is not None and row.get("payload", {}).get("type") == "code"
                        and row.get("payload", {}).get("function_name") == function_name)
                    if not same_problem or not isinstance(row.get("answer"), str):
                        continue
                    comparisons += 1
                    maximum = max(maximum, SequenceMatcher(None, current, tokens(row["answer"]), autojunk=False).ratio())
            evidence = {"method": "python-token-sequence-v1", "comparisons": comparisons,
                        "maximum_similarity": round(maximum, 4), "review_flag": comparisons > 0 and maximum >= .9,
                        "credit_policy": "independent_judge", "credited": correct,
                        "code_hash": hashlib.sha256("\n".join(current).encode()).hexdigest()}
            audit = self.data_dir / "code-similarity.jsonl"
            with audit.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": _now(), "item_id": item_id, **evidence}) + "\n")
        return evidence
