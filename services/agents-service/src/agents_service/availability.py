"""Cached optional-provider capabilities; manual editing never requires an LLM."""
import os
import threading
import time

import httpx

from agents_service.providers import ProviderError

_lock = threading.Lock()
_cache: dict[str, tuple[float, bool]] = {}


def _reachable(feature: str) -> bool:
    """Check only configured provider endpoints with a bounded read-only probe."""
    if feature == "agent":
        token = os.getenv("DEEPSEEK_API_KEY", "")
        if not token:
            return False
        url = "https://api.deepseek.com/models"
        headers = {"Authorization": "Bearer " + token}
    else:
        url = os.getenv("JEV_LOCAL_URL", "http://127.0.0.1:12444").rstrip("/") + "/health"
        headers = {"Host": "localhost:12444"}
    with _lock:
        cached = _cache.get(feature)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
    try:
        response = httpx.get(url, headers=headers, timeout=2, follow_redirects=False)
        available = response.is_success
    except httpx.HTTPError:
        available = False
    with _lock:
        _cache[feature] = (time.monotonic() + 30, available)
    return available


def capability_view() -> dict:
    """Describe optional functionality and its explicit manual replacement."""
    manual = os.getenv("AGENTS_MODE", "auto") == "manual"
    return {"manual_editing": True, "agent": not manual and _reachable("agent"),
            "enrichment": not manual and _reachable("enrichment"),
            "fallback": "manual_editing", "disabled_code": "agent_disabled_manual_available"}


def require_capability(feature: str) -> None:
    """Reject disabled work before enqueue; callers keep the draft editable."""
    if feature not in ("agent", "enrichment"):
        raise ValueError("unknown capability")
    if not capability_view()[feature]:
        raise ProviderError("agent_disabled_manual_available", False)
