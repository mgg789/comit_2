"""Demo content is varied, stable, declaration-only and safely isolated by default."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data/demo"))
from catalog import build_catalog
from seed import validate_target


def test_catalog_is_deterministic_and_varied():
    first = build_catalog()
    assert first == build_catalog()
    assert len(first["candidates"]) == 240 and len(first["vacancies"]) == 48
    assert len({item["id"] for item in first["candidates"]}) == 240
    assert len({item["fields"]["display_name"] for item in first["candidates"]}) == 240
    assert len({item["fields"]["city"] for item in first["candidates"]}) == 10
    assert len({item["fields"]["primary_direction"] for item in first["candidates"]}) == 8
    for item in first["candidates"]:
        assert item["email"].endswith("@comit-demo.invalid")
        assert "Демонстрационное" in item["fields"]["description"]
        assert not set(item["fields"]) & {"evidence", "grade_result", "fsp_id", "phone"}
    for item in first["vacancies"]:
        assert item["fields"]["zp_min"] < item["fields"]["zp_max"]
        assert item["fields"]["metadata"]["synthetic"] is True
    assert json.loads((ROOT / "data/demo/catalog.json").read_text()) == first


def test_only_explicit_local_demo_database_can_be_written():
    validate_target("postgresql://user:synthetic@127.0.0.1:35437/comit_demo_check", "comit_demo_check")
    for url, target in [("postgresql://host/comit_demo_check", "comit_demo_check"), ("postgresql://localhost/comit", "comit"), ("postgresql://localhost/comit_demo_other", "comit_demo_check")]:
        with pytest.raises(ValueError):
            validate_target(url, target)
