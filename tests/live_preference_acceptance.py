"""Isolated real-Harrier acceptance of continuous preference learning and live delivery."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import connect, migrate
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.main import create_app
from matching_engine.models import CandidateProfile, CoordinateAddition
from matching_engine.preference_store import PreferenceStore
from matching_engine.rerank_activation import bootstrap_packaged
from matching_engine.rerank_hash import digest
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import Store
from matching_engine.vacancy_models import VacancyInput
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker
from matching_engine.worker import Worker
from psycopg.conninfo import conninfo_to_dict


def drain(runner, maximum: int = 70) -> None:
    """Drain bounded real pipeline work; no encoder or model implementation is substituted."""
    for _ in range(maximum):
        if not runner.process_once():
            return
    raise AssertionError("worker did not drain")


def main() -> None:
    """Run synthetic facts/actions with real Harrier and a separate continuous learner process."""
    root = Path(__file__).resolve().parents[1]
    url = os.environ["LIVE_PREFERENCE_ACCEPTANCE_DATABASE_URL"]
    config = conninfo_to_dict(url)
    assert config.get("host") in ("127.0.0.1", "localhost", "::1")
    assert config["dbname"].startswith("comit_live_preferences_")
    target = root / "tests/artifacts/live-preferences" / config["dbname"]
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    with connect(url) as connection:
        assert connection.execute("SELECT to_regclass('candidate_pipeline.candidates') AS table_name").fetchone()["table_name"] is None
    environment = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(root / "services/matching-engine/src"), "PIPELINE_POLL_SECONDS": ".2"}
    subprocess.run(["node", "apps/api/dist/auth/migrate.js"], cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL)
    migrations = migrate(url)
    with connect(url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    store, vacancies = Store(url), VacancyStore(url)
    combines, reranks = CombineStore(url), RerankStore(url)
    bootstrap_packaged(reranks)
    seed = json.loads((root / "data/seed/candidate-pipeline.json").read_text())[0]["profile"]
    ids = sorted((uuid4() for _ in range(48)), key=str)
    supports = {}
    for index, candidate_id in enumerate(ids):
        profile = json.loads(json.dumps(seed))
        profile["consents"].update(training=True, training_version="training-v1", training_accepted_at=profile["consents"]["accepted_at"])
        supported = index % 2 == 1
        supports[candidate_id] = supported
        profile.update(source_revision=1, city="Москва", primary_direction="backend", work_types=["remote"],
                       salary={"from_rub": 100000, "to_rub": 200000},
                       description="Синтетическая приёмка: Python API и SQL, одинаковое описание для всех профилей.")
        profile["grade_result"]["grade"] = "middle"
        profile["experience"][0]["facts"] = {"stacks": [{"key": "python", "status": "confirmed"}],
            "production_support": {"value": supported, "status": "confirmed"}}
        store.intake(candidate_id, CandidateProfile.model_validate(profile))
        with connect(url) as connection:
            connection.execute("INSERT INTO accounts(id,email,role,email_verified) VALUES(%s,%s,'candidate',true)", (candidate_id, f"live-preference-{index}@example.invalid"))
            connection.execute("""INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,
                consent_version,consent_accepted_at,training_consent,training_consent_version,training_consent_accepted_at) VALUES(%s,1,true,true,'synthetic-live-v1',now(),true,'training-v1',now())""", (candidate_id,))
    candidate_worker = Worker(store)
    drain(candidate_worker)
    assert all(store.status(candidate).state == "ready" for candidate in ids)
    owner_id, vacancy_id = uuid4(), uuid4()
    vacancies.intake(vacancy_id, VacancyInput.model_validate({"source_revision": 1, "owner_id": owner_id,
        "status": "open", "zp_min": 90000, "zp_max": 220000, "daily": "full_time", "type": "remote",
        "city": "Москва", "class": "backend", "stacks": {"python": 4}, "bio": "Python SQL API backend"}))
    drain(VacancyWorker(vacancies))
    combines.request(vacancy_id, CombineRequest(top_k=48))
    drain(CombineWorker(combines))
    drain(RerankWorker(reranks))
    feed, preferences = FeedStore(url), PreferenceStore(url)
    page = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    session_id = page.session_id
    log_path = target / "worker.log"
    with log_path.open("x", encoding="utf-8") as log:
        log_path.chmod(0o600)
        learner = subprocess.Popen([sys.executable, "-m", "matching_engine", "preference-worker"], cwd=root,
                                   env=environment, stdout=log, stderr=log)
        try:
            for index in range(40):
                assert page.items and page.items[0].preference_state == "baseline"
                card = page.items[0]
                request = FeedDecisionRequest(exposure_id=card.exposure_id,
                    action="like" if supports[card.candidate_id] else "dislike", idempotency_key=uuid4())
                receipt = feed.decision(owner_id, vacancy_id, request)
                assert feed.decision(owner_id, vacancy_id, request).receipt_id == receipt.receipt_id
                if index < 39:
                    page = feed.page(owner_id, vacancy_id, session_id, page.next_cursor, 1)
            before = preferences.status(owner_id, vacancy_id)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                status = preferences.status(owner_id, vacancy_id)
                if status["job_state"] in ("ready", "rejected", "failed"):
                    break
                time.sleep(.2)
            assert status["state"] == "active", status
            page = feed.page(owner_id, vacancy_id, session_id, page.next_cursor, 1)
            assert page.items[0].preference_state == "personalized" and page.items[0].preference_model_hash == status["active_model_hash"]
            assert page.items[0].rerank_score is not None
            personal_order_revision = page.order_revision
            with connect(url) as connection:
                model_row = connection.execute("SELECT * FROM preference_pipeline.models WHERE model_hash=%s", (status["active_model_hash"],)).fetchone()
                job = connection.execute("SELECT * FROM preference_pipeline.jobs WHERE job_id=%s", (model_row["job_id"],)).fetchone()
                baseline_positions = connection.execute("SELECT candidate_id,position FROM rerank_pipeline.cards ORDER BY position").fetchall()
                live_positions = connection.execute("SELECT candidate_id,position FROM feed_pipeline.exposures WHERE session_id=%s AND position>40 ORDER BY position", (session_id,)).fetchall()
                original_tail = [row["candidate_id"] for row in baseline_positions if row["position"] > 40]
                assert [row["candidate_id"] for row in live_positions] != original_tail
                dimensions = connection.execute("SELECT vector_dims(semantic_vector) AS dimensions FROM candidate_pipeline.pool LIMIT 1").fetchone()["dimensions"]
            assert dimensions == 640
            # Source-only withdrawal invalidates runtime before the matching projection.
            participant = UUID(job["plan"]["splits"]["train"][0]["candidate_key"])
            with connect(url) as connection:
                connection.execute("UPDATE candidate_profiles SET pool_consent=false,training_consent=false,training_consent_version=NULL,training_consent_accepted_at=NULL,revision=revision+1 WHERE candidate_id=%s", (participant,))
            fallback = feed.page(owner_id, vacancy_id, session_id, page.next_cursor, 1)
            assert fallback.items[0].preference_state == "baseline" and fallback.order_revision > personal_order_revision
            preferences.reconcile()
            with connect(url) as connection:
                assert connection.execute("SELECT model FROM preference_pipeline.models WHERE model_hash=%s", (status["active_model_hash"],)).fetchone()["model"] is None
            client = TestClient(create_app(store=store, internal_token="synthetic-live-preference-token"))
            headers = {"X-Internal-Token": "synthetic-live-preference-token", "X-Owner-ID": str(owner_id)}
            state = client.get(f"/internal/vacancies/{vacancy_id}/preferences", headers=headers).json()
            reset = client.post(f"/internal/vacancies/{vacancy_id}/preferences/reset", headers=headers,
                                json={"idempotency_key": str(uuid4()), "expected_revision": state["revision"]})
            assert reset.status_code == 200 and reset.json()["eligible_reactions"] == 0
            store.extend_schema(CoordinateAddition(group="stack", key="synthetic_live_preference_extension"))
            preferences.reconcile()
            assert preferences.status(owner_id, vacancy_id)["active_model_hash"] is None
            report = {"version": "live-preference-acceptance-v1", "state": "passed", "checked_at": datetime.now(UTC).isoformat(),
                "contour": "isolated_synthetic_actual_facts_and_explicit_actions", "database": config["dbname"],
                "profiles": 48, "reactions": 40, "real_harrier_dimensions": dimensions, "migrations": migrations,
                "baseline_hash": model_row["model"]["baseline_artifact_hash"], "model_hash": model_row["model_hash"],
                "input_hash": model_row["input_hash"], "plan_hash": model_row["plan_hash"],
                "partitions": {name: len(job["plan"]["splits"][name]) for name in ("train", "validation", "golden")},
                "metrics": {name: job["report"]["contexts"][job["context_hash"]][name]["delta"] for name in ("validation", "golden")},
                "gate": model_row["gate"], "continuous_worker": True, "worker_requested_automatically": bool(before["job_id"]),
                "actual_suffix_reordered": True, "source_only_withdrawal_fallback": True, "coefficients_purged": True,
                "reset_discards_old_actions": True, "schema_change_fallback": True, "network_model_calls": 0,
                "production": "not_run", "selection_bias": "synthetic_deterministic_feedback_not_real_employer_quality"}
            report["report_hash"] = digest(report)
            path = target / "report.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            path.chmod(0o600)
            print(json.dumps(report, ensure_ascii=False))
        finally:
            learner.terminate()
            try:
                learner.wait(timeout=10)
            except subprocess.TimeoutExpired:
                learner.kill()
                learner.wait(timeout=5)


if __name__ == "__main__":
    main()
