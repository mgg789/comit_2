"""App factory and routes of the testing engine."""

from pathlib import Path

from fastapi import FastAPI, HTTPException

from testing_engine.blueprints import DEFAULT_BLUEPRINT_DIR, Blueprint, load_blueprints


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
