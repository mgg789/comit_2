"""End-to-end tests of the adaptive grade-test contour (file storage, no network)."""

from pathlib import Path

from fastapi.testclient import TestClient
from testing_engine import adaptive
from testing_engine.main import create_testing_app
from testing_engine.store import TestingStore


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_testing_app(data_dir=tmp_path / "testing-data", allow_unauthenticated=True))


def test_seed_bank_covers_all_families_and_levels(tmp_path: Path) -> None:
    """The initial bank contains every family at every level."""
    store = TestingStore(tmp_path / "data")
    from testing_engine.bank_seed import FAMILIES, initial_items

    inserted = store.seed_items(initial_items())
    assert inserted == 44
    for family in FAMILIES:
        for level in (1, 2, 3, 4):
            assert store.list_items(family["family"], level), (family, level)


def test_adaptive_engine_decides_a_consistent_strong_candidate() -> None:
    """A candidate answering everything correctly settles at senior."""
    state = adaptive.AdaptiveState()
    levels = [2, 3, 4, 4, 4]
    for level in levels:
        if adaptive.session_complete(state):
            break
        adaptive.update(state, level, True)
    assert adaptive.decided_grade(state) == "senior"
    assert adaptive.session_complete(state) is True


def test_session_lifecycle_mcq_scoring_and_finish(tmp_path: Path, monkeypatch) -> None:
    """Create session, answer MCQs, reach a decided grade, read the result.

    Code tasks require the offline fixture judge (TESTING_LOCAL_JUDGE=1):
    production has no local fallback for untrusted code (issue #18).
    """
    monkeypatch.setenv("TESTING_LOCAL_JUDGE", "1")
    client = _client(tmp_path)
    created = client.post("/testing/sessions", json={
        "candidate_key": "candidate-1", "specialization": "algorithms",
        "declared_grade": "middle"}).json()
    session_id = created["session_id"]

    for _ in range(30):
        response = client.get(f"/testing/sessions/{session_id}/next")
        if response.status_code == 409:
            break
        task = response.json()
        task_payload = task["task"]
        function_name = task_payload.get("function_name")
        answer = client.post(f"/testing/sessions/{session_id}/answer", json={
            "item_id": task_payload["item_id"],
            **({"code": f"def {function_name}(*args):\n    return None"}
               if task_payload["type"] == "code"
               else {"answer": _correct_index(task_payload["prompt"])
                     if task_payload["type"] in ("mcq", "short_answer") else "развернутый ответ"})})
        body = answer.json()
        if task_payload["type"] == "code":
            # Secure default (issue #18): without a configured judge service,
            # code answers are recorded unscored.
            assert body["judge_status"] == "code_judge_unavailable"
            assert body["correct"] is None
            continue
        assert body["judge_status"] == "auto", body
        assert body["correct"] is True, (task_payload["prompt"], task_payload.get("options"))
        if body["status"] == "finished":
            break

    result = client.get(f"/testing/sessions/{session_id}/result").json()
    assert result["status"] == "finished"
    assert result["decided_grade"] in ("intern", "junior", "middle", "senior")
    assert result["answered"] >= 2


def _correct_index(prompt: str) -> int | str:
    """Look up the stored answer for a prompt via the bank (test-only helper)."""
    from testing_engine.bank_seed import initial_items

    for item in initial_items():
        if item["prompt"] == prompt:
            if item["type"] == "mcq":
                return item["answer_index"]
            if item["type"] == "short_answer":
                return item["expected_answers"][0]
    raise AssertionError("prompt not in bank: " + prompt)


def test_unknown_session_is_404(tmp_path: Path) -> None:
    """Missing sessions give a clean 404."""
    client = _client(tmp_path)
    assert client.get("/testing/sessions/missing/result").status_code == 404
