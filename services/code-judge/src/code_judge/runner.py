"""Disposable Docker execution; only JSON output crosses the sandbox boundary."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
import uuid

# Expected outputs are NEVER sent into the candidate process. The container is
# disposable even between cases, and contains no broker files or credentials.
HARNESS = """import json, sys, os
os.dup2(1, 2)
sys.stdout.write('COMIT_SANDBOX_READY\\n')
sys.stdout.flush()
request = json.loads(sys.stdin.read())
code, args, name = request['code'], request['args'], request['function_name']
del request
namespace = {}
exec(compile(code, '<candidate>', 'exec'), namespace)
value = namespace[name](*args)
sys.stdout.write(json.dumps(value, allow_nan=False, ensure_ascii=False))
"""
OUTPUT_LIMIT = 65536
READY_MARKER = b"COMIT_SANDBOX_READY\n"
PROBE = '''def probe():
    import os, pathlib, socket, fcntl, struct
    root = pathlib.Path('/sys/fs/cgroup')
    memory = (root / 'memory.max').read_text().strip()
    pids = (root / 'pids.max').read_text().strip()
    cpu = (root / 'cpu.max').read_text().split()
    status = pathlib.Path('/proc/self/status').read_text()
    # Some kernels create inactive tunnel templates even with --network=none.
    # They must not be UP; require no usable non-loopback interface or IPv4 route.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        isolated = all(name == 'lo' or not (struct.unpack('H', fcntl.ioctl(
            sock.fileno(), 0x8913, struct.pack('256s', name.encode()[:15]))[16:18])[0] & 1)
            for _, name in socket.if_nameindex())
    isolated = isolated and len(pathlib.Path('/proc/net/route').read_text().splitlines()) == 1
    readonly = False
    try:
        open('/sandbox-write-probe', 'w').close()
    except OSError as error:
        readonly = error.errno == 30
    return (memory.isdigit() and int(memory) <= 134217728
        and pids.isdigit() and int(pids) <= 16
        and cpu[0].isdigit() and int(cpu[0]) / int(cpu[1]) <= 0.5
        and os.getuid() == 65534 and readonly
        and 'NoNewPrivs:\\t1' in status and 'CapEff:\\t0000000000000000' in status
        and isolated)
'''


class RunnerUnavailable(Exception):
    """Infrastructure failure, never a candidate's incorrect answer."""


def _invalid_constant(value: str) -> None:
    raise ValueError("non_finite_json")


