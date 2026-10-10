"""App factory of the code-judge service: storage + API skeleton.

The sandboxed runner is delivered as a separate reviewed module (it executes
untrusted candidate code); until it is installed the service stores
submissions with status "stored" and verdict "deferred" - the testing engine
treats those as not-yet-scored. Endpoints: POST /submissions, GET
/submissions/{id}, GET /submissions (recent), GET /healthz, GET /ready.
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from code_judge.dmoj import DmojBridge, package_problem
from code_judge.store import SubmissionStore


def create_app(data_dir: Path | None = None) -> FastAPI:
    import os

    app = FastAPI(
        title="Comit Code Judge",
        version="0.1.0",
        description="Submission storage and sandboxed execution bridge (DMOJ packaging included).",
    )
    resolved_dir = data_dir if data_dir is not None else Path(
        os.environ.get("CODE_JUDGE_DATA_DIR", Path.cwd() / "data" / "code-judge"))
    store = SubmissionStore(resolved_dir)
    bridge = DmojBridge()

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "code-judge"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "code-judge",
                "runner": "not_installed", "dmoj_bridge": "enabled" if bridge.enabled() else "disabled"}

    @app.post("/submissions", tags=["submissions"])
    async def create_submission(body: dict) -> dict:
        code = body.get("code")
        if not isinstance(code, str) or not (0 < len(code) <= 100_000):
            raise HTTPException(status_code=400, detail="invalid_code")
        language = str(body.get("language", "python"))
        if language not in ("python",):
            raise HTTPException(status_code=400, detail="unsupported_language")
        record = store.create(str(body.get("item_id", "")), language, code)
        return {key: record[key] for key in ("submission_id", "status", "item_id")}

    @app.get("/submissions/{submission_id}", tags=["submissions"])
    async def read_submission(submission_id: str) -> dict:
        try:
            return store.read(submission_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail="submission_not_found") from error

    @app.get("/submissions", tags=["submissions"])
    async def list_submissions(item_id: str = "", limit: int = 20) -> dict:
        limit = max(1, min(limit, 100))
        records = []
        for path in sorted(store.submissions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            record = json.loads(path.read_text(encoding="utf-8"))
            if item_id and record.get("item_id") != item_id:
                continue
            records.append({key: record.get(key) for key in ("submission_id", "item_id", "status", "correct")})
            if len(records) >= limit:
                break
        return {"submissions": records, "limit": limit}

    @app.post("/problems/{item_id}/package", tags=["dmoj"])
    async def package_dmoj_problem(item_id: str, body: dict) -> dict:
        """Package a bank code item into a DMOJ problem folder under the data dir."""
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid_payload")
        try:
            folder = package_problem(item_id, payload, resolved_dir / "problems")
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"item_id": item_id, "problem_dir": str(folder)}

    app.state.store = store
    app.state.dmoj_bridge = bridge
    return app
