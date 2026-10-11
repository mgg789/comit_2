"""The removed local executor cannot be reenabled by a fixture flag."""

from testing_engine.judge import LocalPythonJudge, judge_answer


def test_local_execution_is_always_disabled(monkeypatch):
    monkeypatch.setenv("TESTING_LOCAL_JUDGE", "1")
    for code in ("import posix; posix.system('true')", "__import__('os')",
                 "class Forge:\n def __eq__(self, other): return True"):
        assert LocalPythonJudge().judge({}, code) == (None, "local_judge_disabled")


def test_code_without_judge_service_is_retryable(monkeypatch):
    monkeypatch.delenv("CODE_JUDGE_URL", raising=False)
    assert judge_answer({"type": "code"}, "def f(): return 1") == (None, "code_judge_unavailable")
