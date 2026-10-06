"""Real HTTP acceptance for the employer vacancy feed and preference history.

The scenario deliberately uses the same encrypted mailbox, source-profile
projection worker, Harrier candidate/vacancy workers and PostgreSQL database as
``candidate_profile_acceptance``.  It does not inject a provider, print OTPs or
contact data, or use SMTP.  The feed is exercised through both the internal
matching contract and the owner-scoped employer gateway.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DB_DEFAULT = "postgresql://comit:local-synthetic-only@127.0.0.1:25433/comit_profile_acceptance_feed"
API_URL = os.getenv("FEED_ACCEPTANCE_API_URL", "http://127.0.0.1:39580").rstrip("/")
MATCHING_URL = os.getenv("FEED_ACCEPTANCE_MATCHING_URL", "http://127.0.0.1:39581").rstrip("/")
AGENTS_URL = os.getenv("FEED_ACCEPTANCE_AGENTS_URL", "http://127.0.0.1:39584").rstrip("/")
DATABASE_URL = os.getenv("FEED_ACCEPTANCE_DATABASE_URL", DB_DEFAULT)
TOKEN = os.environ.get("CANDIDATE_PIPELINE_TOKEN", "synthetic-profile-pipeline-token")
AGENTS_TOKEN = os.environ.get("AGENTS_SERVICE_TOKEN", "synthetic-profile-agents-token")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "synthetic-profile-auth-secret-32-bytes-long")
ORIGIN = os.getenv("FEED_ACCEPTANCE_ORIGIN", "http://127.0.0.1:39580")
PYTHON = os.getenv(
    "FEED_ACCEPTANCE_PYTHON",
    "/Users/mike/Projects/comit_2/services/matching-engine/.venv/bin/python",
)

# The profile acceptance module is the source of truth for auth, source fields,
# mailbox IPC, projection and worker invocation.  Override its target constants
# so this harness retains the dedicated feed database and feed-specific ports.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
import candidate_profile_acceptance as profile_acceptance

profile_acceptance.API_URL = API_URL
profile_acceptance.MATCHING_URL = MATCHING_URL
profile_acceptance.AGENTS_URL = AGENTS_URL
profile_acceptance.DATABASE_URL = DATABASE_URL
profile_acceptance.TOKEN = TOKEN
profile_acceptance.AGENTS_TOKEN = AGENTS_TOKEN
profile_acceptance.AUTH_SECRET = AUTH_SECRET
profile_acceptance.ORIGIN = ORIGIN
profile_acceptance.PYTHON = PYTHON


def assert_isolated() -> None:
    """Refuse accidental remote services and a non-dedicated acceptance database."""
    for value in (API_URL, MATCHING_URL, AGENTS_URL):
        parsed = urlparse(value)
        assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        assert parsed.username is None and parsed.password is None
    parsed = urlparse(DATABASE_URL)
    assert parsed.scheme.startswith("postgres") and parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == 25433
    assert re.fullmatch(r"/comit_profile_acceptance_feed(?:_[a-z0-9]+)*", parsed.path)


def call_json(client: httpx.Client, method: str, path: str, *, expected: int = 200,
              **kwargs: object) -> dict:
    """Make one JSON request and fail without dumping credentials or response payloads."""
    response = client.request(method, path, **kwargs)
    assert response.status_code == expected, f"{method} {path} returned {response.status_code}"
    value = response.json()
    assert isinstance(value, dict)
    return value


def run_publication_worker() -> None:
    """Deliver the agents publication through its real matching HTTP client."""
    environment = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "AGENTS_SERVICE_TOKEN": AGENTS_TOKEN,
        "CANDIDATE_PIPELINE_TOKEN": TOKEN,
        "MATCHING_ENGINE_URL": MATCHING_URL,
        "PYTHONPATH": f"{ROOT / 'services/matching-engine/src'}:{ROOT / 'services/agents-service/src'}",
    }
    code = (
        "from agents_service.store import Store; from agents_service.worker import Worker; "
        "from agents_service.providers import MatchingClient; import os; "
        "Worker(Store(os.environ['DATABASE_URL']), "
        "matching=MatchingClient(os.environ['MATCHING_ENGINE_URL'], "
        "os.environ['CANDIDATE_PIPELINE_TOKEN'])).process_once('publication')"
    )
    profile_acceptance.run_silent([PYTHON, "-c", code], env=environment)


def prepare_main(api: httpx.Client, matching: httpx.Client, employer: dict,
                 draft: dict, vacancy_id: str, *, top_k: int = 3) -> None:
    """Rebuild and publish the current main selection, then queue R0 fallback."""
    draft_path = f"/employer/vacancy-drafts/{draft['draft_id']}"
    combine = call_json(api, "POST", draft_path + "/combine",
                        headers={"Authorization": employer["authorization"]},
                        json={"top_k": top_k, "idempotency_key": str(uuid4())})
    assert combine["vacancy_id"] == vacancy_id
    profile_acceptance.run_matching_worker("combine-worker")
    selections = profile_acceptance.await_json(
        api,
        draft_path + f"/selections?limit={top_k}",
        lambda value: value["run"]["state"] == "ready" and len(value["items"]) == top_k,
        headers={"Authorization": employer["authorization"]},
    )
    assert selections["run"]["selected_count"] == top_k
    rerank = call_json(
        matching,
        "POST",
        f"/internal/vacancies/{vacancy_id}/rerank",
        json={"idempotency_key": str(uuid4())},
    )
    assert rerank["vacancy_id"] == vacancy_id
    profile_acceptance.run_matching_worker("rerank-worker")
    status = profile_acceptance.await_json(
        matching,
        f"/internal/vacancies/{vacancy_id}/rerank",
        lambda value: value["state"] == "ready",
    )
    assert status["mode"] == "main_fallback"
    assert status["recommended_count"] == top_k and status["reserve_count"] == 0


def feed_items(value: dict) -> list[dict]:
    """Extract cards from either the initial feed receipt or a session page."""
    items = value.get("items")
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def item_candidate(item: dict) -> str:
    """Read the candidate id from a feed item while keeping the assertion explicit."""
    card = item.get("card", item)
    assert isinstance(card, dict) and isinstance(card.get("candidate_id"), str)
    UUID(card["candidate_id"])
    return card["candidate_id"]


def read_feed_history(owner_id: str, vacancy_id: str, candidate_id: str) -> tuple[dict, list[dict]]:
    """Read the private baseline/history rows to verify persistence without exposing them over HTTP."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        current = connection.execute(
            """SELECT profile_revision,evaluated_revision,last_assessment,history_badges
               FROM feed_pipeline.visited
               WHERE owner_id=%s AND vacancy_id=%s AND candidate_id=%s""",
            (owner_id, vacancy_id, candidate_id),
        ).fetchone()
        history = connection.execute(
            """SELECT baseline_revision,evaluated_revision,baseline,assessment,event_type
               FROM feed_pipeline.visited_history
               WHERE owner_id=%s AND vacancy_id=%s AND candidate_id=%s
               ORDER BY history_id""",
            (owner_id, vacancy_id, candidate_id),
        ).fetchall()
    assert current is not None
    return dict(current), [dict(item) for item in history]


