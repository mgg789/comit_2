"""Authenticated internal broker for disposable Python judge containers."""

import asyncio
import hashlib
import json
import os
import secrets
import threading
import weakref
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from code_judge.runner import DockerRunner, RunnerUnavailable
from code_judge.store import SubmissionStore


class Case(BaseModel):
    """A trusted bank case; expected never enters the sandbox."""

    model_config = ConfigDict(extra="forbid")
    args: list
    expected: object


class Problem(BaseModel):
    """Bounded trusted execution specification."""

    model_config = ConfigDict(extra="forbid")
    function_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    tests: list[Case] = Field(min_length=1, max_length=8)


class Submission(BaseModel):
    """Internal-only submission; identity is supplied by testing-engine."""

    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=20000)
    problem: Problem


def create_app(data_dir: Path | None = None, *, runner: DockerRunner | None = None) -> FastAPI:
    """Build the broker; auth and execution availability fail closed."""
    app = FastAPI(title="Comit Code Judge", version="0.2.0")
    store = SubmissionStore(data_dir or Path(os.getenv("CODE_JUDGE_DATA_DIR", "data/code-judge")))
    executor = runner or DockerRunner()
    registry_lock = threading.Lock()
    identity_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
    concurrency = max(1, min(4, int(os.getenv("CODE_JUDGE_CONCURRENCY", "2"))))
    execution_slots = threading.BoundedSemaphore(concurrency)

    @app.on_event("startup")
    async def clean_previous_process() -> None:
        """This file-backed broker exclusively owns its dedicated sandbox daemon."""
        if runner is None:
            await asyncio.to_thread(executor.cleanup_stale)

    @app.middleware("http")
    async def internal_access(request: Request, call_next):
        if request.url.path != "/healthz":
            token = os.getenv("CODE_JUDGE_TOKEN", "")
            if not token:
                return JSONResponse({"detail": "judge_not_configured"}, status_code=503)
            if not secrets.compare_digest(token, request.headers.get("x-internal-token", "")):
                return JSONResponse({"detail": "invalid_internal_token"}, status_code=401)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 100000:
                return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "code-judge"}

    @app.get("/ready")
    def ready() -> dict:
        if not executor.ready():
            raise HTTPException(503, "runner_unavailable")
        return {"status": "ok", "runner": "docker-isolated"}

    @app.post("/submissions")
    def submit(body: Submission) -> dict:
        # One worker/process per file-backed instance; serialize duplicate calls.
        identity = hashlib.sha256((body.owner_id + "\0" + body.idempotency_key).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        with registry_lock:
            identity_lock = identity_locks.setdefault(identity, threading.Lock())
        with identity_lock:
            try:
                record = store.read(identity)
            except FileNotFoundError:
                record = None
            if record:
                if record["fingerprint"] != fingerprint:
                    raise HTTPException(409, "submission_conflict")
                return {key: record[key] for key in ("submission_id", "status", "correct", "verdict_kind")}
            if not execution_slots.acquire(timeout=1):
                raise HTTPException(503, "runner_busy")
            try:
                if not executor.ready():
                    raise RunnerUnavailable
                correct, kind = executor.judge(body.problem.model_dump(), body.code)
            except RunnerUnavailable as error:
                raise HTTPException(503, "runner_unavailable") from error
            finally:
                execution_slots.release()
            record = {"submission_id": identity, "fingerprint": fingerprint,
                      "status": "judged", "correct": correct, "verdict_kind": kind}
            store.save(record)
            return {key: record[key] for key in ("submission_id", "status", "correct", "verdict_kind")}

    app.state.store = store
    app.state.runner = executor
    return app
