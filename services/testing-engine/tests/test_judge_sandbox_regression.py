"""Regression tests for issue #18: sandbox escape and verdict forgery.

PoCs from the issue (verified by the reporter on c3483e7):
1. `import posix; posix.system(...)` executed OS commands (posix was absent
   from the denylist).
2. A candidate object with `__eq__ = lambda *a: True` forged every verdict
   because the harness compared with `==` inside the candidate's process.

Both must be impossible now: the import allowlist rejects unlisted modules,
and the parent compares JSON-serialised values, never candidate objects.
The local judge additionally requires TESTING_LOCAL_JUDGE=1 and is a fixture
only - production judges code via the code-judge service (issue #18 fix).
"""


from testing_engine.judge import LocalPythonJudge, judge_answer

CODE_ITEM = {"function_name": "two_sum", "type": "code",
             "tests": [{"args": [[1, 2, 3], 5], "expected": [1, 2]},
                        {"args": [[2, 7, 11], 9], "expected": [0, 1]}]}


def test_posix_import_is_rejected(monkeypatch) -> None:
    """The issue PoC #1: `import posix` must not reach the subprocess."""
    monkeypatch.setenv("TESTING_LOCAL_JUDGE", "1")
    correct, status = LocalPythonJudge().judge(CODE_ITEM, "import posix\nposix.system('true')")
    assert correct is False
    assert status == "unsafe_code_rejected"


def test_eq_forge_cannot_win(monkeypatch) -> None:
    """The issue PoC #2: a forged __eq__ object must not count as correct."""
    monkeypatch.setenv("TESTING_LOCAL_JUDGE", "1")
    forge = "class Forge:\n    def __eq__(self, other):\n        return True\n" \
            "def two_sum(a, b):\n    return Forge()"
    correct, _ = LocalPythonJudge().judge(CODE_ITEM, forge)
    assert correct is False


def test_import_allowlist_blocks_os_and_star_imports(monkeypatch) -> None:
    """Any non-allowlisted import is rejected before execution."""
    monkeypatch.setenv("TESTING_LOCAL_JUDGE", "1")
    judge = LocalPythonJudge()
    for code in ("import os", "from os import path",
                 "import subprocess", "__import__('os')"):
        correct, status = judge.judge(CODE_ITEM, code)
        assert correct is False and status == "unsafe_code_rejected", (code, status)


def test_code_without_judge_service_is_unscored(monkeypatch) -> None:
    """Production path: no CODE_JUDGE_URL -> code is deferred, never run locally."""
    monkeypatch.delenv("CODE_JUDGE_URL", raising=False)
    monkeypatch.delenv("TESTING_LOCAL_JUDGE", raising=False)
    correct, status = judge_answer(CODE_ITEM, "def two_sum(a, b):\n    return []")
    assert correct is None
    assert status == "code_judge_unavailable"
