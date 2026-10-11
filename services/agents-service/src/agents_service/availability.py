"""Cached optional-provider capabilities; manual editing never requires an LLM."""
import os
import threading
import time

import httpx

from agents_service.providers import ProviderError, llm_config, mirror_config

_lock = threading.Lock()
_cache: dict[str, tuple[float, bool, bool]] = {}


def _reachable(feature: str) -> bool:
    """Check only configured provider endpoints with a bounded read-only probe."""
    if feature == "agent":
        _provider, base, _model, token, _effort = llm_config()
        if not token:
            return False
        url = base + "/models"
        headers = {"Authorization": "Bearer " + token}
    elif os.getenv("JEV_PROVIDER") == "openrouter-mirror":
        _url, _model, token = mirror_config()
        if not token:
            return False
        url = "https://openrouter.droidje.com/api/v1/models"
        headers = {"X-Proxy-Token": token}
    else:
        url = os.getenv("JEV_LOCAL_URL", "http://127.0.0.1:12444").rstrip("/") + "/health"
        headers = {"Host": "localhost:12444"}
    with _lock:
        cached = _cache.get(url)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
    rejected = False
    try:
        response = httpx.get(url, headers=headers, timeout=httpx.Timeout(5, connect=2), follow_redirects=False)
        available = response.is_success
        rejected = response.status_code in (401, 403)
    except httpx.HTTPError:
        available = False
    with _lock:
        # A brief network failure must not disable a configured provider for 30 seconds.
        _cache[url] = (time.monotonic() + (30 if available or rejected else 3), available, rejected)
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
    if os.getenv("AGENTS_MODE", "auto") == "manual" or (feature == "agent" and not llm_config()[3]):
        raise ProviderError("agent_disabled_manual_available", False)
    if not _reachable(feature):
        with _lock:
            url = llm_config()[1] + "/models" if feature == "agent" else os.getenv("JEV_LOCAL_URL", "http://127.0.0.1:12444").rstrip("/") + "/health"
            if feature == "enrichment" and os.getenv("JEV_PROVIDER") == "openrouter-mirror":
                url = "https://openrouter.droidje.com/api/v1/models"
            cached = _cache.get(url)
            rejected = bool(cached and cached[2])
        if rejected:
            raise ProviderError(f"{feature}_provider_rejected", False)
        raise ProviderError(f"{feature}_temporarily_unavailable", True)