class DockerRunner:
    """Run Python with a fixed command in a resource-limited, unprivileged container."""

    def __init__(self) -> None:
        self.image = os.getenv("CODE_JUDGE_IMAGE", "python:3.12-slim")
        self.timeout = 5.0
        self.startup_timeout = 10.0

    def cleanup_stale(self) -> None:
        """On exclusive broker startup, remove only this broker's labelled containers."""
        try:
            found = subprocess.run(["docker", "ps", "-aq", "--filter", "label=comit.code-judge=managed"],
                capture_output=True, timeout=5, check=True)
            identifiers = found.stdout.decode().split()
            if len(identifiers) > 16 or any(not value.isalnum() or len(value) > 64 for value in identifiers):
                raise RunnerUnavailable("stale_cleanup_requires_operator")
            if identifiers:
                subprocess.run(["docker", "rm", "--force", *identifiers],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise RunnerUnavailable("stale_cleanup_failed") from error

    def ready(self) -> bool:
        """Probe actual execution with the same isolation flags as submissions."""
        try:
            correct, _ = self.judge({"function_name": "probe", "tests": [
                {"args": [], "expected": True}]}, PROBE)
            return correct
        except RunnerUnavailable:
            return False

    def command(self, name: str) -> list[str]:
        """Fixed sandbox configuration; candidate text never becomes CLI arguments."""
        # Cleanup has a single owner (finally), avoiding Docker --rm races.
        return ["docker", "run", "--pull=never", "--name", name, "--label=comit.code-judge=managed",
                "--network=none", "--read-only", "--user=65534:65534",
                "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                "--memory=128m", "--memory-swap=128m", "--cpus=0.5",
                "--pids-limit=16", "--ulimit=nofile=64:64", "--ulimit=core=0", "--ulimit=cpu=3:3",
                "--log-driver=none", "--interactive", self.image,
                "python", "-I", "-B", "-c", HARNESS]

    def _case(self, code: str, name: str, args: list, execution_budget: list[float] | None = None, total_deadline: float | None = None) -> tuple[object, str]:
        container = "comit-judge-" + uuid.uuid4().hex
        process = None
        selector = selectors.DefaultSelector()
        output = bytearray()
        execution_started = None
        try:
            process = subprocess.Popen(self.command(container), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert process.stdin and process.stdout
            source = json.dumps({"code": code, "function_name": name, "args": args}).encode()
            written = 0
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            started = False
            deadline = min(time.monotonic() + self.startup_timeout, total_deadline or float("inf"))
            while selector.get_map():
                if time.monotonic() >= deadline:
                    if not started:
                        raise RunnerUnavailable("container_start_timeout")
                    if total_deadline is not None and deadline == total_deadline:
                        raise RunnerUnavailable("submission_infrastructure_timeout")
                    return None, "timeout"
                for key, _ in selector.select(timeout=0.05):
                    if key.fileobj is process.stdin:
                        try:
                            written += os.write(process.stdin.fileno(), source[written:written + 4096])
                        except BrokenPipeError:
                            written = len(source)
                        if written >= len(source):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if not started and len(output) >= len(READY_MARKER):
                        if not output.startswith(READY_MARKER):
                            raise RunnerUnavailable("container_start_failed")
                        del output[:len(READY_MARKER)]
                        started = True
                        execution_started = time.monotonic()
                        available = min(self.timeout, execution_budget[0]) if execution_budget is not None else self.timeout
                        deadline = min(execution_started + available, total_deadline or float("inf"))
                    if len(output) > OUTPUT_LIMIT:
                        return None, "output_limit"
            result = process.wait(timeout=1)
            if not started:
                raise RunnerUnavailable("container_start_failed")
            if result:
                return None, "runtime_error"
            try:
                return json.loads(output, parse_constant=_invalid_constant), "ok"
            except (ValueError, UnicodeError, RecursionError):
                return None, "invalid_output"
        except (OSError, subprocess.SubprocessError) as error:
            raise RunnerUnavailable("container_unavailable") from error
        finally:
            if execution_budget is not None and execution_started is not None:
                execution_budget[0] -= max(0, time.monotonic() - execution_started)
            selector.close()
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            # Docker CLI termination does not terminate a running container.
            try:
                removed = subprocess.run(["docker", "rm", "--force", container],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               timeout=5, check=False)
                if removed.returncode and b"No such container" not in removed.stderr:
                    # --rm may be deleting the same container concurrently.
                    # Accept the race only after Docker confirms its absence.
                    absent = subprocess.run(["docker", "inspect", container],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5, check=False)
                    if absent.returncode != 1 or b"No such object" not in absent.stderr:
                        raise RunnerUnavailable("container_cleanup_failed")
            except (OSError, subprocess.SubprocessError) as error:
                raise RunnerUnavailable("container_cleanup_failed") from error

    def judge(self, payload: dict, code: str) -> tuple[bool, str]:
        """Compare strict JSON outside the sandbox, never candidate-defined equality."""
        # Candidate runtime is graded; slow container setup/cleanup is infrastructure.
        deadline = time.monotonic() + 45
        execution_budget = [25.0]
        for case in payload["tests"]:
            if execution_budget[0] <= 0:
                return False, "timeout"
            if time.monotonic() >= deadline:
                raise RunnerUnavailable("submission_infrastructure_timeout")
            value, status = self._case(code, payload["function_name"], case["args"], execution_budget, deadline)
            if status != "ok":
                return False, status
            if json.dumps(value, sort_keys=True, ensure_ascii=False) != json.dumps(
                    case["expected"], sort_keys=True, ensure_ascii=False):
                return False, "wrong_answer"
        return True, "accepted"
