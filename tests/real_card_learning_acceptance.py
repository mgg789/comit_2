"""Synthetic, isolated PostgreSQL acceptance with actual Harrier and the packaged R₀."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import connect, migrate
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.learning_store import LearningStore
from matching_engine.main import create_app
from matching_engine.models import CandidateProfile, CoordinateAddition
from matching_engine.rerank_activation import REVIEWED_ARTIFACT_HASH, bootstrap_packaged
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import Store
from matching_engine.vacancy_models import VacancyInput
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker
from matching_engine.worker import Worker


def drain(runner, limit: int = 30) -> None:
    """Drain finite synthetic jobs without replacing Harrier or invoking a teacher/fit."""
    for _ in range(limit):
        if not runner.process_once():
            return
    raise AssertionError("acceptance worker did not drain")


def main() -> None:
    """Require a new loopback test database and preserve all real/runtime environments."""
    root = Path(__file__).resolve().parent.parent
    database_url = os.environ["LEARNING_ACCEPTANCE_DATABASE_URL"]
    config = conninfo_to_dict(database_url)
    assert config.get("host") in ("127.0.0.1", "localhost", "::1")
    assert config["dbname"].startswith("comit_cards_learning_")
    with connect(database_url) as connection:
        assert connection.execute("SELECT to_regclass('candidate_pipeline.candidates') AS source").fetchone()["source"] is None
    environment = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(["node", "apps/api/dist/auth/migrate.js"], cwd=root, env=environment,
                   stdout=subprocess.DEVNULL, check=True)
    migrations = migrate(database_url)
    with connect(database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    store, vacancies = Store(database_url), VacancyStore(database_url)
    combines, reranks = CombineStore(database_url), RerankStore(database_url)
    bootstrap = bootstrap_packaged(reranks)
    seed = json.loads((root / "data/seed/candidate-pipeline.json").read_text())[0]["profile"]
    candidates = []
    for index, grade in enumerate(("intern", "junior", "middle", "senior", "middle")):
        candidate_id = uuid4()
        candidates.append(candidate_id)
        profile = json.loads(json.dumps(seed))
        profile.update(source_revision=1, work_types=["remote"], city="Москва", primary_direction="backend",
                       description="Синтетический профиль: эксплуатация Python API, SQL и командная работа.",
                       salary={"from_rub": 500000 if index == 4 else 80000 + 30000 * index,
                               "to_rub": 550000 if index == 4 else 130000 + 30000 * index})
        profile["grade_result"]["grade"] = grade
        store.intake(candidate_id, CandidateProfile.model_validate(profile))
        with connect(database_url) as connection:
            connection.execute("INSERT INTO accounts(id,email,role,email_verified) VALUES(%s,%s,'candidate',true)",
                               (candidate_id, f"learning-{index}@example.invalid"))
            connection.execute("""INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,
                pool_consent,consent_version,consent_accepted_at) VALUES(%s,1,true,true,'synthetic-learning-v1',now())""",
                (candidate_id,))
    candidate_worker, vacancy_worker = Worker(store), VacancyWorker(vacancies)
    drain(candidate_worker)
    assert all(store.status(candidate).state == "ready" for candidate in candidates)
    owner_id, vacancy_id = uuid4(), uuid4()
    vacancies.intake(vacancy_id, VacancyInput.model_validate({"source_revision": 1, "owner_id": owner_id,
        "status": "open", "zp_min": 90000, "zp_max": 220000, "daily": "full_time", "type": "remote",
        "city": "Москва", "class": "backend", "stacks": {"python": 4}, "bio": "Python SQL API backend"}))
    drain(vacancy_worker)
    combines.request(vacancy_id, CombineRequest(top_k=3))
    drain(CombineWorker(combines))
    drain(RerankWorker(reranks))
    result = reranks.selections(vacancy_id, "recommended", 100, 0)
    assert result.run.mode == "learned" and result.run.activation_kind == "manual_override"
    assert result.run.artifact_hash == REVIEWED_ARTIFACT_HASH
    assert result.run.selected_count == 3 and result.run.reserve_count == 0
    assert result.run.unranked_by_reranker_count == 1
    assert all(card.rerank_score is not None and card.rerank_contributions for card in result.items)
    feed = FeedStore(database_url)
    page = feed.open(owner_id, FeedRequest(limit=2), vacancy_id)
    client = TestClient(create_app(store=store, internal_token="synthetic-learning-service-token"))
    headers = {"X-Internal-Token": "synthetic-learning-service-token", "X-Owner-ID": str(owner_id)}
    first = page.items[0]
    body = {"exposure_id": str(first.exposure_id), "idempotency_key": str(uuid4())}
    response = client.post(f"/internal/vacancies/{vacancy_id}/feed/impressions", headers=headers, json=body)
    assert response.status_code == 200
    retry = client.post(f"/internal/vacancies/{vacancy_id}/feed/impressions", headers=headers, json=body)
    assert retry.json()["event_id"] == response.json()["event_id"] and retry.json()["replayed"]
    for card, action in zip(page.items, ("like", "dislike"), strict=True):
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
                                                             action=action, idempotency_key=uuid4()))
    output = root / "tests/artifacts/real-card-learning"
    output.mkdir(exist_ok=False)
    key = b"synthetic-acceptance-pseudonym-key-32"
    exporter = LearningStore(database_url)
    exported = exporter.export(output / "synthetic", key, source="synthetic")
    assert exported["rows"] == 3 and exported["explicit_decisions"] == 2
    # Exercise operational source-consent plumbing with explicitly synthetic fixtures.
    with connect(database_url) as connection:
        connection.execute("UPDATE learning_pipeline.cohorts SET source='operational'")
    operational = exporter.export(output / "simulated-operational", key)
    assert operational["rows"] == 3 and operational["consent_scope"] == "authoritative_api_and_matching"
    with connect(database_url) as connection:
        connection.execute("UPDATE candidate_profiles SET pool_consent=false,revision=revision+1 WHERE candidate_id=%s", (first.candidate_id,))
        assert connection.execute("SELECT (profile->'consents'->>'pool')::boolean AS enabled FROM candidate_pipeline.candidates WHERE candidate_id=%s", (first.candidate_id,)).fetchone()["enabled"]
    revoked = exporter.export(output / "source-revoked", key)
    assert revoked["rows"] == 2
    with connect(database_url) as connection:
        withdrawn = connection.execute("SELECT features,withdrawn_at FROM learning_pipeline.samples WHERE candidate_id=%s", (first.candidate_id,)).fetchone()
        assert withdrawn["features"] is None and withdrawn["withdrawn_at"] is not None
    store.extend_schema(CoordinateAddition(group="stack", key="synthetic_learning_extension"))
    drain(candidate_worker)
    drain(vacancy_worker)
    drain(CombineWorker(combines))
    drain(RerankWorker(reranks))
    fallback = reranks.selections(vacancy_id, "recommended", 100, 0)
    assert fallback.run.mode == "main_fallback" and fallback.run.fallback_reason == "feature_schema_mismatch"
    assert all(card.rerank_score is None and card.main_position == card.position for card in fallback.items)
    report = {"state": "passed", "contour": "isolated_synthetic_only", "database": config["dbname"],
        "profiles": 5, "vacancies": 1, "selected": 3, "passed_outside_top_k": 1,
        "model_hash": REVIEWED_ARTIFACT_HASH, "activation_kind": "manual_override",
        "approval_receipt_hash": result.run.approval_receipt_hash, "gate_changed": False,
        "real_harrier_dimensions": 640, "runtime_fit": False, "tail_cut": False,
        "initial_mode": result.run.mode, "impression_replay": True, "explicit_decisions": 2,
        "export_rows": 3, "source_revocation_rows": 2, "feature_payload_purged": True,
        "schema_change": "37->38", "schema_change_fallback": fallback.run.fallback_reason,
        "migrations": migrations, "bootstrap_activated": bootstrap["state"] == "manual_sort_only_activated",
        "checked_at": datetime.now(UTC).isoformat(), "production": "not_run"}
    (root / "data/validation/real-card-learning-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
