"""App factory and routes of the testing engine."""

import asyncio
import json
import os
import secrets
import weakref
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from testing_engine.blueprints import DEFAULT_BLUEPRINT_DIR, load_blueprints
from testing_engine.judge import CodeJudgeClient, judge_answer
from testing_engine.publisher import TrustedGradePublisher
from testing_engine.rubric import RubricJudge
from testing_engine.supervision import (
    CAMERA_GRANTED,
    MEDIA_RETENTION_DAYS,
    cleanup_expired_media,
    presence_badge,
    validate_aggregates,
)

LEVELS = (1, 2, 3, 4)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def maintenance(data_dir: Path) -> dict:
    """Retention pass: remove media older than the 90-day window."""
    removed = cleanup_expired_media(data_dir, retention_days=MEDIA_RETENTION_DAYS)
    return {"removed": removed, "retention_days": MEDIA_RETENTION_DAYS}


def create_app(blueprint_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Comit Testing Engine",
        version="0.1.0",
        description="Parameterized blueprints, seeded variants, adaptive grade test.",
    )
    # Explicit arg wins (tests), otherwise the env/cwd default from the
    # blueprints module — the installed package must not derive paths from __file__.
    resolved_dir = blueprint_dir if blueprint_dir is not None else DEFAULT_BLUEPRINT_DIR

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.get("/blueprints", tags=["blueprints"])
    async def get_blueprints() -> dict[str, list[dict[str, object]]]:
        blueprints = load_blueprints(resolved_dir)
        return {"blueprints": [vars(bp) for bp in blueprints]}

    @app.get("/blueprints/{blueprint_id}", tags=["blueprints"])
    async def get_blueprint(blueprint_id: str) -> dict[str, object]:
        for bp in load_blueprints(resolved_dir):
            if bp.id == blueprint_id:
                return vars(bp)
        raise HTTPException(status_code=404, detail=f"blueprint {blueprint_id!r} not found")

    return app


