"""Import the production demo-data generator for fixture consumers without copying its content."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("comit_demo_catalog", Path(__file__).resolve().parents[2] / "data/demo/catalog.py")
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_catalog = _module.build_catalog
