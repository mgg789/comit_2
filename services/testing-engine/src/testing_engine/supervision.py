"""Supervision aggregates: badge computation and media retention policy.

Panoptikon design (docs/testing-design.md §5): the browser client tracks
window focus and runs YuNet face detection locally - frames never leave the
device by default. Only aggregate counters are uploaded and the employer sees
a five-level presence badge, never the numbers. Raw media capture exists as a
separately enabled contour (env TESTING_RECORDING_MODE=full + explicit
consents) with a 90-day retention bound enforced by cleanup_expired_media().
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

MEDIA_RETENTION_DAYS = 90
CAMERA_GRANTED = "granted"
GOOD_RATIO = 0.95
NORMAL_RATIO = 0.85
MEDIUM_RATIO = 0.70
BADGES = ("хороший", "нормальный", "средний", "низкий", "не определён")


def presence_badge(*, camera_granted: bool, total_seconds: float,
                   in_frame_seconds: float, multi_face_events: int) -> str:
    """Map uploaded aggregates to the five-level employer badge.

    "не определён" is not the candidate's fault (no camera / no data) and is
    visually distinct from "низкий". Any multi-face event caps the badge at
    "средний" - the multi-face signal is heuristic by design.
    """
    if not camera_granted or total_seconds <= 0:
        return "не определён"
    in_frame_seconds = min(in_frame_seconds, total_seconds)
    ratio = in_frame_seconds / total_seconds
    if ratio >= GOOD_RATIO and multi_face_events == 0:
        return "хороший"
    if ratio >= NORMAL_RATIO and multi_face_events == 0:
        return "нормальный"
    if ratio >= MEDIUM_RATIO:
        return "средний"
    return "низкий"


def validate_aggregates(body: dict) -> tuple[dict, list[str]]:
    """Normalize and sanity-check an uploaded aggregate report.

    Returns (clean, problems): clean holds bounded numeric fields for storage;
    problems lists reason codes (empty = accepted). Values are clamped so a
    tampered client cannot store nonsense, never inflated.
    """
    def number(value: object, limit: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return -1.0
        if not math.isfinite(parsed) or not 0 <= parsed <= limit:
            return -1.0
        return parsed

    total = number(body.get("total_seconds"), 24 * 3600)
    in_frame = number(body.get("in_frame_seconds"), total if total >= 0 else -1)
    focus_lost = number(body.get("focus_lost_seconds"), total if total >= 0 else -1)
    try:
        multi_face = int(body.get("multi_face_events", 0))
    except (TypeError, ValueError):
        multi_face = -1
    if multi_face < 0 or multi_face > 10000:
        multi_face = -1
    problems: list[str] = []
    if total < 0 or in_frame < 0 or focus_lost < 0 or multi_face < 0:
        problems.append("invalid_values")
        return {"total_seconds": 0.0, "in_frame_seconds": 0.0,
                "focus_lost_seconds": 0.0, "multi_face_events": 0,
                "camera": str(body.get("camera", "denied"))[:20]}, problems
    return {"total_seconds": total, "in_frame_seconds": in_frame,
            "focus_lost_seconds": focus_lost,
            "multi_face_events": multi_face,
            "camera": str(body.get("camera", "granted"))[:20]}, problems


def cleanup_expired_media(data_dir, *, retention_days: int = MEDIA_RETENTION_DAYS) -> int:
    """Delete stored media older than the retention window; returns the count."""
    import shutil

    media_dir = Path(data_dir) / "media"
    if not media_dir.exists():
        return 0
    deadline = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    for record_path in media_dir.glob("*/manifest.json"):
        try:
            manifest = json.loads(record_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        created = str(manifest.get("created_at", ""))
        try:
            if datetime.fromisoformat(created) >= deadline:
                continue
        except ValueError:
            continue
        shutil.rmtree(record_path.parent, ignore_errors=True)
        removed += 1
    return removed
