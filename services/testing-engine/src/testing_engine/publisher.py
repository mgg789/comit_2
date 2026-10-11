"""Trusted-grade publication: testing engine -> api internal contour.

When TESTING_API_URL and TESTING_ENGINE_TOKEN are configured, a finished
session publishes {candidate_id, session_id, specialization, decided_grade,
supervision_badge, tasks_answered} to POST /internal/testing/session-result.
Any failure degrades silently - the session result remains stored in the
testing engine and can be republished. Cooldowns (retake 7 days, upgrade
30 days) are enforced by the api, which is the source of truth.
"""

from __future__ import annotations

import json
import os
import urllib.request

PUBLISH_TIMEOUT_SECONDS = 15


class TrustedGradePublisher:
    """HTTP publisher for finished adaptive sessions."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("TESTING_API_URL", "")).rstrip("/")
        self.token = token or os.environ.get("TESTING_ENGINE_TOKEN", "")

    def configured(self) -> bool:
        return bool(self.base_url) and bool(self.token)

    def publish(self, session: dict) -> tuple[bool, str]:
        """POST the session result; (True, 'published') or (False, reason)."""
        if not self.configured():
            return False, "not_configured"
        candidate_id = str(session.get("candidate_key", ""))
        try:
            import uuid as uuid_module

            uuid_module.UUID(candidate_id)
        except (ValueError, AttributeError):
            return False, "candidate_key_is_not_uuid"
        body = {
            "candidate_id": candidate_id,
            "session_id": session.get("session_id", ""),
            "specialization": session.get("specialization", "backend"),
            "decided_grade": session.get("decided_grade"),
            "supervision_badge": (session.get("supervision") or {}).get("badge"),
            "camera_status": "denied" if not session.get("consents", {}).get("video_analysis", False)
                else session.get("supervision", {}).get("camera", "unavailable"),
            # submit_answer persists the adaptive scored answer count as tasks_given.
            "tasks_answered": session.get("tasks_given", 0),
        }
        if session.get("consents", {}).get("version") != "testing-media-v1":
            body.pop("camera_status", None)
        request = urllib.request.Request(
            self.base_url + "/internal/testing/session-result",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"content-type": "application/json",
                     "x-internal-token": self.token}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=PUBLISH_TIMEOUT_SECONDS) as response:
                return response.status == 200, f"api_status:{response.status}"
        except OSError:
            return False, "api_unreachable"
