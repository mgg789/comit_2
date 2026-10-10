"""Isolated real matching instrumentation, distinct-vacancy suppression and purge."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest
from matching_engine.combine_scoring import select_candidates
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.main import create_app
from matching_engine.store import Store
from matching_engine.vacancy_store import VacancyStore
from test_contracts import eligible_profile
from test_pipeline_integration import (
    store as store,  # noqa: PLC0414 - register isolated fixture.
)
from test_pipeline_integration import worker
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import run

pytestmark = pytest.mark.integration


def test_actual_publish_instruments_each_candidate_and_aggregates_distinct_vacancies(store: Store):
    candidate_id = uuid4()
    profile = eligible_profile(work_types=["remote"], salary={"from_rub": 900000, "to_rub": 1000000})
    # Canonical source exists in the API-owned test database: identity is never sent to inference.
    with connect(store.database_url) as connection:
        connection.execute("INSERT INTO accounts(id,email,role) VALUES(%s,%s,'candidate')", (candidate_id, f"{candidate_id}@example.test"))
        connection.execute("""INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,training_consent,
            training_consent_version,training_consent_accepted_at,consent_version,consent_accepted_at)
            VALUES(%s,1,true,true,true,'training-v2',now(),'profile-v1',now())""", (candidate_id,))
    store.intake(candidate_id, profile)
    assert worker(store).process_once()
    combines = CombineStore(store.database_url)
    vacancies = VacancyStore(store.database_url)
    ids = [uuid4() for _ in range(4)]
    for vacancy_id in ids:
        vacancies.intake(vacancy_id, eligible_vacancy())
        assert run(vacancies).process_once()
        combines.request(vacancy_id, CombineRequest())
        job = combines.claim(30, 5)
        assert job is not None
        outcome = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k, job.excluded_ids)
        assert combines.publish(job, outcome)
    now = datetime.now(UTC)
    client = TestClient(create_app(store=store, internal_token="synthetic"))
    path = f"/internal/candidates/{candidate_id}/recommendation-evidence"
    params = {"after": (now-timedelta(days=1)).isoformat(), "before": (now+timedelta(seconds=5)).isoformat()}
    headers = {"X-Internal-Token": "synthetic", "X-Owner-ID": str(candidate_id)}
    response = client.get(path, params=params, headers=headers)
    assert response.status_code == 200
    assert {"source": "matching", "category": "salary_disjoint", "count": 4} in response.json()["evidence"]
    assert str(candidate_id) not in response.text and str(ids[0]) not in response.text
    assert client.get(path, params=params, headers={**headers, "X-Owner-ID": str(UUID(int=2))}).status_code == 404
    # Many fresh run IDs for one vacancy still count as exactly one distinct vacancy.
    with connect(store.database_url) as connection:
        for _ in range(5):
            connection.execute("""INSERT INTO recommendation_evidence.observations(candidate_id,vacancy_id,run_id,category)
                VALUES(%s,%s,%s,'salary_disjoint')""", (candidate_id, ids[0], uuid4()))
        connection.execute("""INSERT INTO recommendation_evidence.observations(candidate_id,vacancy_id,run_id,category)
            VALUES(%s,%s,%s,'class_mismatch')""", (candidate_id, ids[0], uuid4()))
    result = client.get(path, params=params, headers=headers).json()["evidence"]
    assert {"source": "matching", "category": "salary_disjoint", "count": 4} in result
    assert not any(entry["category"] == "class_mismatch" for entry in result)
    # API trigger deletes matching evidence synchronously, before projection reaches matching.
    with connect(store.database_url) as connection:
        connection.execute("""UPDATE candidate_profiles SET training_consent=false,training_consent_version=NULL,
            training_consent_accepted_at=NULL,revision=revision+1 WHERE candidate_id=%s""", (candidate_id,))
        assert connection.execute("SELECT count(*) AS n FROM recommendation_evidence.observations WHERE candidate_id=%s", (candidate_id,)).fetchone()["n"] == 0
    assert client.get(path, params=params, headers=headers).json() == {"evidence": []}
