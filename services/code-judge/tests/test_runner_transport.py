"""Exercise bounded pipe handling without executing candidate code on the host."""

import os
from types import SimpleNamespace

import pytest
from code_judge import runner as module
from code_judge.runner import DockerRunner, RunnerUnavailable


@pytest.mark.parametrize("output,exit_code,expected", [
    (b"6", 0, (6, "ok")), (b"broken", 0, (None, "invalid_output")),
    (b"NaN", 0, (None, "invalid_output")),
    (b"error", 1, (None, "runtime_error")),
    (b"", 125, (None, "runtime_error")), (b"", 126, (None, "runtime_error")), (b"", 127, (None, "runtime_error")), (b"123456789", 0, (None, "output_limit")),
])
def test_bounded_transport(monkeypatch, output, exit_code, expected):
    resources = []
    class Process:
        def __init__(self, *args, **kwargs):
            source_read, source_write = os.pipe()
            output_read, output_write = os.pipe()
            os.write(output_write, module.READY_MARKER + output)
            os.close(output_write)
            resources.append(source_read)
            self.stdin = os.fdopen(source_write, "wb", buffering=0)
            self.stdout = os.fdopen(output_read, "rb", buffering=0)
            resources.extend([self.stdin, self.stdout])
        def wait(self, timeout):
            return exit_code
        def poll(self):
            return exit_code
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(module, "OUTPUT_LIMIT", 8)
    try:
        assert DockerRunner()._case("untrusted text is never executed", "solve", []) == expected
    finally:
        for value in resources:
            os.close(value) if isinstance(value, int) else value.close()


def test_start_and_cleanup_failures_are_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable")
    monkeypatch.setattr(module.subprocess, "Popen", unavailable)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""))
    with pytest.raises(RunnerUnavailable):
        DockerRunner()._case("text", "solve", [])
    assert not DockerRunner().ready()
    monkeypatch.setattr(module.subprocess, "run", unavailable)
    with pytest.raises(RunnerUnavailable, match="cleanup"):
        DockerRunner()._case("text", "solve", [])


def test_deadline_kills_cli_and_removes_container(monkeypatch):
    source_read, source_write = os.pipe()
    output_read, output_write = os.pipe()
    stdin = os.fdopen(source_write, "wb", buffering=0)
    stdout = os.fdopen(output_read, "rb", buffering=0)
    os.write(output_write, module.READY_MARKER)
    killed = []
    removed = []
    process = SimpleNamespace(stdin=stdin, stdout=stdout, poll=lambda: None,
        kill=lambda: killed.append(True), wait=lambda timeout: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    def cleanup(command, **kwargs):
        removed.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(module.subprocess, "run", cleanup)
    executor = DockerRunner()
    executor.timeout = 0
    try:
        assert executor._case("text", "solve", []) == (None, "timeout")
        assert killed and removed[0][:3] == ["docker", "rm", "--force"]
    finally:
        stdin.close()
        stdout.close()
        os.close(source_read)
        os.close(output_write)


def test_legacy_package_and_store(tmp_path):
    from code_judge.dmoj import DmojBridge, load_submission_payload, package_problem
    from code_judge.store import SubmissionStore
    problem = {"function_name": "f", "tests": [{"args": [1], "expected": True}]}
    folder = package_problem("item", problem, tmp_path / "problems")
    assert (folder / "init.yml").exists()
    with pytest.raises(ValueError):
        package_problem("item", {}, tmp_path)
    assert not DmojBridge().enabled()
    with pytest.raises(NotImplementedError):
        DmojBridge().judge(tmp_path, "code", "python")
    store = SubmissionStore(tmp_path / "data")
    created = store.create("item", "python", "text")
    assert store.mark_judged(created["submission_id"], True, "accepted")["correct"]
    assert load_submission_payload(store._path(created["submission_id"]))["status"] == "judged"
    with pytest.raises(ValueError):
        store.read("../bad")


def test_startup_cleanup_targets_only_managed_label(monkeypatch):
    calls = []
    def invoke(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=b"abc123\ndef456\n", returncode=0)
    monkeypatch.setattr(module.subprocess, "run", invoke)
    DockerRunner().cleanup_stale()
    assert calls == [["docker", "ps", "-aq", "--filter", "label=comit.code-judge=managed"],
                     ["docker", "rm", "--force", "abc123", "def456"]]
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=b"-invalid-id"))
    with pytest.raises(RunnerUnavailable):
        DockerRunner().cleanup_stale()
