"""Bounded, redirect-free provider connectors."""

from __future__ import annotations

import asyncio
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REPOSITORY_BYTES = 32 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 15
PUBLIC_REPOSITORY_HOSTS = {
    "github.com", "gitlab.com", "bitbucket.org", "gitverse.ru", "sourcecraft.dev", "git.sourcecraft.dev",
}


class ConnectorUnavailable(RuntimeError):
    """Provider cannot be contacted with the configured credentials."""


class ProviderRejected(RuntimeError):
    """Provider answered, but the requested public fact is absent or invalid."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent provider redirects from hiding a non-canonical destination."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


_OPENER = build_opener(NoRedirectHandler)


@dataclass(frozen=True)
class RepositoryProbe:
    """Actual public Git tree/content observation, not an HTML page check."""

    url: str
    status_code: int
    nonempty: bool
    bytes_observed: int
    canonical_url: str = ""
    tree_entries: int = 0
    content_bytes: int = 0
    provider: str = ""


@dataclass(frozen=True)
class HhResume:
    """Allowlisted HH resume facts; ownership remains unproven."""

    resume_id: str
    title: str | None
    updated_at: str | None
    area: str | None
    experience: list[dict[str, str]] | None = None


def canonical_repository_url(url: str) -> tuple[str, str, str, str]:
    """Validate and canonicalize an HTTPS public repository URL."""

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise ProviderRejected("repository_url_not_canonical") from error
    if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
        raise ProviderRejected("repository_url_not_canonical")
    if parsed.query or parsed.fragment or host not in PUBLIC_REPOSITORY_HOSTS:
        raise ProviderRejected("repository_url_not_canonical")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    max_parts = 8 if host == "gitlab.com" else 2
    if len(parts) < 2 or len(parts) > max_parts or any(
        not re.fullmatch(r"[A-Za-z0-9._~-]+", part) or part in {".", ".."} for part in parts
    ):
        raise ProviderRejected("repository_url_invalid_path")
    path = "/" + "/".join(parts)
    return urlunsplit(("https", host, path, "", "")), host, "/".join(parts[:-1]), parts[-1]


def _read(url: str, *, headers: dict[str, str], token: str | None = None) -> tuple[int, bytes]:
    """Read a bounded response without following redirects or exposing body logs."""

    request_headers = dict(headers)
    if token is not None:
        request_headers["authorization"] = f"Bearer {token}"
    request = Request(url, headers=request_headers, method="GET")
    try:
        with _OPENER.open(request, timeout=8) as response:
            return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        return int(error.code), error.read(MAX_RESPONSE_BYTES + 1)
    except (URLError, TimeoutError, OSError) as error:
        raise ConnectorUnavailable("provider_unavailable") from error


def _json(url: str, *, headers: dict[str, str], token: str | None = None) -> tuple[int, dict[str, Any]]:
    status, body = _read(url, headers=headers, token=token)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ConnectorUnavailable("provider_response_too_large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConnectorUnavailable("provider_invalid_json") from error
    if not isinstance(value, dict):
        raise ConnectorUnavailable("provider_invalid_json")
    return status, value


async def fetch_json(url: str, *, token: str | None = None) -> dict[str, Any]:
    """Fetch a bounded JSON object without logging response bodies."""

    def read() -> dict[str, Any]:
        status, value = _json(url, headers={"accept": "application/json", "user-agent": "comit-verification/0.1"}, token=token)
        if status == 404:
            raise ProviderRejected("provider_not_found")
        if status < 200 or status >= 300:
            raise ConnectorUnavailable("provider_unavailable")
        return value

    return await asyncio.to_thread(read)


GIT_OPTIONS = ("-c", "http.followRedirects=false", "-c", "protocol.allow=never",
               "-c", "protocol.https.allow=always", "-c", "core.hooksPath=/dev/null",
               "-c", "credential.helper=")


def _directory_size(root: str) -> int:
    """Return bounded checkout size without following symlinks."""
    total = 0
    for path in Path(root).rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a Git process and every child it may have spawned."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _run_git(arguments: list[str], *, root: str | None = None, timeout: float = GIT_TIMEOUT_SECONDS,
             size_root: str | None = None) -> tuple[int, bytes, bytes]:
    """Run Git with bounded stdout/stderr, timeout and checkout-size monitoring."""
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "LC_ALL": "C", "HOME": tempfile.gettempdir()}
    command = ["git", *GIT_OPTIONS]
    if root:
        command.extend(["--git-dir", root])
    command.extend(arguments)
    try:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError as error:
        raise ConnectorUnavailable("repository_provider_unavailable") from error
    assert process.stdout is not None and process.stderr is not None
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stderr.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    exceeded = False
    try:
        while selector.get_map():
            if size_root is not None and _directory_size(size_root) > MAX_REPOSITORY_BYTES:
                exceeded = True
                _kill_process(process)
            if time.monotonic() > deadline:
                _kill_process(process)
                raise ConnectorUnavailable("repository_provider_timeout")
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = output[key.data]
                if len(buffer) + len(chunk) > MAX_RESPONSE_BYTES:
                    _kill_process(process)
                    raise ConnectorUnavailable("repository_provider_output_too_large")
                buffer.extend(chunk)
            if process.poll() is not None and not selector.get_map():
                break
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            _kill_process(process)
            process.wait(timeout=1)
            raise ConnectorUnavailable("repository_provider_timeout") from error
    finally:
        selector.close()
        if process.poll() is None:
            _kill_process(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    if exceeded:
        raise ConnectorUnavailable("repository_response_too_large")
    return process.returncode or 0, bytes(output["stdout"]), bytes(output["stderr"])


def _probe_git_sync(url: str) -> RepositoryProbe:
    """Inspect a public repository using a bounded hook-free shallow Git clone."""

    canonical, host, _owner, _repository = canonical_repository_url(url)
    clone_url = canonical + ".git"
    # GitVerse and SourceCraft intentionally use their documented clone hosts.
    if host == "sourcecraft.dev":
        clone_url = canonical.replace("https://sourcecraft.dev/", "https://git.sourcecraft.dev/") + ".git"
    root = tempfile.mkdtemp(prefix="comit-verification-")
    try:
        returncode, _stdout, stderr = _run_git(["clone", "--bare", "--depth=1", "--no-tags", clone_url, root], size_root=root)
        if returncode != 0:
            if b"not found" in stderr.lower() or b"repository not found" in stderr.lower():
                return RepositoryProbe(url, 404, False, 0, canonical, provider=host)
            return RepositoryProbe(url, 503, False, 0, canonical, provider=host)
        tree_code, tree_output, _tree_error = _run_git(["ls-tree", "-z", "-r", "--long", "HEAD"], root=root)
        entries = 0
        content_bytes = 0
        for record in tree_output.split(b"\0"):
            if not record:
                continue
            header, separator, _path = record.partition(b"\t")
            parts = header.split()
            if not separator or len(parts) != 4 or parts[1] != b"blob":
                continue
            try:
                length = int(parts[3])
            except ValueError:
                continue
            if length < 0:
                continue
            entries += 1
            content_bytes += length
            if content_bytes > MAX_REPOSITORY_BYTES:
                raise ConnectorUnavailable("repository_response_too_large")
        if tree_code != 0 and entries == 0:
            return RepositoryProbe(url, 503, False, 0, canonical, provider=host)
        return RepositoryProbe(url, 200, entries > 0 and content_bytes > 0, min(content_bytes, MAX_RESPONSE_BYTES),
                               canonical, tree_entries=entries, content_bytes=content_bytes, provider=host)
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def probe_public_repository(url: str, *, fetcher: Callable[[str], RepositoryProbe] | None = None) -> RepositoryProbe:
    """Prove a real public Git tree and at least one non-empty file."""

    if fetcher is not None:
        return await asyncio.to_thread(fetcher, url)
    return await asyncio.to_thread(_probe_git_sync, url)


async def fetch_hh_resume(resume_id: str, *, token: str, user_agent: str) -> HhResume:
    """Read one HH resume through the documented read-only endpoint."""

    def read() -> HhResume:
        if not token or len(token) > 4096 or not user_agent or len(user_agent) > 300:
            raise ConnectorUnavailable("hh_credentials_invalid")
        status, value = _json(f"https://api.hh.ru/resumes/{quote(resume_id, safe='')}",
                              headers={"accept": "application/json", "HH-User-Agent": user_agent}, token=token)
        if status == 404:
            raise ProviderRejected("hh_resume_not_found")
        if status in {401, 403}:
            raise ConnectorUnavailable("hh_token_rejected")
        if status < 200 or status >= 300:
            raise ConnectorUnavailable("hh_provider_unavailable")
        returned = value.get("id")
        if not isinstance(returned, str) or returned != resume_id:
            raise ConnectorUnavailable("hh_invalid_response")
        title = value.get("title") if isinstance(value.get("title"), str) else None
        updated = value.get("updated_at") if isinstance(value.get("updated_at"), str) else None
        area_value = value.get("area")
        area = area_value.get("name") if isinstance(area_value, dict) and isinstance(area_value.get("name"), str) else None
        experience: list[dict[str, str]] = []
        raw_experience = value.get("experience")
        if isinstance(raw_experience, list):
            for item in raw_experience[:100]:
                if not isinstance(item, dict):
                    continue
                entry: dict[str, str] = {}
                for source, target in (("position", "title"), ("name", "company"), ("company", "company")):
                    candidate = item.get(source)
                    if isinstance(candidate, str) and candidate.strip() and target not in entry:
                        entry[target] = candidate[:300]
                if entry:
                    experience.append(entry)
        return HhResume(resume_id=returned, title=title, updated_at=updated, area=area, experience=experience)

    return await asyncio.to_thread(read)
