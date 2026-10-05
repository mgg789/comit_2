"""App factory and routes of the testing engine."""

from pathlib import Path

from fastapi import FastAPI, HTTPException

from testing_engine.blueprints import Blueprint, load_blueprints

BLUEPRINT_DIR = Path(__file__).resolve().parent.parent.parent / "blueprints"


def create_app(blueprint_dir: Path = BLUEPRINT_DIR) -> FastAPI:
    app = FastAPI(
        title="Comit Testing Engine",
        version="0.1.0",
        description="Parameterized blueprints, seeded variants, adaptive grade test.",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "testing-engine"}

    @app.get("/blueprints", tags=["blueprints"])
    async def get_blueprints() -> dict[str, list[dict[str, object]]]:
        blueprints = load_blueprints(blueprint_dir)
        return {"blueprints": [vars(bp) for bp in blueprints]}

    @app.get("/blueprints/{blueprint_id}", tags=["blueprints"])
    async def get_blueprint(blueprint_id: str) -> dict[str, object]:
        for bp in load_blueprints(blueprint_dir):
            if bp.id == blueprint_id:
                return vars(bp)
        raise HTTPException(status_code=404, detail=f"blueprint {blueprint_id!r} not found")

    return app