def create_testing_app(data_dir: Path | None = None, *, allow_unauthenticated: bool = False) -> FastAPI:
    """Adaptive grade-test contour: sessions, next-task selection, scoring.

    Code runs only in the isolated broker. Extended answers use a typed rubric
    provider. Unavailable judges never consume an answer or update the grade.
    """
    import uuid

    from testing_engine import adaptive
    from testing_engine.bank_seed import FAMILIES, initial_items
    from testing_engine.store import TestingStore

    app = FastAPI(
        title="Comit Testing Engine (grade test contour)",
        version="0.2.0",
        description="Adaptive grade test: up to 30 auto-checked tasks, early stop on a decided grade.",
    )
    resolved_data = data_dir if data_dir is not None else Path(
        os.environ.get("TESTING_DATA_DIR", Path.cwd() / "data" / "testing"))
    store = TestingStore(resolved_data)
    store.seed_items(initial_items())
    owner_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def testing_enabled() -> bool:
        """Disable every testing operation explicitly without substituting judgments."""
        return os.environ.get("TESTING_ENABLED", "true").lower() == "true"

    async def capabilities(specialization: str) -> dict:
        code_required = specialization in ("algorithms", "python")
        code_ready = await asyncio.to_thread(CodeJudgeClient().ready) if code_required and testing_enabled() else False
        text_configured = testing_enabled() and RubricJudge().configured()
        return {"code_required": code_required, "code_ready": code_ready,
                "text_configured": text_configured,
                "types": ["mcq", "short_answer"] + (["code"] if code_ready else []) +
                         (["extended"] if text_configured else [])}

    @app.on_event("startup")
    async def start_retention():
        """Delete expired recordings on startup and hourly while the service runs."""
        async def sweep():
            while True:
                await asyncio.to_thread(maintenance, resolved_data)
                await asyncio.sleep(3600)
        app.state.retention_task = asyncio.create_task(sweep())

    @app.on_event("shutdown")
    async def stop_retention():
        task = getattr(app.state, "retention_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @app.middleware("http")
    async def internal_access(request: Request, call_next):
        """Protect private sessions; API supplies the owner derived from its login."""
        if request.url.path.startswith("/testing/"):
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 3_100_000:
                return JSONResponse({"detail": "payload_too_large"}, status_code=413)
            token = os.environ.get("TESTING_ENGINE_TOKEN", "")
            # Unauthenticated access requires an explicit isolated-test factory flag.
            if not token and not allow_unauthenticated:
                return JSONResponse({"detail": "testing_not_configured"}, status_code=503)
            if token and not secrets.compare_digest(request.headers.get("x-internal-token", ""), token):
                return JSONResponse({"detail": "invalid_internal_token"}, status_code=401)
            request.state.candidate_key = request.headers.get("x-candidate-id")
            if token and not request.state.candidate_key:
                return JSONResponse({"detail": "candidate_required"}, status_code=403)
            if request.state.candidate_key and store.is_deleted(request.state.candidate_key):
                return JSONResponse({"detail": "candidate_deleted"}, status_code=410)
            if not testing_enabled() and request.url.path != "/testing/preflight":
                return JSONResponse({"detail": "testing_disabled"}, status_code=503)
            # Serialize the owner's file-backed session mutations across awaits:
            # answer retries, media, supervision and next-task cannot lose writes.
            lock = owner_locks.setdefault(request.state.candidate_key or "offline", asyncio.Lock())
            async with lock:
                # The request may have waited behind account deletion after
                # the first marker check; fence it again after acquiring lock.
                if request.state.candidate_key and store.is_deleted(request.state.candidate_key):
                    return JSONResponse({"detail": "candidate_deleted"}, status_code=410)
                return await call_next(request)
        return await call_next(request)

    @app.delete("/internal/candidates/{candidate_id}", tags=["internal"])
    async def delete_candidate(candidate_id: str, request: Request) -> dict[str, object]:
        """Delete all testing sessions/media for a candidate and fence retries.

        This endpoint is service-to-service only. The owner key is hashed for
        the durable tombstone, while the lock serializes deletion with an
        in-flight answer or media write.
        """
        token = os.environ.get("TESTING_ENGINE_TOKEN", "")
        if not token or not secrets.compare_digest(request.headers.get("x-internal-token", ""), token):
            raise HTTPException(status_code=401, detail="invalid_internal_token")
        lock = owner_locks.setdefault(candidate_id, asyncio.Lock())
        async with lock:
            removed = await asyncio.to_thread(store.delete_candidate, candidate_id)
        return {"candidate_id": candidate_id, "deleted": True, "sessions_removed": removed}


    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.get("/testing/preflight", tags=["sessions"])
    async def preflight(request: Request, specialization: str) -> dict:
        """Read durable attempts without consuming a test opportunity."""
        from datetime import UTC, datetime, timedelta
        supported = [row["family"] for row in FAMILIES]
        if specialization not in supported:
            raise HTTPException(status_code=422, detail="unsupported_specialization")
        judges = await capabilities(specialization)
        attempts = [json.loads(path.read_text()) for path in store.sessions_dir.glob("*.json")]
        own = [row for row in attempts if row["candidate_key"] == request.state.candidate_key
               and row["specialization"] == specialization]
        active = next((row for row in own if row["status"] == "active" or (row["status"] == "finished" and not row.get("capture_finalized"))), None)
        media_sequences = []
        if active:
            for chunk in (resolved_data / "media" / active["session_id"]).glob("chunk-*"):
                try:
                    media_sequences.append(int(chunk.name.split("-")[1]))
                except (ValueError, IndexError):
                    continue
        # Provider outages cannot consume a candidate's retake cooldown.
        last = max((row for row in own if row.get("failure_reason") != "judge_unavailable"),
                   key=lambda row: row["created_at"], default=None)
        eligible_at = datetime.fromisoformat(last["created_at"]) + timedelta(days=7) if last else None
        return {"specialization": specialization, "supported_specializations": supported,
                "eligible": testing_enabled() and not active and (eligible_at is None or datetime.now(UTC) >= eligible_at)
                    and (not judges["code_required"] or judges["code_ready"]),
                "judges": judges,
                "unavailable_reason": "testing_disabled" if not testing_enabled() else
                    "code_judge_unavailable" if judges["code_required"] and not judges["code_ready"] else None,
                "next_attempt_at": eligible_at.isoformat() if eligible_at else None,
                "active_session_id": active["session_id"] if active else None,
                "active_session_consents": active.get("consents") if active else None,
                "active_supervision": {key: value for key, value in active["supervision"].items()
                    if key in ("total_seconds", "in_frame_seconds", "focus_lost_seconds", "multi_face_events", "camera")}
                    if active and active.get("supervision") else None,
                "next_sequence": max(media_sequences, default=-1) + 1,
                "recording_enabled": os.environ.get("TESTING_RECORDING_MODE", "off") == "full",
                "active_recording_enabled": bool(active and active.get("recording_mode") == "full"),
                "max_questions": 30, "supported_question_types": judges["types"],
                "active_session_status": active["status"] if active else None}

    async def publish_result(session: dict) -> None:
        """Bound retries, preserve API rejection categories, and keep HTTP I/O off the event loop."""
        from datetime import UTC, datetime, timedelta
        previous = session.get("grade_publication") or {}
        attempts = int(previous.get("attempts", 0))
        if previous.get("published") or previous.get("retryable") is False or attempts >= 3:
            return
        retry_at = previous.get("next_retry_at")
        if retry_at and datetime.now(UTC) < datetime.fromisoformat(retry_at):
            return
        publisher = TrustedGradePublisher()
        published, status = await asyncio.to_thread(publisher.publish, session)
        attempts += 1
        code = int(status.split(":")[1]) if status.startswith("api_status:") else None
        retryable = not published and attempts < 3 and (status == "api_unreachable" or (code is not None and (code >= 500 or code in (408, 429))))
        session["grade_publication"] = {"published": published, "status": status,
            "attempts": attempts, "retryable": retryable,
            "detail": publisher.failure_detail, "retry_at": publisher.retry_at,
            "next_retry_at": (datetime.now(UTC) + timedelta(seconds=5 * attempts)).isoformat() if retryable else None}
        store.write_session(session)

    @app.post("/testing/sessions", tags=["sessions"])
    async def create_session(body: dict, request: Request) -> dict:
        candidate_key = request.state.candidate_key or str(body.get("candidate_key") or uuid.uuid4())
        specialization = str(body.get("specialization") or "backend")
        if specialization not in {row["family"] for row in FAMILIES}:
            raise HTTPException(status_code=422, detail="unsupported_specialization")
        declared_grade = body.get("declared_grade")
        consents = body.get("consents") if isinstance(body.get("consents"), dict) else {}
        recording_mode = "full" if os.environ.get("TESTING_RECORDING_MODE", "off") == "full" else "off"
        if request.state.candidate_key:
            if consents.get("processing") is not True or consents.get("version") != "testing-media-v1":
                raise HTTPException(status_code=422, detail="processing_consent_required")
            if consents.get("screen_recording") is not (recording_mode == "full"):
                raise HTTPException(status_code=422, detail="recording_consent_mismatch")
            from datetime import UTC, datetime, timedelta
            for path in store.sessions_dir.glob("*.json"):
                previous = json.loads(path.read_text())
                if previous["candidate_key"] != candidate_key or previous["specialization"] != specialization:
                    continue
                if previous["status"] == "active":
                    raise HTTPException(status_code=409, detail="attempt_active")
                if previous.get("failure_reason") == "judge_unavailable":
                    continue
                eligible = datetime.fromisoformat(previous["created_at"]) + timedelta(days=7)
                if datetime.now(UTC) < eligible:
                    raise HTTPException(status_code=409, detail="retake_cooldown")
            if specialization in ("algorithms", "python") and not await asyncio.to_thread(CodeJudgeClient().ready):
                raise HTTPException(status_code=503, detail="code_judge_unavailable")
        session_id = store.create_session(candidate_key, specialization,
                                          declared_grade if isinstance(declared_grade, str) else None)
        session = store.read_session(session_id)
        session["recording_mode"] = recording_mode
        session["consents"] = {
            "processing": consents.get("processing") is True,
            "video_analysis": bool(consents.get("video_analysis", False)),
            "screen_recording": bool(consents.get("screen_recording", False)),
            "accepted_at": _now(), "version": consents.get("version", "legacy"), "retention_days": 90,
        }
        store.write_session(session)
        return {"session_id": session_id, "status": "active", "tasks_given": 0}

    def _session_or_404(session_id: str, request: Request) -> dict:
        try:
            session = store.read_session(session_id)
            if request.state.candidate_key and session["candidate_key"] != request.state.candidate_key:
                raise HTTPException(status_code=404, detail="session_not_found")
            return session
        except (FileNotFoundError, ValueError, KeyError) as error:
            raise HTTPException(status_code=404, detail="session_not_found") from error

    def _family_for(specialization: str) -> str:
        known = {row["family"] for row in FAMILIES}
        return specialization if specialization in known else "algorithms"

    @app.get("/testing/sessions/{session_id}/next", tags=["sessions"])
    async def next_task(session_id: str, request: Request) -> dict:
        session = _session_or_404(session_id, request)
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="session_finished")
        state = adaptive.AdaptiveState.from_dict(session["state"])
        if adaptive.session_complete(state):
            raise HTTPException(status_code=409, detail="session_complete")
        pending = next((row for row in session["items"] if row["judge_status"] == "presented"), None)
        if pending:
            record = {key: value for key, value in pending["payload"].items()
                      if key not in ("answer_index", "tests", "rubric", "expected_answers", "exposure_count")}
            return {"session_id": session_id, "answered": state.answered, "decided_grade": None,
                    "task": record, "pending_answer": pending.get("pending_answer")}
        family = _family_for(session["specialization"])
        forced_code = bool(request.state.candidate_key and family in ("algorithms", "python") and len(session["items"]) == 1)
        code_ready = await asyncio.to_thread(CodeJudgeClient().ready) if forced_code else False
        if forced_code and not code_ready:
            raise HTTPException(status_code=503, detail="code_judge_unavailable")
        allowed = {"mcq", "short_answer"}
        text_configured = testing_enabled() and RubricJudge().configured()
        if text_configured and session["items"]:
            allowed.add("extended")
        if code_ready:
            allowed.add("code")
        presented = {row["item_id"] for row in session["items"]}
        extended_slot = 2 if family in ("algorithms", "python") else 1
        forced_extended = bool(text_configured and len(session["items"]) == extended_slot and any(
            store.list_items(family, level, task_types={"extended"}, exclude_ids=presented)
            for level in LEVELS))
        selected_types = {"code"} if forced_code else {"extended"} if forced_extended else allowed
        candidates = store.list_items(family, state.next_level, limit=100,
                                      task_types=selected_types, exclude_ids=presented)
        item = next(iter(candidates), None)
        if item is None:
            for level in LEVELS:
                if level == state.next_level:
                    continue
                candidates = store.list_items(family, level, limit=100,
                                              task_types=selected_types, exclude_ids=presented)
                item = next(iter(candidates), None)
                if item is not None:
                    break
        if item is None or len(session["items"]) >= 30:
            # Bank exhausted for this family: finish with the posterior leader
            # when at least one auto-scored answer exists, else stay unclear.
            answered_mcq = sum(1 for row in session["items"] if type(row["correct"]) is bool)
            grade = adaptive.decided_grade(state) if answered_mcq else None
            if grade is None and answered_mcq:
                grade = adaptive.GRADES[max(range(len(state.posterior)),
                                            key=lambda index: state.posterior[index])]
            session.update(status="finished", decided_grade=grade)
            store.write_session(session)
            if grade and not request.state.candidate_key:
                await publish_result(session)
            raise HTTPException(status_code=409, detail="bank_exhausted")
        record = dict(item)
        if item["type"] == "mcq":
            import secrets
            order = list(range(len(item["options"])))
            secrets.SystemRandom().shuffle(order)
            item = {**item, "options": [item["options"][index] for index in order],
                    "answer_index": order.index(item["answer_index"])}
            record = dict(item)
        presented_answer = record.pop("answer_index", None)
        record.pop("exposure_count", None)
        record.pop("tests", None)
        record.pop("rubric", None)
        record.pop("expected_answers", None)
        if not store.claim_item(item["item_id"]):
            return await next_task(session_id, request)
        session["items"].append({"item_id": item["item_id"], "level": item["level"],
                                 "payload": item, "presented_answer": presented_answer,
                                 "answer": None, "correct": None,
                                 "judge_status": "presented", "answered_at": None})
        store.write_session(session)
        return {"session_id": session_id, "answered": state.answered,
                "decided_grade": adaptive.decided_grade(state), "task": record, "pending_answer": None}

    @app.post("/testing/sessions/{session_id}/answer", tags=["sessions"])
    async def submit_answer(session_id: str, body: dict, request: Request) -> dict:
        session = _session_or_404(session_id, request)
        state = adaptive.AdaptiveState.from_dict(session["state"])
        item_id = str(body.get("item_id", ""))
        presented = next((row for row in session["items"] if row["item_id"] == item_id), None)
        if presented is None:
            raise HTTPException(status_code=404, detail="item_not_presented")
        if presented.get("answer") is not None:
            answer = body.get("answer", body.get("code", ""))
            text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            if text != presented["answer"]:
                raise HTTPException(status_code=409, detail="answer_conflict")
            return {"session_id": session_id, "correct": presented["correct"], "judge_status": presented["judge_status"],
                    "answered": state.answered, "decided_grade": session["decided_grade"], "status": session["status"]}
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="session_finished")
        payload = presented["payload"]
        answer = body.get("answer", body.get("code", ""))
        answer_text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if payload["type"] == "mcq" and (type(answer) is not int or not 0 <= answer < len(payload["options"])):
            raise HTTPException(status_code=422, detail="invalid_answer")
        if len(answer_text) > 20000:
            raise HTTPException(status_code=422, detail="answer_too_large")
        if "pending_answer" in presented and presented["pending_answer"] != answer_text:
            raise HTTPException(status_code=409, detail="answer_conflict")
        if payload["type"] == "mcq":
            correct, judge_status = (presented.get("presented_answer") == answer), "auto"
        else:
            presented["pending_answer"] = answer_text
            store.write_session(session)
            correct, judge_status = await asyncio.to_thread(judge_answer, payload, answer_text, owner=session["candidate_key"],
                key=session_id + ":" + item_id, cache_dir=resolved_data / "rubric-cache")
        if correct is None:
            if judge_status == "runner_busy":
                raise HTTPException(status_code=503, detail="runner_busy")
            attempts = int(presented.get("judge_attempts", 0)) + 1
            presented.update(judge_attempts=attempts, last_judge_status=judge_status)
            if attempts >= 3:
                # No posterior update, false score or cooldown for an unjudgeable response.
                presented.update(answer=answer_text, correct=None, judge_status="unjudged:" + judge_status,
                                 answered_at=_now())
                presented.pop("pending_answer", None)
                session.update(status="abandoned", ended_at=_now(), decided_grade=None,
                               grade_publication=None, failure_reason="judge_unavailable")
                store.write_session(session)
                return {"session_id": session_id, "correct": None, "judge_status": presented["judge_status"],
                        "answered": state.answered, "decided_grade": None, "status": "abandoned"}
            store.write_session(session)
            raise HTTPException(status_code=503, detail=judge_status)
        if correct is not None:
            adaptive.update(state, payload["level"], correct)
        if payload["type"] == "code":
            presented["similarity_evidence"] = store.audit_code_similarity(
                session["candidate_key"], item_id, answer_text, bool(correct), function_name=payload.get("function_name"))
        store.bump_exposure(item_id)
        presented.update(answer=answer_text, correct=correct, judge_status=judge_status,
                         answered_at=_now())
        done = adaptive.session_complete(state)
        grade = adaptive.decided_grade(state)
        if done and grade is None:
            grade = adaptive.GRADES[max(range(len(state.posterior)), key=lambda index: state.posterior[index])]
        session.update(state=state.to_dict(), tasks_given=state.answered,
                       status="finished" if done else "active", decided_grade=grade)
        store.write_session(session)
        if done and grade and not request.state.candidate_key:
            await publish_result(session)
        return {"session_id": session_id, "correct": correct, "judge_status": judge_status,
                "answered": state.answered, "decided_grade": grade,
                "status": "finished" if done else "active"}

    @app.post("/testing/sessions/{session_id}/supervision", tags=["supervision"])
    async def supervision(session_id: str, body: dict, request: Request) -> dict:
        """Accept aggregate supervision counters (never frames) and store the badge.

        The presence badge is computed server-side from the uploaded totals;
        the employer sees only the badge, counters stay in the operator report.
        """
        session = _session_or_404(session_id, request)
        if session.get("capture_finalized"):
            raise HTTPException(status_code=409, detail="capture_finalized")
        clean, problems = validate_aggregates(body)
        if problems:
            raise HTTPException(status_code=400, detail=",".join(problems))
        from datetime import datetime

        elapsed = max(0, (datetime.fromisoformat(_now()) - datetime.fromisoformat(session["created_at"])).total_seconds())
        if clean["total_seconds"] > elapsed + 5:
            raise HTTPException(status_code=400, detail="supervision_exceeds_elapsed")
        previous = session.get("supervision", {})
        for key in ("total_seconds", "in_frame_seconds", "focus_lost_seconds", "multi_face_events"):
            if clean[key] < previous.get(key, 0):
                raise HTTPException(status_code=409, detail="supervision_regressive")
        if previous.get("camera") in ("denied", "unavailable"):
            clean["camera"] = previous["camera"]
        if not session.get("consents", {}).get("video_analysis"):
            clean["camera"] = "denied"
        badge = presence_badge(camera_granted=clean["camera"] == CAMERA_GRANTED,
                               total_seconds=clean["total_seconds"],
                               in_frame_seconds=clean["in_frame_seconds"],
                               multi_face_events=clean["multi_face_events"])
        session["supervision"] = {**clean, "badge": badge, "uploaded_at": _now()}
        store.write_session(session)
        return {"session_id": session_id, "badge": badge}

    @app.post("/testing/sessions/{session_id}/media", tags=["supervision"])
    async def media(session_id: str, body: dict, request: Request) -> dict:
        """Store raw media for a session - only in the explicitly enabled contour.

        TESTING_RECORDING_MODE=full (env, documented in README) switches capture
        on; the default contour never receives frames, so this endpoint refuses
        them. Retention: 90 days, cleanup_expired_media removes expired files.
        """
        session = _session_or_404(session_id, request)
        if session.get("capture_finalized"):
            raise HTTPException(status_code=409, detail="capture_finalized")
        if session.get("recording_mode") != "full" or os.environ.get("TESTING_RECORDING_MODE", "off") != "full":
            raise HTTPException(status_code=409, detail="recording_disabled")
        if not session.get("consents", {}).get("screen_recording"):
            raise HTTPException(status_code=403, detail="screen_recording_consent_required")
        data = body.get("data_base64")
        kind = str(body.get("kind", "screen"))
        if kind not in ("screen", "webcam") or not isinstance(data, str) or not data:
            raise HTTPException(status_code=400, detail="invalid_media")
        if kind == "webcam" and not session.get("consents", {}).get("video_analysis"):
            raise HTTPException(status_code=403, detail="webcam_consent_required")
        if len(data) > 3_000_000:
            raise HTTPException(status_code=413, detail="media_too_large")
        media_dir = resolved_data / "media" / session_id
        media_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        import base64
        import binascii

        try:
            blob = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="invalid_base64") from None
        if sum(path.stat().st_size for path in media_dir.glob("chunk-*")) + len(blob) > 512 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="session_media_limit")
        manifest = media_dir / "manifest.json"
        if not manifest.exists():
            from testing_engine.store import _atomic_write
            _atomic_write(manifest, {"created_at": session["created_at"], "retention_days": 90,
                                     "session_id": session_id, "candidate_key": session["candidate_key"],
                                     "consents": session["consents"]})
        seq = body.get("sequence", len(list(media_dir.glob("chunk-*"))))
        if type(seq) is not int or not 0 <= seq <= 10000:
            raise HTTPException(status_code=422, detail="invalid_sequence")
        target = media_dir / f"chunk-{seq:04d}-{kind}.bin"
        if target.exists() and target.read_bytes() != blob:
            raise HTTPException(status_code=409, detail="media_conflict")
        target.write_bytes(blob)
        target.chmod(0o600)
        manifest.chmod(0o600)
        return {"session_id": session_id, "stored": target.name,
                "retention_days": MEDIA_RETENTION_DAYS}

    @app.get("/testing/sessions/{session_id}/result", tags=["sessions"])
    async def result(session_id: str, request: Request) -> dict:
        session = _session_or_404(session_id, request)
        state = adaptive.AdaptiveState.from_dict(session["state"])
        if session["status"] == "finished" and session.get("decided_grade") and session.get("capture_finalized", not request.state.candidate_key) and not session.get("grade_publication", {}).get("published"):
            await publish_result(session)
        from datetime import datetime, timedelta
        next_attempt = None if session.get("failure_reason") == "judge_unavailable" else (datetime.fromisoformat(session["created_at"]) + timedelta(days=7)).isoformat()
        return {"failure_reason": session.get("failure_reason"), "capture_finalized": bool(session.get("capture_finalized")), "next_attempt_at": next_attempt, "grade_publication": session.get("grade_publication"),
                "session_id": session_id, "status": session["status"],
                "decided_grade": session["decided_grade"], "answered": state.answered,
                "correct": state.correct, "tasks_given": session["tasks_given"],
                "posterior": dict(zip(adaptive.GRADES, state.posterior)),
                "items": [{key: row[key] for key in
                           ("item_id", "level", "answer", "correct", "judge_status")}
                          for row in session["items"]],
                "supervision": session.get("supervision", {}).get("badge"),
                "consents": session.get("consents", {})}

    @app.post("/testing/sessions/{session_id}/finish", tags=["sessions"])
    async def finish(session_id: str, request: Request) -> dict:
        """Finalize evidence after recording drain, then publish with final supervision."""
        session = _session_or_404(session_id, request)
        if session["status"] != "finished":
            raise HTTPException(status_code=409, detail="session_not_finished")
        if not session.get("supervision"):
            raise HTTPException(status_code=409, detail="supervision_required")
        session["capture_finalized"] = True
        store.write_session(session)
        return await result(session_id, request)

    @app.post("/testing/sessions/{session_id}/abandon", tags=["sessions"])
    async def abandon(session_id: str, request: Request) -> dict:
        """Persist a voluntary interruption; never publish an incomplete grade."""
        session = _session_or_404(session_id, request)
        if session["status"] == "active":
            if any(row.get("judge_attempts", 0) > 0 and row.get("answer") is None for row in session["items"]):
                # Leaving an unresolved judge failure must not turn that outage into a penalty.
                session["failure_reason"] = "judge_unavailable"
            session.update(status="abandoned", ended_at=_now(), decided_grade=None, grade_publication=None)
            store.write_session(session)
        return {"session_id": session_id, "status": session["status"]}

    return app


create_testing_app.__name__ = "create_testing_app"
