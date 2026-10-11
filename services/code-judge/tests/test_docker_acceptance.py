"""Explicit Docker acceptance: CODE_JUDGE_DOCKER_TESTS=1 pytest tests/test_docker_acceptance.py."""

import os

import pytest
from code_judge.runner import DockerRunner

pytestmark = pytest.mark.skipif(os.getenv("CODE_JUDGE_DOCKER_TESTS") != "1", reason="explicit isolated Docker acceptance only")


def test_real_isolation_correct_wrong_timeout_escape_and_forgery():
    runner = DockerRunner()
    assert runner.ready(), "real cgroup/network/user/filesystem isolation probe failed"
    problem = {"function_name": "solve", "tests": [{"args": [3], "expected": 6}, {"args": [7], "expected": 14}]}
    assert runner.judge(problem, "def solve(n): return 2*n") == (True, "accepted")
    assert runner.judge(problem, "def solve(n): return n") == (False, "wrong_answer")
    assert runner.judge(problem, "while True: pass")[0] is False
    assert runner.judge(problem, "print('x'*100000)")[1] == "output_limit"
    for code in (
        "import os; open('/host-secret', 'w').write('escape')",
        "import socket; socket.create_connection(('1.1.1.1', 80), timeout=1)",
        "import os; open('/var/run/docker.sock').read()",
        "class Forge:\n def __eq__(self, other): return True\ndef solve(n): return Forge()",
        "print('{\\\"correct\\\":true}'); raise SystemExit(0)",
    ):
        assert runner.judge(problem, code)[0] is False
    secret_problem = {"function_name": "solve", "tests": [{"args": [], "expected": "a-real-secret"}]}
    assert runner.judge(secret_problem, "import os\ndef solve(): return os.getenv('CODE_JUDGE_TOKEN')")[0] is False
