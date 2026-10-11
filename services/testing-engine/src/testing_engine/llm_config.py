"""Validated configuration for the optional OpenAI-compatible testing provider."""

import os
from urllib.parse import urlparse


def completion_config() -> tuple[str, str, str, str, dict[str, str]]:
    """Return provider, URL, model, token and provider-specific request options."""
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    if provider not in {"deepseek", "timeweb"}:
        raise ValueError("LLM_PROVIDER must be deepseek or timeweb")
    prefix = provider.upper()
    base = os.getenv(prefix + "_BASE_URL", "https://api.timeweb.ai/v1" if provider == "timeweb" else "https://api.deepseek.com").rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or parsed.hostname != ("api.timeweb.ai" if provider == "timeweb" else "api.deepseek.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or provider == "timeweb" and parsed.path != "/v1"):
        raise ValueError("LLM transport requires the official HTTPS endpoint")
    options = {}
    if provider == "timeweb":
        effort = os.getenv("TIMEWEB_REASONING_EFFORT", "low")
        if effort not in {"low", "medium", "high"}:
            raise ValueError("TIMEWEB_REASONING_EFFORT must be low, medium or high")
        options["reasoning_effort"] = effort
    model = os.getenv(prefix + "_MODEL", "timeweb/gpt-oss-120b" if provider == "timeweb" else "deepseek-flash")
    return provider, base + "/chat/completions", model, os.getenv(prefix + "_API_KEY", ""), options
