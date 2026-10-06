"""Real HTTP showcase → manual like → invitation with local Harrier and no SMTP.

Uses an explicitly dedicated empty database and real source/auth/projection
workers. Only synthetic inputs are used; credentials and contacts are never
included in the printed verification report.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import candidate_profile_acceptance as helpers
import vacancy_feed_acceptance as publication

API = os.getenv("SHOWCASE_ACCEPTANCE_API_URL", "http://127.0.0.1:39790")
MATCHING = os.getenv("SHOWCASE_ACCEPTANCE_MATCHING_URL", "http://127.0.0.1:39791")
AGENTS = os.getenv("SHOWCASE_ACCEPTANCE_AGENTS_URL", "http://127.0.0.1:39794")
DATABASE = os.getenv("SHOWCASE_ACCEPTANCE_DATABASE_URL", "postgres://comit:local-synthetic-only@127.0.0.1:25433/comit_profile_acceptance_showcase")
helpers.API_URL, helpers.MATCHING_URL, helpers.AGENTS_URL = API, MATCHING, AGENTS
helpers.DATABASE_URL, helpers.ORIGIN = DATABASE, API
publication.DATABASE_URL, publication.MATCHING_URL = DATABASE, MATCHING


def call(client, method, path, account=None, expected=200, **kwargs):
    """Assert only HTTP status, keeping tokens and private response payloads silent."""
    response = client.request(method, path, headers={"Authorization": account["authorization"]} if account else {}, **kwargs)
    assert response.status_code == expected, f"{method} {path}: {response.status_code}"
    return response.json() if expected == 200 else {}


def main():
    """Exercise real-model search, exact-revision disclosure and shared recruitment."""
    import psycopg
    for url in (API, MATCHING, AGENTS):
        assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    assert urlparse(DATABASE).hostname in {"127.0.0.1", "localhost"}
    assert urlparse(DATABASE).path.startswith("/comit_profile_acceptance_showcase")
    helpers.migrate()
    with psycopg.connect(DATABASE) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    checks = []
    with httpx.Client(base_url=API, timeout=60) as api:
        tag = uuid4().hex[:8]
        employer = helpers.login(api, "employer", f"showcase-{tag}-employer@example.test")
        foreign = helpers.login(api, "employer", f"showcase-{tag}-foreign@example.test")
        candidates = [helpers.login(api, "candidate", f"showcase-{tag}-{n}@example.test") for n in range(3)]
        fields = [helpers.profile(f"Showcase Person {n}", "Москва", (150000, 260000)) for n in range(3)]
        fields[0]["description"] = "Python разработчик серверных API, PostgreSQL и распределенные системы."
        fields[1]["description"] = "Разработка приложений и интеграций на Python."
        fields[2]["description"] = "Системы обработки данных."
        fields[2].update(city="Казань", primary_direction="frontend", salary={"from_rub": 900000, "to_rub": 1000000})
        for candidate, profile in zip(candidates, fields, strict=True):
            helpers.put_profile(api, candidate, profile)
            helpers.run_profile_projection()
            helpers.run_matching_worker("worker")
        browse = call(api, "POST", "/employer/candidates/search", employer, json={})
        assert browse["mode"] == "browse" and browse["total"] == 3
        call(api, "POST", "/employer/candidates/search", candidates[0], expected=401, json={})
        hybrid = call(api, "POST", "/employer/candidates/search", employer, json={"q": "Python серверные системы", "filters": {"groups": {"stacks": [{"key": "python", "min_level": 5}]}}})
        assert hybrid["mode"] == "hybrid" and hybrid["total"] == 3
        assert any(item["search_factors"]["lexical_rank"] and item["search_factors"]["semantic_rank"] for item in hybrid["items"])
        checks.append("real_harrier_hybrid_search_filters_without_vacancy")
        first_page = call(api, "POST", "/employer/candidates/search", employer, json={"limit": 1})
        second_page = call(api, "POST", "/employer/candidates/search", employer, json={"limit": 1, "cursor": first_page["next_cursor"]})
        assert first_page["items"][0]["candidate_id"] != second_page["items"][0]["candidate_id"]
        candidate = candidates[0]
        detail = call(api, "GET", f"/employer/candidates/{candidate['id']}", employer)
        assert detail["public_profile"]["stacks"][0]["status"] == "self_reported"
        assert fields[0]["display_name"] not in json.dumps(detail)
        assert "email" not in detail["public_profile"] and "phone" not in detail["public_profile"]
        pdf = api.get(detail["resume_url"].removeprefix("/api"), headers={"Authorization": employer["authorization"]})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        text = helpers.pdf_text(pdf.content)
        assert fields[0]["display_name"] not in text and fields[0]["phone"] not in text
        checks.append("pagination_public_profile_and_redacted_pdf")
        draft = call(api, "POST", "/employer/vacancy-drafts", employer, json={"fields": {
            "title": "Showcase backend vacancy", "zp_min": 170000, "zp_max": 270000,
            "daily": "5/2", "city": "Москва", "type": "onsite", "class": "backend"}})
        base = f"/employer/vacancy-drafts/{draft['draft_id']}"
        approved = call(api, "POST", base + "/approve", employer, json={"expected_revision": draft["revision"]})
        call(api, "POST", base + "/create", employer, json={"expected_revision": approved["revision"], "summary_hash": approved["summary"]["summary_hash"]})
        publication.run_publication_worker()
        published = helpers.await_json(api, base, lambda value: value["publication"]["status"] == "accepted", headers={"Authorization": employer["authorization"]})
        helpers.run_matching_worker("vacancy-worker")
        assert call(api, "GET", base + "/ready", employer)["vacancy_id"] == published["vacancy_id"]
        operation = {"candidate_id": candidate["id"], "profile_revision": detail["profile_revision"], "idempotency_key": str(uuid4())}
        call(api, "POST", base + "/showcase-likes", foreign, expected=404, json=operation)
        liked = call(api, "POST", base + "/showcase-likes", employer, json=operation)
        replay = call(api, "POST", base + "/showcase-likes", employer, json=operation)
        assert liked["saved"] and replay["replayed"] and replay["receipt_id"] == liked["receipt_id"]
        saved = call(api, "GET", base + "/saved", employer)
        assert saved["items"][0]["candidate_id"] == candidate["id"] and saved["items"][0]["card"]["mode"] == "manual"
        checks.append("owned_open_vacancy_manual_like_saved_exact_replay")
        override_operation = {"candidate_id": candidates[2]["id"], "profile_revision": 1, "idempotency_key": str(uuid4())}
        warning_response = api.post(base + "/showcase-likes", headers={"Authorization": employer["authorization"]}, json=override_operation)
        assert warning_response.status_code == 409
        warning = warning_response.json()["detail"]
        assert warning["code"] == "strict_filter_confirmation_required"
        assert {"class_mismatch", "salary_disjoint", "relocation_not_ready"} <= set(warning["warnings"])
        assert len(call(api, "GET", base + "/saved", employer)["items"]) == 1
        override_operation["mismatch_token"] = warning["mismatch_token"]
        override = call(api, "POST", base + "/showcase-likes", employer, json=override_operation)
        assert override["saved"] and override["strict_filter_warnings"] == warning["warnings"]
        assert call(api, "POST", base + "/showcase-likes", employer, json=override_operation)["replayed"]
        assert call(api, "POST", base + "/invitations", employer, json={"candidate_id": candidates[2]["id"],
            "company_name": "Synthetic company", "description": "Acknowledged manual choice", "idempotency_key": str(uuid4())})["state"] == "sent"
        checks.append("structured_mismatch_warning_and_explicit_manual_override")
        process = call(api, "POST", base + "/invitations", employer, json={"candidate_id": candidate["id"], "company_name": "Synthetic company", "description": "Backend position", "idempotency_key": str(uuid4())})
        call(api, "GET", f"/employer/recruitment/{process['id']}/contacts", employer, expected=403)
        own = call(api, "GET", f"/candidate/recruitment/{process['id']}", candidate)
        accepted = call(api, "POST", f"/candidate/recruitment/{process['id']}/actions", candidate, json={"action": "accept", "expected_revision": own["revision"], "idempotency_key": str(uuid4())})
        assert accepted["state"] == "accepted"
        assert call(api, "GET", f"/employer/recruitment/{process['id']}/contacts", employer)["candidate_id"] == candidate["id"]
        checks.append("same_invitation_acceptance_and_contact_gate")
        helpers.put_profile(api, candidates[1], fields[1], revision=1, pool=False)
        # No projection delivery: source watermark must already deny stale discovery.
        call(api, "GET", f"/employer/candidates/{candidates[1]['id']}", employer, expected=409)
        helpers.run_profile_projection()
        helpers.run_matching_worker("worker")
        call(api, "POST", "/employer/candidates/search", employer, expected=409, json={"limit": 1, "cursor": first_page["next_cursor"]})
        assert call(api, "POST", "/employer/candidates/search", employer, json={})["total"] == 2
        checks.append("immediate_source_withdrawal_and_stale_cursor_rejection")
    print(json.dumps({"result": "passed", "candidate_count": 3, "checks": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
