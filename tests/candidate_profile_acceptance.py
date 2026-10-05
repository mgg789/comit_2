"""Durable profile-to-selection acceptance on the isolated local HTTP stack.

The test uses real auth, the encrypted ``AuthStore.claimMail`` mailbox, the
TypeScript projection worker, Harrier 640D candidate/vacancy workers and the
public employer gateway.  It intentionally never prints OTPs, bearer tokens,
email addresses, phone numbers, provider payloads or database credentials.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pypdf import PdfReader

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DB_DEFAULT = "postgresql://comit:local-synthetic-only@127.0.0.1:25433/comit_profile_acceptance"
API_URL = os.getenv("PROFILE_ACCEPTANCE_API_URL", "http://127.0.0.1:39580").rstrip("/")
MATCHING_URL = os.getenv("PROFILE_ACCEPTANCE_MATCHING_URL", "http://127.0.0.1:39581").rstrip("/")
AGENTS_URL = os.getenv("PROFILE_ACCEPTANCE_AGENTS_URL", "http://127.0.0.1:39584").rstrip("/")
DATABASE_URL = os.getenv("PROFILE_ACCEPTANCE_DATABASE_URL", DB_DEFAULT)
TOKEN = os.environ.get("CANDIDATE_PIPELINE_TOKEN", "synthetic-profile-pipeline-token")
AGENTS_TOKEN = os.environ.get("AGENTS_SERVICE_TOKEN", "synthetic-profile-agents-token")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "synthetic-profile-auth-secret-32-bytes-long")
ORIGIN = os.getenv("PROFILE_ACCEPTANCE_ORIGIN", "http://127.0.0.1:39580")
PYTHON = os.getenv("PROFILE_ACCEPTANCE_PYTHON", sys.executable)


def assert_isolated() -> None:
    """Refuse accidental non-local or non-dedicated service targets."""
    for value in (API_URL, MATCHING_URL, AGENTS_URL):
        parsed = urlparse(value)
        assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        assert parsed.username is None and parsed.password is None
    parsed = urlparse(DATABASE_URL)
    assert parsed.scheme.startswith("postgres") and parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == 25433 and re.fullmatch(r"/comit_profile_acceptance(?:_[a-z0-9]+)*", parsed.path)
    assert "acceptance" in parsed.path


def run_silent(command: list[str], *, env: dict[str, str]) -> None:
    """Run a real worker/migration without allowing secrets into captured output."""
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=90, check=False)
    assert result.returncode == 0, "bounded local worker failed"


def migrate() -> None:
    """Apply all three explicit migration sets to the named acceptance database."""
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "AUTH_SECRET": AUTH_SECRET}
    run_silent(["node", str(ROOT / "apps/api/dist/auth/migrate.js")], env=env)
    env_python = {**env, "PYTHONPATH": f"{ROOT / 'services/matching-engine/src'}:{ROOT / 'services/agents-service/src'}"}
    code = "from matching_engine.database import migrate as m; from agents_service.database import migrate as a; m(__import__('os').environ['DATABASE_URL']); a(__import__('os').environ['DATABASE_URL'])"
    run_silent([PYTHON, "-c", code], env=env_python)


def claim_code(email: str, challenge_id: str) -> str:
    """Consume one encrypted AuthStore mail job over a private descriptor IPC pipe."""
    read_fd, write_fd = os.pipe()
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "AUTH_SECRET": AUTH_SECRET,
           "PROFILE_MAILBOX_FD": str(write_fd)}
    try:
        child = subprocess.Popen(["node", str(BASE / "profile_auth_mailbox.mjs")], cwd=ROOT,
                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, pass_fds=(write_fd,))
        os.close(write_fd)
        assert child.stdin is not None
        child.stdin.write(json.dumps({"email": email, "challenge_id": challenge_id}).encode())
        child.stdin.close()
        payload = os.read(read_fd, 20000)
        assert child.wait(timeout=20) == 0
        job = json.loads(payload.decode())
        assert job["email"] == email and job["challenge_id"] == challenge_id
        assert re.fullmatch(r"[0-9]{6}", job["code"])
        return job["code"]
    finally:
        os.close(read_fd)


def login(client: httpx.Client, role: str, email: str) -> dict:
    """Authenticate one synthetic role through HTTP and the real encrypted mailbox."""
    code = client.post(f"/auth/{role}/code", json={"email": email}, headers={"Origin": ORIGIN})
    assert code.status_code == 202, "auth challenge was not accepted"
    receipt = code.json()
    otp = claim_code(email, receipt["challenge_id"])
    verified = client.post(f"/auth/{role}/verify", json={
        "email": email, "challenge_id": receipt["challenge_id"], "code": otp,
        "consent": {"processing": True, "version": "account-v1"},
    }, headers={"Origin": ORIGIN})
    assert verified.status_code == 200, "synthetic auth verification failed"
    value = verified.json()
    assert value["account"]["role"] == role
    return {"id": value["account"]["id"], "authorization": f"Bearer {value['access_token']}"}


def profile(name: str, city: str, salary: tuple[int, int], direction: str = "backend",
            relocation: bool = False) -> dict:
    """Build a declaration-only profile; proof and approval fields are absent by design."""
    return {
        "display_name": name, "phone": "+79990000001", "city": city,
        "work_types": ["onsite"], "relocation_ready": relocation,
        "relocation_cities": ["Москва"] if relocation else [],
        "salary": {"from_rub": salary[0], "to_rub": salary[1]},
        "primary_direction": direction, "declared_grade": "middle",
        "domains": [{"key": "rest_api", "level": 4}],
        "stacks": [{"key": "python", "level": 5}, {"key": "postgres", "level": 4}],
        "soft_skills": [{"key": "teamwork", "level": 4}],
        "description": "Самооценка: Python backend и PostgreSQL.",
        "projects": [{"title": "Declared backend project", "description": "Самооценка проекта.",
                       "facts": {"domains": ["rest_api"], "stacks": ["python"], "duration_months": 12,
                                  "role": "developer", "production_support": True}}],
        "experience": [{"title": "Declared experience", "description": "Самооценка опыта."}],
        "achievements": [], "preferences": ["Командная работа"],
    }


def put_profile(client: httpx.Client, account: dict, fields: dict, revision: int = 0,
                pool: bool = True) -> dict:
    """Persist one candidate source snapshot through the authenticated API."""
    response = client.patch("/candidate/profile", headers={"Authorization": account["authorization"]}, json={
        "expected_revision": revision, "fields": fields,
        "consent": {"processing": True, "pool": pool, "version": "profile-v1"},
    })
    assert response.status_code == 200, "candidate profile mutation failed"
    return response.json()


def run_profile_projection() -> None:
    """Deliver exactly one API source outbox snapshot to real HTTP matching."""
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "AUTH_SECRET": AUTH_SECRET,
           "CANDIDATE_PIPELINE_TOKEN": TOKEN, "MATCHING_ENGINE_URL": MATCHING_URL}
    run_silent(["node", str(ROOT / "apps/api/dist/profiles/worker.js"), "--once"], env=env)


def run_matching_worker(mode: str) -> None:
    """Run one real local Harrier/vacancy/combine worker pass against PostgreSQL."""
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "CANDIDATE_PIPELINE_TOKEN": TOKEN,
           "PYTHONPATH": f"{ROOT / 'services/matching-engine/src'}:{ROOT / 'services/agents-service/src'}"}
    run_silent([PYTHON, "-m", "matching_engine", mode, "--once"], env=env)


def await_json(client: httpx.Client, path: str, predicate, timeout: float = 180,
               headers: dict[str, str] | None = None) -> dict:
    """Poll a safe state transition without exposing response bodies on failure."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path, headers=headers)
        if response.status_code == 200:
            value = response.json()
            if predicate(value):
                return value
        time.sleep(0.3)
    raise AssertionError("bounded local state transition timed out")


