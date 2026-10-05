from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from testing_engine.blueprints import Blueprint, load_blueprints
from testing_engine.main import create_app

REPO_BLUEPRINTS = Path(__file__).resolve().parent.parent / "blueprints"


def test_healthz_reports_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "testing-engine"}


def test_example_blueprint_is_loadable() -> None:
    blueprints = load_blueprints(REPO_BLUEPRINTS)
    ids = [bp.id for bp in blueprints]

    assert "algo-hash-lookup" in ids


def test_blueprint_parsing_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        Blueprint.from_dict({"id": "broken"})


def test_unknown_blueprint_returns_404(tmp_path) -> None:
    client = TestClient(create_app(blueprint_dir=tmp_path))
    response = client.get("/blueprints/nope")

    assert response.status_code == 404
