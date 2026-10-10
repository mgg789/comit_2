"""App factory and routes of the testing engine."""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from testing_engine.blueprints import DEFAULT_BLUEPRINT_DIR, load_blueprints
from testing_engine.judge import judge_answer
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


def create_testing_app(data_dir: Path | None = None) -> FastAPI:
    """Adaptive grade-test contour: sessions, next-task selection, scoring.

    Skeleton pass (docs/testing-design.md P1): MCQ answers are scored inline;
    code and extended answers are recorded and deferred to the code-judge
    service (judging lands with the judge modules). The bank is seeded from
    bank_seed.initial_items() on first start.
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

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.post("/testing/sessions", tags=["sessions"])
    async def create_session(body: dict) -> dict:
        candidate_key = str(body.get("candidate_key") or uuid.uuid4())
        specialization = str(body.get("specialization") or "backend")
        declared_grade = body.get("declared_grade")
        consents = body.get("consents") if isinstance(body.get("consents"), dict) else {}
        session_id = store.create_session(candidate_key, specialization,
                                          declared_grade if isinstance(declared_grade, str) else None)
        session = store.read_session(session_id)
        session["consents"] = {
            "video_analysis": bool(consents.get("video_analysis", False)),
            "screen_recording": bool(consents.get("screen_recording", False)),
            "accepted_at": os.environ.get("TESTING_CONSENT_STAMP") or None,
        }
        store.write_session(session)
        return {"session_id": session_id, "status": "active", "tasks_given": 0}

    def _session_or_404(session_id: str) -> dict:
        try:
            return store.read_session(session_id)
        except (FileNotFoundError, ValueError, KeyError) as error:
            raise HTTPException(status_code=404, detail="session_not_found") from error

    def _family_for(specialization: str) -> str:
        known = {row["family"] for row in FAMILIES}
        return specialization if specialization in known else "algorithms"

    @app.get("/testing/sessions/{session_id}/next", tags=["sessions"])
    async def next_task(session_id: str) -> dict:
        session = _session_or_404(session_id)
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="session_finished")
        state = adaptive.AdaptiveState.from_dict(session["state"])
        if adaptive.session_complete(state):
            raise HTTPException(status_code=409, detail="session_complete")
        candidates = store.list_items(_family_for(session["specialization"]), state.next_level)
        presented = {row["item_id"] for row in session["items"]}
        item = next((row for row in candidates if row["item_id"] not in presented), None)
        if item is None:
            for level in LEVELS:
                if level == state.next_level:
                    continue
                for row in store.list_items(_family_for(session["specialization"]), level):
                    if row["item_id"] not in presented:
                        item = row
                        break
                if item is not None:
                    break
        if item is None:
            # Bank exhausted for this family: finish with the posterior leader
            # when at least one auto-scored answer exists, else stay unclear.
            answered_mcq = sum(1 for row in session["items"] if row["judge_status"] == "auto")
            grade = adaptive.decided_grade(state) if answered_mcq else None
            if grade is None and answered_mcq:
                grade = adaptive.GRADES[max(range(len(state.posterior)),
                                            key=lambda index: state.posterior[index])]
            session.update(status="finished", decided_grade=grade)
            store.write_session(session)
            raise HTTPException(status_code=409, detail="bank_exhausted")
        record = dict(item)
        presented_answer = record.pop("answer_index", None)
        record.pop("exposure_count", None)
        store.mark_served(item["item_id"])
        session["items"].append({"item_id": item["item_id"], "level": item["level"],
                                 "payload": item, "presented_answer": presented_answer,
                                 "answer": None, "correct": None,
                                 "judge_status": "presented", "answered_at": None})
        store.write_session(session)
        return {"session_id": session_id, "answered": state.answered,
                "decided_grade": adaptive.decided_grade(state), "task": record}

    @app.post("/testing/sessions/{session_id}/answer", tags=["sessions"])
    async def submit_answer(session_id: str, body: dict) -> dict:
        session = _session_or_404(session_id)
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="session_finished")
        state = adaptive.AdaptiveState.from_dict(session["state"])
        item_id = str(body.get("item_id", ""))
        presented = next((row for row in session["items"] if row["item_id"] == item_id), None)
        if presented is None or presented.get("answer") is not None:
            raise HTTPException(status_code=404, detail="item_not_presented")
        payload = presented["payload"]
        answer = body.get("answer", body.get("code", ""))
        answer_text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        if payload["type"] == "mcq":
            correct, judge_status = (presented.get("presented_answer") == answer), "auto"
        else:
            correct, judge_status = judge_answer(payload, answer_text)
        if correct is not None:
            adaptive.update(state, payload["level"], correct)
        store.bump_exposure(item_id)
        presented.update(answer=answer_text, correct=correct, judge_status=judge_status,
                         answered_at=None)
        done = adaptive.session_complete(state)
        grade = adaptive.decided_grade(state)
        session.update(state=state.to_dict(), tasks_given=state.answered,
                       status="finished" if done else "active", decided_grade=grade)
        store.write_session(session)
        return {"session_id": session_id, "correct": correct, "judge_status": judge_status,
                "answered": state.answered, "decided_grade": grade,
                "status": "finished" if done else "active"}

    @app.post("/testing/sessions/{session_id}/supervision", tags=["supervision"])
    async def supervision(session_id: str, body: dict) -> dict:
        """Accept aggregate supervision counters (never frames) and store the badge.

        The presence badge is computed server-side from the uploaded totals;
        the employer sees only the badge, counters stay in the operator report.
        """
        session = _session_or_404(session_id)
        clean, problems = validate_aggregates(body)
        if problems:
            raise HTTPException(status_code=400, detail=",".join(problems))
        badge = presence_badge(camera_granted=clean["camera"] == CAMERA_GRANTED,
                               total_seconds=clean["total_seconds"],
                               in_frame_seconds=clean["in_frame_seconds"],
                               multi_face_events=clean["multi_face_events"])
        session["supervision"] = {**clean, "badge": badge, "uploaded_at": _now()}
        store.write_session(session)
        return {"session_id": session_id, "badge": badge}

    @app.post("/testing/sessions/{session_id}/media", tags=["supervision"])
    async def media(session_id: str, body: dict) -> dict:
        """Store raw media for a session - only in the explicitly enabled contour.

        TESTING_RECORDING_MODE=full (env, documented in README) switches capture
        on; the default contour never receives frames, so this endpoint refuses
        them. Retention: 90 days, cleanup_expired_media removes expired files.
        """
        session = _session_or_404(session_id)
        if os.environ.get("TESTING_RECORDING_MODE", "off") != "full":
            raise HTTPException(status_code=409, detail="recording_disabled")
        if not session.get("consents", {}).get("screen_recording"):
            raise HTTPException(status_code=403, detail="screen_recording_consent_required")
        data = body.get("data_base64")
        kind = str(body.get("kind", "screen"))
        if kind not in ("screen", "webcam") or not isinstance(data, str) or not data:
            raise HTTPException(status_code=400, detail="invalid_media")
        media_dir = resolved_data / "media" / session_id
        media_dir.mkdir(parents=True, exist_ok=True)
        import base64
        import binascii

        try:
            blob = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="invalid_base64") from None
        seq = len(list(media_dir.glob("chunk-*")))
        target = media_dir / f"chunk-{seq:04d}-{kind}.bin"
        target.write_bytes(blob)
        return {"session_id": session_id, "stored": target.name,
                "retention_days": MEDIA_RETENTION_DAYS}

    @app.get("/testing/sessions/{session_id}/result", tags=["sessions"])
    async def result(session_id: str) -> dict:
        session = _session_or_404(session_id)
        state = adaptive.AdaptiveState.from_dict(session["state"])
        return {"session_id": session_id, "status": session["status"],
                "decided_grade": session["decided_grade"], "answered": state.answered,
                "correct": state.correct, "tasks_given": session["tasks_given"],
                "posterior": dict(zip(adaptive.GRADES, state.posterior)),
                "items": [{key: row[key] for key in
                           ("item_id", "level", "answer", "correct", "judge_status")}
                          for row in session["items"]],
                "supervision": session.get("supervision", {}).get("badge"),
                "consents": session.get("consents", {})}

    return app


create_testing_app.__name__ = "create_testing_app"
