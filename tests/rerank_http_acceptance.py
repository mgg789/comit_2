"""Real built Fastify→FastAPI HTTP verification on the fresh synthetic acceptance DB."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
from psycopg.conninfo import make_conninfo


def free_port() -> int:
    """Pick a currently free ephemeral loopback port without changing host configuration."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main() -> None:
    """Start only owned short-lived processes and always stop them without touching other services."""
    root = Path(__file__).resolve().parent.parent
    report_path = root / "tests/artifacts/reranker/acceptance-report.json"
    report = json.loads(report_path.read_text())
    matching_port, api_port = free_port(), free_port()
    assert matching_port != api_port
    environment = dict(os.environ)
    environment.update(DATABASE_URL=make_conninfo(os.environ["PIPELINE_TEST_DATABASE_URL"], dbname=report["database"]),
                       PYTHONPATH=str(root / "services/matching-engine/src"),
                       CANDIDATE_PIPELINE_TOKEN="synthetic-local-http-only",
                       MATCHING_ENGINE_URL=f"http://127.0.0.1:{matching_port}", PORT=str(api_port))
    children = []
    try:
        with (root / "tests/artifacts/reranker/http-services.log").open("w") as log:
            children.append(subprocess.Popen([str(root / "services/matching-engine/.venv/bin/python"), "-m", "uvicorn",
                "matching_engine.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(matching_port)],
                cwd=root, env=environment, stdout=log, stderr=log))
            children.append(subprocess.Popen(["node", "apps/api/dist/server.js"], cwd=root, env=environment, stdout=log, stderr=log))
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=10) as client:
                deadline = time.monotonic() + 30
                while True:
                    if any(process.poll() is not None for process in children):
                        raise RuntimeError("owned acceptance service failed to start")
                    try:
                        if client.get("/healthz").status_code == 200 and httpx.get(
                            f"http://127.0.0.1:{matching_port}/healthz", timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("acceptance API readiness timed out")
                    time.sleep(.2)
                headers = {"x-internal-token": environment["CANDIDATE_PIPELINE_TOKEN"]}
                assert client.get("/internal/vacancies/ready").status_code == 401
                ready = client.get("/internal/vacancies/ready", headers=headers)
                ready.raise_for_status()
                vacancy_ids = [item["vacancy_id"] for item in ready.json()["items"]]
                assert len(vacancy_ids) == 3
                for identifier in vacancy_ids:
                    status = client.get(f"/internal/vacancies/{identifier}/rerank", headers=headers)
                    status.raise_for_status()
                    assert status.json()["mode"] == "main_fallback"
                    page = client.get(f"/internal/vacancies/{identifier}/rerank/selections?kind=recommended&limit=2", headers=headers)
                    page.raise_for_status()
                    assert len(page.json()["items"]) == 2
                    assert client.get(f"/internal/vacancies/{identifier}/rerank/selections?kind=reserve", headers=headers).json()["items"] == []
                    request = client.post(f"/internal/vacancies/{identifier}/rerank", json={}, headers=headers)
                    request.raise_for_status()
                    assert request.json()["run_id"] == status.json()["run_id"]
                report["built_fastify_real_http"] = {"state": "passed", "vacancies": len(vacancy_ids),
                    "anonymous": 401, "recommended_and_reserve": True, "idempotent_requeue": True}
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report["built_fastify_real_http"], ensure_ascii=False))
    finally:
        for process in children:
            process.terminate()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
