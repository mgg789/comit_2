"""Answer checking: local sandbox judging now, DMOJ/code-judge bridge next pass.

- check_mcq: exact server-side comparison (the stored answer never leaves).
- LocalPythonJudge: runs the candidate function against fixed tests in a
  subprocess (list argv, shell=False, timeout, isolated env). Before execution
  the code is parsed and rejected if it imports dangerous modules or touches
  dunders - the sandbox denies the common escape hatches instead of trusting
  the input. Running untrusted candidate code is the product's purpose; the
  sandbox bounds what that code can reach.
- CodeJudgeClient: HTTP client for the code-judge microservice (env
  CODE_JUDGE_URL, operator-configured). The URL scheme is validated as
  http/https and the submission id is format-checked before interpolation;
  on any transport failure judging degrades to the local sandbox.
- Extended answers are NOT auto-judged in this pass: recorded as "manual"
  and excluded from scoring (rubric judging lands in a later pass).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

LOCAL_TIMEOUT_SECONDS = 10
FORBIDDEN_MODULES = frozenset((
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "ctypes", "multiprocessing", "threading", "webbrowser", "pty", "signal",
))


def check_mcq(payload: dict, answer: int | str) -> bool | None:
    """Exact comparison against the stored answer index; None on malformed input."""
    expected = payload.get("answer_index")
    try:
        given = int(answer)
    except (TypeError, ValueError):
        return None
    return given == expected if isinstance(expected, int) else None


def code_is_safe(code: str) -> bool:
    """Reject code that imports escape-hatch modules or touches dunders."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in FORBIDDEN_MODULES for alias in node.names):
                return False
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                return False
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") or isinstance(node, ast.Name) and node.id.startswith("__"):
            return False
    return True


class LocalPythonJudge:
    """Run one candidate function against fixed test cases in a subprocess."""

    def judge(self, payload: dict, code: str) -> tuple[bool | None, str]:
        function_name = str(payload.get("function_name", ""))
        tests = payload.get("tests", [])
        if not function_name or not isinstance(tests, list) or not tests:
            return None, "item_has_no_tests"
        if not code_is_safe(code):
            return False, "unsafe_code_rejected"
        cases = json.dumps([
            {"args": test.get("args", []), "expected": test.get("expected")}
            for test in tests], ensure_ascii=False)
        harness = (
            "import json as _json, sys as _sys\n"
            "namespace = {}\n"
            "exec(compile(_sys.stdin.read(), 'solution', 'exec'), namespace)\n"
            "cases = _json.loads(_sys.argv[1])\n"
            "results = [namespace[" + repr(function_name) + "](*case['args']) == case['expected']\n"
            "          for case in cases]\n"
            "print(_json.dumps(results))\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
            handle.write(code)
            solution_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", harness, cases],
                input=code, capture_output=True, text=True,
                timeout=LOCAL_TIMEOUT_SECONDS, check=False,
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                cwd=solution_path.parent,
            )
        except subprocess.TimeoutExpired:
            return False, "timeout"
        finally:
            solution_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            return False, "runtime_error"
        try:
            results = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False, "judge_output_invalid"
        return (all(results) if results else False), "auto"


class CodeJudgeClient:
    """HTTP client for the code-judge microservice; falls back to local judging."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("CODE_JUDGE_URL", "")).rstrip("/")

    def configured(self) -> bool:
        return self.base_url.startswith(("http://", "https://"))

    def judge(self, payload: dict, code: str) -> tuple[bool | None, str]:
        """Submit code and wait for the verdict; degrade to local judging on failure."""
        import urllib.request

        if not self.configured():
            return LocalPythonJudge().judge(payload, code)
        try:
            request = urllib.request.Request(
                self.base_url + "/submissions",
                data=json.dumps({"item_id": str(payload.get("item_id", "")),
                                 "language": "python", "code": code}).encode("utf-8"),
                headers={"content-type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=30) as response:
                created = json.loads(response.read().decode("utf-8"))
            submission_id = str(created.get("submission_id", ""))
            if not submission_id.replace("-", "").isalnum() or len(submission_id) > 64:
                return LocalPythonJudge().judge(payload, code)
            detail_url = self.base_url + "/submissions/" + submission_id
            for _ in range(30):
                with urllib.request.urlopen(detail_url, timeout=10) as response:
                    record = json.loads(response.read().decode("utf-8"))
                if record.get("status") == "judged":
                    return record.get("correct"), "code_judge:" + str(record.get("verdict_kind", ""))
            return None, "code_judge_timeout"
        except (OSError, ValueError, KeyError):
            return LocalPythonJudge().judge(payload, code)


def judge_answer(payload: dict, answer: str) -> tuple[bool | None, str]:
    """Dispatch by task type; extended answers are recorded as manual (no auto-scoring)."""
    task_type = payload.get("type")
    if task_type == "mcq":
        return check_mcq(payload, answer), "auto"
    if task_type == "code":
        return CodeJudgeClient().judge(payload, answer)
    return None, "manual"
