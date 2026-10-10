"""Tests of the code-judge service skeleton (storage, API, DMOJ packaging)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from code_judge.dmoj import DmojBridge, package_problem
from code_judge.main import create_app
from code_judge.store import SubmissionStore


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path / "code-judge-data"))


def test_submission_lifecycle_stores_and_reads(tmp_path: Path) -> None:
    """Create, read and list submissions; storage keeps the full code payload."""
    client = _client(tmp_path)
    created = client.post("/submissions", json={
        "item_id": "algorithms-L2-5", "language": "python", "code": "def two_sum(a, b):\n    return []"}).json()
    assert created["status"] == "stored"
    record = client.get(f"/submissions/{created['submission_id']}").json()
    assert record["code"].startswith("def two_sum")
    assert record["correct"] is None
    listed = client.get("/submissions", params={"item_id": "algorithms-L2-5"}).json()
    assert len(listed["submissions"]) == 1


def test_submission_validation(tmp_path: Path) -> None:
    """Bad payloads are rejected with 400; unknown ids with 404."""
    client = _client(tmp_path)
    assert client.post("/submissions", json={"code": ""}).status_code == 400
    assert client.post("/submissions", json={"code": "x=1", "language": "ruby"}).status_code == 400
    assert client.get("/submissions/does-not-exist").status_code == 404


def test_dmoj_packaging_writes_problem_folder(tmp_path: Path) -> None:
    """A bank item packages into init.yml plus per-case in/out files."""
    payload = {"function_name": "two_sum",
               "tests": [{"args": [[1, 2], 3], "expected": True},
                          {"args": [[1], 3], "expected": False}]}
    folder = package_problem("algorithms-L2-5", payload, tmp_path / "problems")
    init = json.loads((folder / "init.yml").read_text(encoding="utf-8"))
    assert init["harness"]["function_name"] == "two_sum"
    assert (folder / "tests" / "01.in").exists() and (folder / "tests" / "02.out").exists()


def test_dmoj_bridge_is_disabled_by_default(tmp_path: Path) -> None:
    """The live judge bridge never runs without an explicit operator address."""
    bridge = DmojBridge()
    assert bridge.enabled() is False
    try:
        bridge.judge(tmp_path, "code", "python")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("disabled bridge must not run")


def test_store_rejects_path_traversal(tmp_path: Path) -> None:
    """Submission ids cannot escape the storage directory."""
    store = SubmissionStore(tmp_path / "data")
    try:
        store.read("../../etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal accepted")
