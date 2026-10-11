"""Concurrency and candidate runtime budgets are tested with inert runners, never host code execution."""
import threading
from concurrent.futures import ThreadPoolExecutor

from code_judge.main import create_app
from code_judge.runner import DockerRunner, RunnerUnavailable
from fastapi.testclient import TestClient
from test_code_judge import submission


def test_parallel_identities_do_not_block_readiness_or_duplicate_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_JUDGE_TOKEN", "synthetic")
    monkeypatch.setenv("CODE_JUDGE_CONCURRENCY", "2")
    entered = threading.Barrier(3)
    release = threading.Event()
    calls = []
    class Runner:
        def ready(self):
            return True
        def judge(self, problem, code):
            calls.append(code)
            entered.wait(timeout=3)
            assert release.wait(3)
            return True, "accepted"
    client = TestClient(create_app(tmp_path, runner=Runner()), headers={"x-internal-token": "synthetic"})
    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(client.post, "/submissions", json=submission())
        second = executor.submit(client.post, "/submissions", json={**submission(), "owner_id": "other"})
        entered.wait(timeout=3)
        assert client.get("/ready").status_code == 200
        duplicate = executor.submit(client.post, "/submissions", json=submission())
        release.set()
        assert first.result().json() == duplicate.result().json()
        assert second.result().status_code == 200
    assert len(calls) == 2


def test_total_candidate_runtime_is_terminal_but_startup_wall_limit_is_infrastructure(monkeypatch):
    runner = DockerRunner()
    problem = {"function_name": "solve", "tests": [{"args": [], "expected": 1} for _ in range(8)]}
    def slow_candidate(code, name, args, budget, deadline):
        budget[0] -= 5
        return 1, "ok"
    monkeypatch.setattr(runner, "_case", slow_candidate)
    assert runner.judge(problem, "inert") == (False, "timeout")
    clock = [0.0]
    monkeypatch.setattr("code_judge.runner.time.monotonic", lambda: clock[0])
    def slow_infrastructure(code, name, args, budget, deadline):
        clock[0] += 50
        return 1, "ok"
    monkeypatch.setattr(runner, "_case", slow_infrastructure)
    import pytest
    with pytest.raises(RunnerUnavailable, match="infrastructure_timeout"):
        runner.judge(problem, "inert")
