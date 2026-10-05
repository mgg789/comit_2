"""App factory and routes of the matching engine.

Skeleton scope (S1): health endpoints only. The combine pipeline
(strict filter -> scoring -> top-k -> reranker) lands in sprint S3 (context/70).
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Comit Matching Engine",
        version="0.1.0",
        description="Strict filter, hybrid scoring, top-k reranker, tinder feedback loop.",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "matching-engine"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        # pgvector connectivity lands with the schema migration (S1/S3).
        return {"status": "ok", "service": "matching-engine"}

    return app
