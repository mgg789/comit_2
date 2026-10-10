"""Real local HTTP profile → Harrier → like → invitation → interview/offer acceptance.

Only a new dedicated database and loopback services are allowed. OTPs are consumed
through the existing encrypted mailbox IPC; SMTP, contacts and credentials are
never printed. Meeting timestamps are real; no model or clock is replaced.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import candidate_profile_acceptance as helpers

API = os.getenv("RECRUITMENT_ACCEPTANCE_API_URL", "http://127.0.0.1:39680")
MATCHING = os.getenv("RECRUITMENT_ACCEPTANCE_MATCHING_URL", "http://127.0.0.1:39681")
AGENTS = os.getenv("RECRUITMENT_ACCEPTANCE_AGENTS_URL", "http://127.0.0.1:39684")
DATABASE = os.getenv("RECRUITMENT_ACCEPTANCE_DATABASE_URL",
                     "postgresql://comit:local-synthetic-only@127.0.0.1:25433/comit_profile_acceptance_recruitment")
PYTHON = os.getenv("RECRUITMENT_ACCEPTANCE_PYTHON", sys.executable)
helpers.API_URL, helpers.MATCHING_URL, helpers.AGENTS_URL = API, MATCHING, AGENTS
helpers.DATABASE_URL, helpers.ORIGIN, helpers.PYTHON = DATABASE, API, PYTHON


def call(client: httpx.Client, method: str, path: str, account: dict | None = None,
         expected: int = 200, **kwargs: object) -> dict:
    """Check status without leaking private response bodies into an assertion."""
    headers = {"Authorization": account["authorization"]} if account else {}
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected, f"{method} {path}: {response.status_code}"
    if expected != 200:
        return {}
    return response.json()


def action(api: httpx.Client, role: str, account: dict, process: dict, name: str, **fields: object) -> dict:
    """Submit a real participant-scoped CAS command with its own retry key."""
    return call(api, "POST", f"/{role}/recruitment/{process['id']}/actions", account,
                json={"action": name, "expected_revision": process["revision"],
                      "idempotency_key": str(uuid4()), **fields})


def main() -> None:
    """Exercise both hiring paths and a private pre-invitation rejection with real services."""
    helpers.assert_isolated()
    assert urlparse(DATABASE).path.startswith("/comit_profile_acceptance_recruitment")
    helpers.migrate()
    with psycopg.connect(DATABASE) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM candidate_pipeline.pool").fetchone()[0] == 0
    checks = []
    with httpx.Client(base_url=API, timeout=30) as api, httpx.Client(
        base_url=MATCHING, headers={"X-Internal-Token": helpers.TOKEN}, timeout=30
    ) as matching:
        tag = uuid4().hex[:8]
        candidates = [helpers.login(api, "candidate", f"recruit-{tag}-{index}@example.test") for index in range(3)]
        employer = helpers.login(api, "employer", f"recruit-{tag}-employer@example.test")
        foreign = helpers.login(api, "employer", f"recruit-{tag}-foreign@example.test")
        for index, candidate in enumerate(candidates):
            helpers.put_profile(api, candidate, helpers.profile(f"Synthetic candidate {index}", "Москва", (150000, 260000)))
            helpers.run_profile_projection()
            helpers.run_matching_worker("worker")
        helpers.await_json(matching, "/internal/pool?limit=10", lambda page: len(page["items"]) == 3)
        checks.append("real_auth_source_projection_harrier_pdf_pool")
        draft = call(api, "POST", "/employer/vacancy-drafts", employer, json={"fields": {
            "title": "Synthetic hiring vacancy", "zp_min": 170000, "zp_max": 270000,
            "daily": "5/2", "city": "Москва", "type": "onsite", "class": "backend",
            "bio": "Python backend", "stacks": {"python": 5},
        }})
        base = f"/employer/vacancy-drafts/{draft['draft_id']}"
        draft = call(api, "POST", base + "/approve", employer, json={"expected_revision": draft["revision"]})
        call(api, "POST", base + "/create", employer, json={"expected_revision": draft["revision"], "summary_hash": draft["summary"]["summary_hash"]})
        environment = {**os.environ, "DATABASE_URL": DATABASE, "AGENTS_SERVICE_TOKEN": helpers.AGENTS_TOKEN,
                       "CANDIDATE_PIPELINE_TOKEN": helpers.TOKEN, "MATCHING_ENGINE_URL": MATCHING,
                       "PYTHONPATH": f"{ROOT / 'services/matching-engine/src'}:{ROOT / 'services/agents-service/src'}"}
        helpers.run_silent([PYTHON, "-c", "from agents_service.store import Store; from agents_service.worker import Worker; from agents_service.providers import MatchingClient; import os; Worker(Store(os.environ['DATABASE_URL']),matching=MatchingClient(os.environ['MATCHING_ENGINE_URL'],os.environ['CANDIDATE_PIPELINE_TOKEN'])).process_once('publication')"], env=environment)
        published = helpers.await_json(api, base, lambda value: value["publication"]["status"] == "accepted",
                                       headers={"Authorization": employer["authorization"]})
        vacancy = published["vacancy_id"]
        helpers.run_matching_worker("vacancy-worker")
        call(api, "POST", base + "/combine", employer, json={"top_k": 3, "idempotency_key": str(uuid4())})
        helpers.run_matching_worker("combine-worker")
        call(matching, "POST", f"/internal/vacancies/{vacancy}/rerank", json={"idempotency_key": str(uuid4())})
        helpers.run_matching_worker("rerank-worker")
        feed = call(api, "POST", base + "/feed", employer, json={"limit": 3})
        assert len(feed["items"]) == 3
        for card in feed["items"]:
            call(api, "POST", base + "/feed/decisions", employer,
                 json={"exposure_id": card["exposure_id"], "action": "like", "idempotency_key": str(uuid4())})
        checks.append("published_vacancy_main_fallback_topk_three_likes")
        processes = []
        for candidate in candidates[:2]:
            payload = {"candidate_id": candidate["id"], "company_name": "Synthetic company",
                       "description": "Backend role and salary", "idempotency_key": str(uuid4())}
            process = call(api, "POST", base + "/invitations", employer, json=payload)
            assert call(api, "POST", base + "/invitations", employer, json=payload) == process
            assert process["chat_available"] is False and process["state"] == "sent"
            call(api, "GET", f"/employer/recruitment/{process['id']}/contacts", employer, expected=403)
            call(api, "GET", f"/employer/recruitment/{process['id']}", foreign, expected=404)
            own = call(api, "GET", f"/candidate/recruitment/{process['id']}", candidate)
            assert "email" not in own and "phone" not in own
            process = action(api, "candidate", candidate, own, "accept")
            contacts = call(api, "GET", f"/employer/recruitment/{process['id']}/contacts", employer)
            assert contacts["candidate_id"] == candidate["id"] and contacts["phone"]
            processes.append(process)
        checks.append("invitations_idempotent_candidate_acceptance_contact_gate_owner_isolation")
        first = action(api, "employer", employer, processes[0], "offer_send", salary_rub=230000,
                       description="Synthetic offer", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
        first = action(api, "candidate", candidates[0], first, "offer_accept", offer_id=first["offers"][-1]["id"])
        assert first["state"] == "hired" and first["allowed_actions"] == [] and not first["interviews"]
        checks.append("immediate_offer_without_interviews_and_terminal_hired")
        second = processes[1]
        for kind in ("technical", "general"):
            starts = datetime.now(UTC) + timedelta(seconds=3)
            second = action(api, "employer", employer, second, "interview_propose", kind=kind,
                            starts_at=starts.isoformat(), time_zone="Europe/Moscow", duration_minutes=30,
                            format="remote", location="https://example.test/meeting")
            meeting = second["interviews"][-1]
            second = action(api, "candidate", candidates[1], second, "interview_accept", interview_id=meeting["id"])
            time.sleep(max(0, (starts - datetime.now(UTC)).total_seconds()) + 0.1)
            second = action(api, "employer", employer, second, "interview_complete", interview_id=meeting["id"],
                            outcome="passed", feedback="Synthetic completed meeting")
        assert "interview_propose" not in second["allowed_actions"]
        assert set(second["allowed_actions"]) == {"offer_send", "reject"}
        second = action(api, "employer", employer, second, "offer_send", salary_rub=250000,
                        description="Offer after two meetings", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
        second = action(api, "candidate", candidates[1], second, "offer_decline",
                        offer_id=second["offers"][-1]["id"], reason="Another opportunity")
        assert second["state"] == "offer_declined" and second["closure"]["actor"] == "candidate"
        checks.append("two_real_scheduled_completed_interviews_limit_and_offer_decline")
        rejected = call(api, "POST", base + "/recruitment/reject", employer,
                        json={"candidate_id": candidates[2]["id"], "idempotency_key": str(uuid4()), "reason": "Other specialization required"})
        assert rejected["state"] == "rejected" and rejected["terms"] is None
        call(api, "GET", f"/candidate/recruitment/{rejected['id']}", candidates[2], expected=404)
        assert call(api, "GET", "/candidate/recruitment", candidates[2])["items"] == []
        assert call(api, "GET", "/candidate/recruitment-events", candidates[2])["items"] == []
        checks.append("preinvitation_rejection_hidden_from_candidate")
        employer_events = call(api, "GET", "/employer/recruitment-events", employer)
        assert employer_events["items"]
        assert all("email" not in event and "phone" not in event for event in employer_events["items"])
        own_events = call(api, "GET", "/candidate/recruitment-events", candidates[1])
        assert all(event["process_id"] == second["id"] for event in own_events["items"])
        helpers.put_profile(api, candidates[0], {}, revision=1, pool=False)
        # Pool consent governs discovery; accepted contacts persist until processing consent is withdrawn.
        assert call(api, "GET", f"/employer/recruitment/{first['id']}/contacts", employer)["profile_revision"] == 2
        revoked = api.patch("/candidate/profile", headers={"Authorization": candidates[0]["authorization"]}, json={
            "expected_revision": 2, "fields": {}, "consent": {"processing": False, "pool": False, "version": "profile-v1"}})
        assert revoked.status_code == 200
        call(api, "GET", f"/employer/recruitment/{first['id']}/contacts", employer, expected=409)
        checks.append("durable_scoped_events_and_live_processing_consent_revocation")
    print(json.dumps({"result": "passed", "checks": checks, "candidate_count": 3,
                      "top_k": 3, "rerank_mode": "main_fallback", "smtp_used": False,
                      "database": urlparse(DATABASE).path.lstrip("/")}, sort_keys=True))


if __name__ == "__main__":
    main()