def pdf_text(content: bytes) -> str:
    """Extract PDF text only for redaction and declaration retention assertions."""
    assert content.startswith(b"%PDF-")
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)


def main() -> None:
    """Exercise auth, source ownership, all durable projections and consent lifecycle."""
    assert_isolated()
    migrate()
    env_python = {**os.environ, "DATABASE_URL": DATABASE_URL,
                  "PYTHONPATH": f"{ROOT / 'services/matching-engine/src'}:{ROOT / 'services/agents-service/src'}"}
    # Freshness is checked through the state owners; no destructive reset is allowed.
    import psycopg
    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM candidate_pipeline.pool").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agents_service.drafts").fetchone()[0] == 0

    checks: list[str] = []
    with httpx.Client(base_url=API_URL, timeout=20, follow_redirects=False) as api, \
            httpx.Client(base_url=MATCHING_URL, headers={"X-Internal-Token": TOKEN}, timeout=20) as matching:
        run_tag = uuid4().hex[:10]
        accounts = [login(api, "candidate", f"profile-acceptance-{run_tag}-{index}@example.test") for index in range(5)]
        employer = login(api, "employer", f"profile-acceptance-{run_tag}-employer@example.test")
        foreign_employer = login(api, "employer", f"profile-acceptance-{run_tag}-foreign@example.test")
        checks.append("real_http_auth_and_encrypted_mailbox")

        forged = api.patch("/candidate/profile", headers={"Authorization": accounts[0]["authorization"]}, json={
            "expected_revision": 0, "fields": {"grade_result": {"grade": "senior"}, "evidence": {"status": "confirmed"}}})
        assert forged.status_code == 400
        assert api.patch("/candidate/profile", headers={"Authorization": employer["authorization"]}, json={
            "expected_revision": 0, "fields": {}}).status_code == 401
        assert api.patch("/candidate/profile", headers={"Authorization": accounts[0]["authorization"], "Origin": "https://foreign.example"}, json={
            "expected_revision": 0, "fields": {}}).status_code == 403
        checks.append("forged_proofs_400_foreign_role_and_origin")

        fields = [
            profile("Candidate One", "Москва", (150_000, 200_000)),
            profile("Candidate Two", "Москва", (160_000, 220_000)),
            profile("Candidate Front", "Москва", (150_000, 230_000), "frontend"),
            profile("Candidate Salary", "Москва", (300_000, 400_000)),
            profile("Candidate City", "Санкт-Петербург", (150_000, 220_000)),
        ]
        saved = [put_profile(api, account, item) for account, item in zip(accounts, fields)]
        assert all(item["consents"]["pool"] for item in saved)
        for item in saved:
            run_profile_projection()
        for account in accounts:
            await_json(api, "/candidate/profile", lambda value: value["projection"]["state"] == "delivered",
                       headers={"Authorization": account["authorization"]})
        for _ in accounts:
            run_matching_worker("worker")
        pool = await_json(matching, "/internal/pool?limit=20", lambda value: len(value["items"]) == 5)
        assert all(item["category"]["grade_status"] == "self_reported" for item in pool["items"])
        checks.append("real_profile_worker_harrier640_pdf_and_self_reported_projection")

        # Source projection keeps self-reported skill/narrative claims and no trusted proof.
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute("SELECT profile FROM candidate_pipeline.candidates ORDER BY candidate_id LIMIT 1").fetchone()[0]
            assert row["grade_result"]["status"] == "self_reported"
            assert all(item["status"] == "self_reported" for group in ("domains", "stacks", "soft_skills") for item in row[group])
            assert all(item["status"] == "self_reported" for group in ("projects", "experience", "achievements") for item in row[group])

        employer_headers = {"Authorization": employer["authorization"]}
        draft_response = api.post("/employer/vacancy-drafts", headers=employer_headers, json={"fields": {
            "title": "Python backend profile acceptance", "zp_min": 180_000, "zp_max": 250_000,
            "daily": "5/2", "city": "Москва", "type": "onsite", "class": "backend",
            "bio": "Python backend и PostgreSQL", "stacks": {"python": 5}}})
        assert draft_response.status_code == 200
        draft = draft_response.json()
        draft_path = f"/employer/vacancy-drafts/{draft['draft_id']}"
        assert api.get(draft_path, headers={"Authorization": foreign_employer["authorization"]}).status_code == 404
        approved = api.post(draft_path + "/approve", headers=employer_headers, json={"expected_revision": draft["revision"]})
        assert approved.status_code == 200 and approved.json()["state"] == "summary"
        summary = approved.json()
        created = api.post(draft_path + "/create", headers=employer_headers, json={
            "expected_revision": summary["revision"], "summary_hash": summary["summary"]["summary_hash"]})
        assert created.status_code == 200
        publication = created.json()
        # This is the real agents worker; MatchingClient performs the HTTP intake.
        agents_env = {**env_python, "AGENTS_SERVICE_TOKEN": AGENTS_TOKEN,
                      "MATCHING_ENGINE_URL": MATCHING_URL}
        code = ("from agents_service.store import Store; from agents_service.worker import Worker; "
                "from agents_service.providers import MatchingClient; import os; "
                "Worker(Store(os.environ['DATABASE_URL']), matching=MatchingClient(os.environ['MATCHING_ENGINE_URL'], os.environ['CANDIDATE_PIPELINE_TOKEN'])).process_once('publication')")
        run_silent([PYTHON, "-c", code], env={**agents_env, "CANDIDATE_PIPELINE_TOKEN": TOKEN})
        await_json(api, draft_path, lambda value: value["state"] == "created" and value["publication"]["status"] == "accepted",
                   headers=employer_headers)
        run_matching_worker("vacancy-worker")
        await_json(matching, f"/internal/vacancies/{publication['vacancy_id']}/ready", lambda value: value["status"] == "open")
        combine = api.post(draft_path + "/combine", headers=employer_headers, json={"top_k": 2})
        assert combine.status_code == 200
        run_matching_worker("combine-worker")
        selections = await_json(api, draft_path + "/selections?limit=2", lambda value: value["run"]["state"] == "ready" and len(value["items"]) == 2,
                                headers=employer_headers)
        selected = selections["items"]
        assert selections["run"]["scanned_count"] == 5 and selections["run"]["passed_count"] == 2
        assert all(item["category"]["specialization"] == "backend" for item in selected)
        assert all(item["explanation"]["grade_status"] == "self_reported" and item["explanation"]["grade_confidence"] == 0.6 for item in selected)
        selected_ids = {item["candidate_id"] for item in selected}
        assert accounts[0]["id"] in selected_ids and accounts[1]["id"] in selected_ids
        checks.append("manual_vacancy_approve_create_agents_http_matching_vacancy_combine_top2")

        selected_pdf = api.get(draft_path + f"/candidates/{accounts[0]['id']}/resume.pdf", headers=employer_headers)
        assert selected_pdf.status_code == 200
        text = pdf_text(selected_pdf.content)
        assert "profile-acceptance" not in text and "+79990000001" not in re.sub(r"\D", "", text)
        assert "заявлено самостоятельно" in text and "Declared backend project" in text
        assert api.get(draft_path + f"/candidates/{accounts[2]['id']}/resume.pdf", headers=employer_headers).status_code == 404
        checks.append("public_pdf_redacted_selected_only_grade_and_narrative_retained")

        # Revoke source pool consent and prove the public gateway blocks before projection.
        revoked = put_profile(api, accounts[0], fields[0], revision=1, pool=False)
        assert revoked["revision"] == 2
        assert api.get(draft_path + "/selections", headers=employer_headers).status_code == 409
        assert api.get(draft_path + f"/candidates/{accounts[0]['id']}/resume.pdf", headers=employer_headers).status_code == 409
        assert api.get("/candidate/resume.pdf", headers={"Authorization": accounts[0]["authorization"]}).status_code == 409
        run_profile_projection(); run_matching_worker("worker"); run_matching_worker("combine-worker")
        rerun = api.post(draft_path + "/combine", headers=employer_headers, json={"top_k": 2})
        assert rerun.status_code == 200
        run_matching_worker("combine-worker")
        remaining = await_json(api, draft_path + "/selections?limit=2", lambda value: value["run"]["state"] == "ready" and len(value["items"]) == 1,
                               headers=employer_headers)
        assert remaining["run"]["scanned_count"] == 4 and remaining["run"]["passed_count"] == 1
        assert accounts[1]["id"] in {item["candidate_id"] for item in remaining["items"]}
        checks.append("source_consent_revoke_blocks_before_projection_event_and_rerun_remaining1")

        regranted = put_profile(api, accounts[0], fields[0], revision=2, pool=True)
        assert regranted["revision"] == 3
        run_profile_projection(); run_matching_worker("worker"); run_matching_worker("combine-worker")
        again = api.post(draft_path + "/combine", headers=employer_headers, json={"top_k": 2})
        assert again.status_code == 200
        run_matching_worker("combine-worker")
        final = await_json(api, draft_path + "/selections?limit=2", lambda value: value["run"]["state"] == "ready" and len(value["items"]) == 2,
                           headers=employer_headers)
        assert final["run"]["scanned_count"] == 5 and final["run"]["passed_count"] == 2
        assert {item["candidate_id"] for item in final["items"]} == {accounts[0]["id"], accounts[1]["id"]}
        checks.append("regrant_rebuild_returns2")
        delivery = matching.get("/internal/matching/events")
        assert delivery.status_code == 200
        events = delivery.json()
        assert all(events[key] == 0 for key in ("candidate_pending", "vacancy_pending", "queued_count", "quarantined_count", "retrying_count"))
        assert events["acknowledged_count"] >= 8
        checks.append("durable_events_acknowledged_zero_pending_or_failed")

    report = {"result": "passed", "checks": checks, "candidate_count": 5, "selected_initial": 2,
              "selected_after_revoke": 1, "selected_after_regrant": 2,
              "database": urlparse(DATABASE_URL).path.lstrip("/"),
              "ports": {"api": urlparse(API_URL).port, "matching": urlparse(MATCHING_URL).port, "agents": urlparse(AGENTS_URL).port},
              "events": events,
              "startup": [f"PORT={urlparse(MATCHING_URL).port} python -m matching_engine serve",
                          f"PORT={urlparse(AGENTS_URL).port} python -m agents_service serve",
                          f"PORT={urlparse(API_URL).port} node apps/api/dist/server.js"],
              "migrations": ["node apps/api/dist/auth/migrate.js", "matching_engine.database.migrate", "agents_service.database.migrate"],
              "sanitized": True}
    artifact = BASE / "artifacts/candidate-profile-acceptance.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
