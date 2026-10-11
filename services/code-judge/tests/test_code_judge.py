"""Authenticated broker contracts, durable replay and retryable outages."""

from code_judge.main import create_app
from code_judge.runner import DockerRunner, RunnerUnavailable
from fastapi.testclient import TestClient


class Runner:
    available = True
    calls = 0

    def ready(self):
        return self.available

    def judge(self, problem, code):
        self.calls += 1
        if code == "outage":
            raise RunnerUnavailable
        return code == "correct", "accepted" if code == "correct" else "wrong_answer"


def submission(code="correct"):
    return {"owner_id": "candidate", "item_id": "item", "idempotency_key": "session:item",
            "code": code, "problem": {"function_name": "solve", "tests": [{"args": [1], "expected": 2}]}}


def test_auth_ready_and_durable_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_JUDGE_TOKEN", "test-token")
    runner = Runner()
    client = TestClient(create_app(tmp_path, runner=runner))
    assert client.get("/healthz").status_code == 200
    assert client.get("/ready").status_code == 401
    client.headers["x-internal-token"] = "test-token"
    assert client.get("/ready").json()["runner"] == "docker-isolated"
    first = client.post("/submissions", json=submission())
    assert first.json()["correct"] is True
    assert client.post("/submissions", json=submission()).json() == first.json()
    restarted = TestClient(create_app(tmp_path, runner=runner), headers=client.headers)
    assert restarted.post("/submissions", json=submission()).json() == first.json()
    assert runner.calls == 1
    assert client.post("/submissions", json=submission("wrong")).status_code == 409


def test_unavailable_then_retry_is_not_consumed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_JUDGE_TOKEN", "test-token")
    runner = Runner()
    client = TestClient(create_app(tmp_path, runner=runner), headers={"x-internal-token": "test-token"})
    runner.available = False
    assert client.get("/ready").status_code == 503
    assert client.post("/submissions", json=submission()).status_code == 503
    runner.available = True
    assert client.post("/submissions", json=submission("outage")).status_code == 503
    assert client.post("/submissions", json=submission()).json()["correct"] is True


def test_owner_scope_invalid_input_and_missing_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_JUDGE_TOKEN", raising=False)
    client = TestClient(create_app(tmp_path, runner=Runner()))
    assert client.get("/ready").status_code == 503
    monkeypatch.setenv("CODE_JUDGE_TOKEN", "test-token")
    client.headers["x-internal-token"] = "test-token"
    assert client.post("/submissions", json={}).status_code == 422
    assert client.post("/submissions", json={**submission(), "extra": True}).status_code == 422
    a = client.post("/submissions", json=submission()).json()
    b = client.post("/submissions", json={**submission("wrong"), "owner_id": "other"}).json()
    assert a["submission_id"] != b["submission_id"] and b["correct"] is False


def test_isolation_command_and_external_strict_comparison(monkeypatch):
    runner = DockerRunner()
    command = runner.command("test")
    for flag in ("--network=none", "--read-only", "--user=65534:65534", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges:true", "--memory=128m", "--cpus=0.5",
                 "--pids-limit=16", "--log-driver=none", "--pull=never"):
        assert flag in command
    assert not any("mount" in flag or "volume" in flag or "privileged" in flag for flag in command)
    problem = {"function_name": "f", "tests": [{"args": [], "expected": True}]}
    monkeypatch.setattr(runner, "_case", lambda *args: (1, "ok"))
    assert runner.judge(problem, "code") == (False, "wrong_answer")
    monkeypatch.setattr(runner, "_case", lambda *args: (True, "ok"))
    assert runner.judge(problem, "code") == (True, "accepted")
    monkeypatch.setattr(runner, "_case", lambda *args: (None, "timeout"))
    assert runner.judge(problem, "code") == (False, "timeout")
    assert not runner.ready()
