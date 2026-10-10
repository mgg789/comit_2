"""Fresh loopback PG acceptance with real Harrier and inactive-model fallback.

The failed real teacher pilot forbids fitting or approving an R0 artifact.
This harness verifies ready profiles/PDFs, durable main→rerank delivery and
schema backfill without fabricating learned-model quality or human reviews.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import connect, migrate
from matching_engine.main import create_app
from matching_engine.models import CandidateProfile, CoordinateAddition
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import Store
from matching_engine.vacancy_models import VacancyInput
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker
from matching_engine.worker import Worker
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def drain(worker, limit: int = 50) -> None:
    """Drain finite local work, reporting worker failures through persisted status assertions."""
    for _ in range(limit):
        if not worker.process_once():
            return
    raise AssertionError("acceptance queue did not drain")


def main() -> None:
    """Create one new synthetic DB without truncating or altering an existing contour."""
    base_url = os.environ["PIPELINE_TEST_DATABASE_URL"]
    config = conninfo_to_dict(base_url)
    assert config.get("host") in ("127.0.0.1", "localhost", "::1"), "loopback PG required"
    database_name = "comit_reranker_acceptance_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    with psycopg.connect(make_conninfo(base_url, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    database_url = make_conninfo(base_url, dbname=database_name)
    applied = migrate(database_url)
    store, vacancies = Store(database_url), VacancyStore(database_url)
    combines, reranks = CombineStore(database_url), RerankStore(database_url)
    root = Path(__file__).resolve().parent.parent
    traits = json.loads((root / "tests/artifacts/reranker/pilot-profiles.json").read_text())
    seed = json.loads((root / "data/seed/candidate-pipeline.json").read_text())[0]["profile"]
    ids = []
    for index, trait in enumerate(traits):
        profile = json.loads(json.dumps(seed))
        profile.update(work_types=["remote"], city="Москва", primary_direction="backend",
                       salary={"from_rub": trait["salary_min"], "to_rub": trait["salary_min"] + 20000},
                       description=trait["description"])
        profile["grade_result"]["grade"] = trait["grade"]
        for group, keys in (("domains", ("sql", "rest_api")), ("stacks", ("python", "react")), ("soft_skills", ("teamwork",))):
            profile[group] = [{"key": key, "level": trait["levels"][key], "status": trait["status"]} for key in keys]
        profile["projects"] = [{"title": "Синтетический проект R0", "description": trait["description"],
            "status": trait["status"], "facts": {"stacks": [{"key": "python", "status": trait["status"]}],
                "duration_months": {"value": trait["duration_months"], "status": "partial"},
                "production_support": {"value": trait["production_support"], "status": "self_reported"}}}]
        candidate_id = UUID(int=9000 + index)
        ids.append(candidate_id)
        store.intake(candidate_id, CandidateProfile.model_validate(profile))
    candidate_worker, vacancy_worker = Worker(store), VacancyWorker(vacancies)
    drain(candidate_worker)
    assert all(store.status(identifier).state == "ready" for identifier in ids)
    owner = uuid4()
    vacancy_ids = []
    for index in range(3):
        vacancy_id = uuid4()
        vacancy_ids.append(vacancy_id)
        vacancies.intake(vacancy_id, VacancyInput.model_validate({"owner_id": owner, "source_revision": 1,
            "status": "open", "zp_min": 50000, "zp_max": 220000, "daily": "full_time", "type": "remote",
            "city": "Москва", "class": "backend", "bio": "Backend Python SQL API" if index == 0 else None,
            "stacks": {"python": 4} if index != 1 else {}}))
    drain(vacancy_worker)
    for vacancy_id in vacancy_ids:
        combines.request(vacancy_id, CombineRequest(top_k=3))
    drain(CombineWorker(combines))
    drain(RerankWorker(reranks))
    token = "synthetic-local-test-only"
    client = TestClient(create_app(store=store, internal_token=token))
    headers = {"x-internal-token": token}

    def assert_cards(version: int) -> None:
        for vacancy_id in vacancy_ids:
            response = client.get(f"/internal/vacancies/{vacancy_id}/rerank/selections", headers=headers)
            assert response.status_code == 200
            page = response.json()
            assert page["run"]["mode"] == "main_fallback" and page["run"]["fallback_reason"] == "artifact_absent"
            assert page["run"]["unranked_by_reranker_count"] == 2
            assert len(page["items"]) == 3
            assert all(card["position"] == card["main_position"] and card["rerank_score"] is None for card in page["items"])
            assert client.get(f"/internal/vacancies/{vacancy_id}/rerank/selections?kind=reserve", headers=headers).json()["items"] == []
            assert combines.status(vacancy_id).strict_schema_version == version

    assert client.get(f"/internal/vacancies/{vacancy_ids[0]}/rerank").status_code == 401
    assert_cards(1)
    pdf_count = 0
    for identifier in ids:
        response = client.get(f"/internal/pool/{identifier}/resume.pdf", headers=headers)
        assert response.status_code == 200 and response.content.startswith(b"%PDF-")
        pdf_count += 1
    with connect(database_url) as connection:
        snapshot = connection.execute("SELECT candidates_snapshot FROM matching_pipeline.runs LIMIT 1").fetchone()["candidates_snapshot"]
        assert all(candidate["narrative_facts"] for candidate in snapshot)
        assert "provenance" not in json.dumps(snapshot) and seed["email"] not in json.dumps(snapshot)
        assert all(len(candidate["semantic_vector"]) == 640 for candidate in snapshot)
        assert connection.execute("SELECT active_artifact_hash FROM rerank_pipeline.settings").fetchone()["active_artifact_hash"] is None
    store.extend_schema(CoordinateAddition(group="stack", key="synthetic_rerank_extension"))
    assert client.get(f"/internal/vacancies/{vacancy_ids[0]}/rerank/selections", headers=headers).status_code == 404
    drain(vacancy_worker)
    assert client.get(f"/internal/vacancies/{vacancy_ids[0]}/rerank/selections", headers=headers).status_code == 409
    drain(candidate_worker)
    drain(CombineWorker(combines))
    drain(RerankWorker(reranks))
    assert_cards(2)
    report = {"state": "passed", "database": database_name, "migrations": applied, "profiles": len(ids),
              "vacancies": len(vacancy_ids), "real_harrier_dimensions": 640, "public_pdf_count": pdf_count,
              "schema_backfill": "37->38", "learned_artifact_active": False,
              "quality_claim": "contractual backend acceptance only; real teacher pilot stopped",
              "delivery": combines.delivery_status().model_dump(mode="json")}
    target = root / "tests/artifacts/reranker/acceptance-report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
