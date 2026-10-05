"""Regenerate the gateway's OpenAPI source from the actual FastAPI routes."""

import json
from pathlib import Path

from matching_engine.main import create_app

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "apps/api/src/contracts/candidate-pipeline.openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