def main() -> None:
    """Run auth → profile/vacancy → main/R0 → feed decisions → structural release."""
    assert_isolated()
    profile_acceptance.migrate()

    import psycopg

    with psycopg.connect(DATABASE_URL) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM candidate_pipeline.pool").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agents_service.drafts").fetchone()[0] == 0
        # Feed migrations may evolve table names; every existing feed table must
        # be empty because the acceptance database is dedicated to this run.
        feed_tables = connection.execute(
            """SELECT schemaname, tablename FROM pg_catalog.pg_tables
               WHERE schemaname LIKE '%feed%' OR tablename LIKE '%feed%'"""
        ).fetchall()
        for row in feed_tables:
            qualified = f'"{row[0]}"."{row[1]}"'
            assert connection.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0] == 0

    checks: list[str] = []
    with httpx.Client(base_url=API_URL, timeout=25, follow_redirects=False) as api, \
            httpx.Client(base_url=MATCHING_URL, headers={"X-Internal-Token": TOKEN}, timeout=25) as matching:
        run_tag = uuid4().hex[:10]
        accounts = [
            profile_acceptance.login(api, "candidate", f"feed-acceptance-{run_tag}-{index}@example.test")
            for index in range(4)
        ]
        employer = profile_acceptance.login(api, "employer", f"feed-acceptance-{run_tag}-employer@example.test")
        foreign = profile_acceptance.login(api, "employer", f"feed-acceptance-{run_tag}-foreign@example.test")
        checks.append("real_http_auth_and_encrypted_mailbox")

        fields = [
            profile_acceptance.profile("Feed Candidate One", "Москва", (150_000, 260_000)),
            profile_acceptance.profile("Feed Candidate Two", "Москва", (150_000, 260_000)),
            profile_acceptance.profile("Feed Candidate Three", "Москва", (150_000, 260_000)),
            profile_acceptance.profile("Feed Candidate Four", "Москва", (150_000, 260_000)),
        ]
        for account, value in zip(accounts, fields):
            profile_acceptance.put_profile(api, account, value)
            profile_acceptance.run_profile_projection()
        for account in accounts:
            profile_acceptance.await_json(
                api, "/candidate/profile", lambda value: value["projection"]["state"] == "delivered",
                headers={"Authorization": account["authorization"]},
            )
            profile_acceptance.run_matching_worker("worker")
        current_candidate = matching.get(f"/internal/candidates/{accounts[1]['id']}")
        assert current_candidate.status_code == 200
        assert current_candidate.json()["state"] == "ready"
        pool = profile_acceptance.await_json(matching, "/internal/pool?limit=20",
                                             lambda value: len(value["items"]) == 4)
        assert all(item["category"]["grade_status"] == "self_reported" for item in pool["items"])
        checks.append("source_profile_projection_real_harrier_pool")

        employer_headers = {"Authorization": employer["authorization"]}
        draft = call_json(api, "POST", "/employer/vacancy-drafts", headers=employer_headers, json={"fields": {
            "title": "Feed acceptance backend vacancy", "zp_min": 170_000, "zp_max": 270_000,
            "daily": "5/2", "city": "Москва", "type": "onsite", "class": "backend",
            "bio": "Python backend and PostgreSQL", "stacks": {"python": 5},
        }})
        draft_path = f"/employer/vacancy-drafts/{draft['draft_id']}"
        assert api.get(draft_path, headers={"Authorization": foreign["authorization"]}).status_code == 404
        approved = call_json(api, "POST", draft_path + "/approve", headers=employer_headers,
                             json={"expected_revision": draft["revision"]})
        summary = call_json(api, "POST", draft_path + "/create", headers=employer_headers,
                            json={"expected_revision": approved["revision"],
                                  "summary_hash": approved["summary"]["summary_hash"]})
        assert summary["publication"]["status"] in {"pending", "accepted"}
        run_publication_worker()
        publication = profile_acceptance.await_json(
            api, draft_path,
            lambda value: value["state"] == "created" and value["publication"]["status"] == "accepted",
            headers=employer_headers,
        )
        vacancy_id = publication["vacancy_id"]
        profile_acceptance.run_matching_worker("vacancy-worker")
        profile_acceptance.await_json(matching, f"/internal/vacancies/{vacancy_id}/ready",
                                      lambda value: value["status"] == "open")
        prepare_main(api, matching, employer, draft, vacancy_id)
        checks.append("manual_draft_approve_create_main_and_real_r0_fallback")

        # Exercise the private feed contract and the owner-scoped public gateway
        # with the same reserve-disabled request.
        internal_feed = call_json(
            matching, "POST", f"/internal/vacancies/{vacancy_id}/feed",
            headers={"X-Owner-Id": employer["id"]}, json={"include_reserve": False},
        )
        assert internal_feed["vacancy_id"] == vacancy_id
        assert internal_feed["items"] and all(item["kind"] == "recommended" for item in internal_feed["items"])
        public_feed = call_json(api, "POST", draft_path + "/feed", headers=employer_headers,
                                json={"include_reserve": False, "limit": 2})
        assert public_feed["vacancy_id"] == vacancy_id
        assert public_feed["items"] and all(item["kind"] == "recommended" for item in public_feed["items"])
        session_id = public_feed.get("session_id")
        assert isinstance(session_id, str)
        cursor = public_feed.get("next_cursor")
        assert isinstance(cursor, str)
        page = call_json(api, "GET", draft_path + f"/feed/{session_id}?cursor={cursor}",
                         headers=employer_headers)
        assert feed_items(page) and all("exposure_id" in item for item in feed_items(page))
        items = feed_items(public_feed)
        assert len(items) == 2 and all("exposure_id" in item for item in items)
        first, second = items
        first_id, second_id = item_candidate(first), item_candidate(second)
        first_exposure, second_exposure = first["exposure_id"], second["exposure_id"]
        pdf = api.get(draft_path + f"/feed/exposures/{first_exposure}/resume.pdf", headers=employer_headers)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
        assert "79990000001" not in re.sub(r"\D", "", profile_acceptance.pdf_text(pdf.content))
        checks.append("public_feed_session_cursor_owner_pdf")

        like_key = str(uuid4())
        liked = call_json(api, "POST", draft_path + "/feed/decisions", headers=employer_headers,
                          json={"exposure_id": first_exposure, "action": "like", "idempotency_key": like_key})
        assert liked.get("action") == "like"
        replay = call_json(api, "POST", draft_path + "/feed/decisions", headers=employer_headers,
                           json={"exposure_id": first_exposure, "action": "like", "idempotency_key": like_key})
        assert replay["replayed"] is True
        assert {key: value for key, value in replay.items() if key != "replayed"} == {
            key: value for key, value in liked.items() if key != "replayed"}
        saved = call_json(api, "GET", draft_path + "/saved", headers=employer_headers)
        assert first_id in {item_candidate(item) for item in feed_items(saved)}

        dislike_key = str(uuid4())
        disliked = call_json(api, "POST", draft_path + "/feed/decisions", headers=employer_headers,
                             json={"exposure_id": second_exposure, "action": "dislike", "idempotency_key": dislike_key})
        assert disliked.get("action") == "dislike"
        visited = call_json(api, "GET", draft_path + "/visited", headers=employer_headers)
        visited_record = next(item for item in feed_items(visited) if item_candidate(item) == second_id)
        assert visited_record["profile_revision"] == 1
        assert visited_record["evaluated_revision"] == 1
        assert visited_record["state"] == "visited"
        assert visited_record["assessment"]["event"] == "dislike"
        current_db, history_db = read_feed_history(employer["id"], vacancy_id, second_id)
        assert current_db["profile_revision"] == 1 and current_db["evaluated_revision"] == 1
        assert history_db and history_db[0]["event_type"] == "dislike"
        assert history_db[0]["baseline_revision"] == 1 and history_db[0]["evaluated_revision"] == 1
        checks.append("like_saved_dislike_visited_idempotent")

        # A prose-only edit changes the source/profile revision but has zero
        # structural delta and therefore must keep the last dislike blocking.
        second_index = next(index for index, account in enumerate(accounts) if account["id"] == second_id)
        second_account = accounts[second_index]
        text_update = dict(fields[second_index])
        text_update["description"] = "A different private profile description."
        profile_acceptance.put_profile(api, second_account, text_update, revision=1)
        profile_acceptance.run_profile_projection()
        profile_acceptance.run_matching_worker("worker")
        profile_acceptance.run_matching_worker("combine-worker")
        current_candidate = matching.get(f"/internal/candidates/{second_id}")
        assert current_candidate.status_code == 200 and current_candidate.json()["profile_revision"] == 2
        visited_text = call_json(api, "GET", draft_path + "/visited", headers=employer_headers)
        visited_item = next(item for item in feed_items(visited_text) if item_candidate(item) == second_id)
        assert visited_item["profile_revision"] == 1
        assert visited_item["evaluated_revision"] == 2
        assert visited_item["state"] == "visited"
        assert visited_item["assessment"]["refresh"] is False
        assert visited_item["assessment"]["distance"] == 0.0
        assert "visited_updated" not in visited_item.get("history_badges", [])
        current_db, history_db = read_feed_history(employer["id"], vacancy_id, second_id)
        assert current_db["profile_revision"] == 1 and current_db["evaluated_revision"] == 2
        assert current_db["last_assessment"]["refresh"] is False
        assert len(history_db) == 1
        checks.append("text_only_update_does_not_release_visited")

        # A one-step grade upgrade is deliberately above the strict N>K policy
        # threshold (middle -> senior), so the next main run can include it.
        grade_update = dict(text_update)
        grade_update["declared_grade"] = "senior"
        profile_acceptance.put_profile(api, second_account, grade_update, revision=2)
        profile_acceptance.run_profile_projection()
        profile_acceptance.run_matching_worker("worker")
        profile_acceptance.run_matching_worker("combine-worker")
        current_candidate = matching.get(f"/internal/candidates/{second_id}")
        assert current_candidate.status_code == 200 and current_candidate.json()["profile_revision"] == 3
        prepare_main(api, matching, employer, draft, vacancy_id)
        visited_updated = call_json(api, "GET", draft_path + "/visited", headers=employer_headers)
        released = next(item for item in feed_items(visited_updated) if item_candidate(item) == second_id)
        assert released["profile_revision"] == 1
        assert released["evaluated_revision"] == 3
        assert released["state"] == "visited_updated"
        assert released["assessment"]["refresh"] is True
        assert released["assessment"]["distance"] > released["assessment"]["threshold"]
        assert "visited_updated" in released.get("history_badges", [])
        current_db, history_db = read_feed_history(employer["id"], vacancy_id, second_id)
        assert current_db["profile_revision"] == 1 and current_db["evaluated_revision"] == 3
        assert current_db["last_assessment"]["refresh"] is True
        assert history_db[-1]["event_type"] == "visited_updated"
        assert history_db[-1]["baseline_revision"] == 1 and history_db[-1]["evaluated_revision"] == 3
        checks.append("grade_upgrade_n_gt_k_visited_updated_badge")

        refreshed = call_json(api, "POST", draft_path + "/feed", headers=employer_headers,
                              json={"include_reserve": False, "limit": 3})
        refreshed_session = refreshed.get("session_id")
        assert isinstance(refreshed_session, str)
        refreshed_page = call_json(api, "GET", draft_path + f"/feed/{refreshed_session}",
                                   headers=employer_headers,
                                   params={"cursor": refreshed.get("cursor")} if refreshed.get("cursor") else None)
        released_cards = [item for item in feed_items(refreshed_page) if item_candidate(item) == second_id]
        assert released_cards and "visited_updated" in released_cards[0].get("history_badges", [])
        # A second dislike stores a new baseline and returns to visited.
        new_exposure = released_cards[0]["exposure_id"]
        re_dislike = call_json(api, "POST", draft_path + "/feed/decisions", headers=employer_headers,
                               json={"exposure_id": new_exposure, "action": "dislike",
                                     "idempotency_key": str(uuid4())})
        assert re_dislike.get("action") == "dislike"
        visited_again = call_json(api, "GET", draft_path + "/visited", headers=employer_headers)
        second_again = next(item for item in feed_items(visited_again) if item_candidate(item) == second_id)
        assert second_again["profile_revision"] == 3
        assert second_again["evaluated_revision"] == 3
        assert second_again["state"] == "visited"
        assert second_again["assessment"]["event"] == "dislike"
        current_db, history_db = read_feed_history(employer["id"], vacancy_id, second_id)
        assert current_db["profile_revision"] == 3 and current_db["evaluated_revision"] == 3
        assert history_db[-1]["event_type"] == "dislike"
        assert history_db[-1]["baseline_revision"] == 3 and history_db[-1]["evaluated_revision"] == 3
        assert "previously_visited" in second_again.get("history_badges", [])
        checks.append("rerun_released_badge_and_new_dislike_baseline")

    report = {
        "result": "passed",
        "checks": checks,
        "candidate_count": 4,
        "top_k": 3,
        "database": urlparse(DATABASE_URL).path.lstrip("/"),
        "ports": {"api": urlparse(API_URL).port, "matching": urlparse(MATCHING_URL).port,
                  "agents": urlparse(AGENTS_URL).port},
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
