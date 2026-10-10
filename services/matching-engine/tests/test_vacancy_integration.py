"""Dedicated PostgreSQL lifecycle and race coverage; inference doubles are test-only."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_contracts import eligible_profile
from test_pipeline_integration import TestEncoder, worker
from test_pipeline_integration import (
    store as store,  # noqa: PLC0414 - register the shared pytest fixture.
)
from test_vacancy_contracts import eligible_vacancy

from matching_engine.database import connect
from matching_engine.main import create_app
from matching_engine.models import CoordinateAddition
from matching_engine.store import ConflictError, NotFoundError, Store
from matching_engine.vacancy_features import build_strict_features
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker

pytestmark = pytest.mark.integration


@pytest.fixture
def vacancies(store: Store) -> VacancyStore:
    """Share one explicitly opted-in disposable database with the candidate pipeline."""
    return VacancyStore(store.database_url)


def run(vacancies: VacancyStore, encoder=None, **kwargs) -> VacancyWorker:
    """Inject an isolated deterministic encoder with immediate retry scheduling."""
    return VacancyWorker(vacancies, encoder or TestEncoder(), retry_base_seconds=0, **kwargs)


def publish(vacancies, job) -> bool:
    """Exercise actual lease checks using genuine strict artifact construction."""
    vector, mask = build_strict_features(job.profile, job.schema)
    return vacancies.publish(job, vector, mask, None, None, None)


def test_sparse_vacancies_ready_without_inference_and_pagination(vacancies: VacancyStore) -> None:
    encoder = TestEncoder(fail=True)
    ids = [uuid4() for _ in range(3)]
    inputs = [eligible_vacancy(), eligible_vacancy(**{"class": None, "domens": {"sql": 7}}),
              eligible_vacancy(**{"class": None, "stacks": {"python": 0}})]
    client = TestClient(create_app(store=Store(vacancies.database_url), vacancy_store=vacancies,
                                   internal_token="test", encoder_ready=lambda: True))
    headers = {"X-Internal-Token": "test"}
    for vacancy_id, value in zip(ids, inputs, strict=True):
        response = client.put(f"/internal/vacancies/{vacancy_id}", json=value.model_dump(mode="json", by_alias=True), headers=headers)
        assert response.status_code == 200 and response.json()["state"] == "queued"
        assert run(vacancies, encoder).process_once()
    assert encoder.calls == 0
    page = client.get("/internal/vacancies/ready?limit=2&offset=1", headers=headers)
    assert page.status_code == 200 and len(page.json()["items"]) == 2
    entry = client.get(f"/internal/vacancies/{ids[0]}/ready", headers=headers).json()
    assert entry["class"] == "backend" and entry["semantic_vector"] is None
    assert entry["semantic_available"] is False and entry["semantic_space_id"] is None
    assert client.get("/internal/vacancies/ready").status_code == 401


def test_draft_missing_fields_preserved_and_owner_can_only_be_assigned_once(vacancies: VacancyStore) -> None:
    vacancy_id = uuid4()
    profile = eligible_vacancy(**{"class": None, "owner_id": None, "soft_skills": {"teamwork": 3}})
    assert vacancies.intake(vacancy_id, profile).state == "draft"
    assert not run(vacancies).process_once()
    profile.owner_id = uuid4()
    profile.source_revision = 2
    vacancies.intake(vacancy_id, profile)
    profile.owner_id = uuid4()
    profile.source_revision = 3
    with pytest.raises(ConflictError, match="owner_id"):
        vacancies.intake(vacancy_id, profile)
    assert vacancies.page(100, 0).items == []


def test_null_bio_preserves_raw_input_and_publishes_without_inference(vacancies: VacancyStore) -> None:
    """Clearing optional narrative with null succeeds at the real HTTP and storage boundary."""
    vacancy_id = uuid4()
    payload = eligible_vacancy().model_dump(mode="json", by_alias=True)
    payload["bio"] = None
    client = TestClient(create_app(store=Store(vacancies.database_url), vacancy_store=vacancies,
                                   internal_token="test", encoder_ready=lambda: True))
    response = client.put(f"/internal/vacancies/{vacancy_id}", json=payload,
                          headers={"X-Internal-Token": "test"})
    assert response.status_code == 200
    encoder = TestEncoder(fail=True)
    assert run(vacancies, encoder).process_once() and encoder.calls == 0
    ready = vacancies.ready_entry(vacancy_id)
    assert ready.bio == "" and not ready.semantic_available
    assert ready.semantic_vector is ready.semantic_model_version is ready.semantic_space_id is None
    with connect(vacancies.database_url) as connection:
        saved = connection.execute("SELECT profile,raw_input FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                   (vacancy_id,)).fetchone()
        assert saved["raw_input"]["bio"] is None and saved["profile"]["bio"] == ""


@pytest.mark.parametrize("status", ["draft", "pending", "closed", "done"])
def test_business_status_withdraws_artifacts_and_only_nonclosed_reopens(vacancies, status) -> None:
    """Closure is terminal; other inactive snapshots may publish a later source revision."""
    vacancy_id, profile = uuid4(), eligible_vacancy()
    vacancies.intake(vacancy_id, profile)
    run(vacancies).process_once()
    profile.status, profile.source_revision = status, 2
    assert vacancies.intake(vacancy_id, profile).state == "draft"
    with pytest.raises(NotFoundError):
        vacancies.ready_entry(vacancy_id)
    with connect(vacancies.database_url) as connection:
        assert connection.execute("SELECT event_type FROM vacancy_pipeline.outbox ORDER BY id DESC LIMIT 1").fetchone()["event_type"] == "vacancy.withdrawn"
    profile.status, profile.source_revision = "open", 3
    if status == "closed":
        with pytest.raises(ConflictError, match="vacancy_closed"):
            vacancies.intake(vacancy_id, profile)
        assert vacancies.status(vacancy_id).revision == 2
        with pytest.raises(NotFoundError):
            vacancies.ready_entry(vacancy_id)
        return
    vacancies.intake(vacancy_id, profile)
    run(vacancies).process_once()
    assert vacancies.ready_entry(vacancy_id).revision == 3


def test_revision_idempotency_and_old_worker_cannot_publish_new_snapshot(vacancies) -> None:
    vacancy_id, profile = uuid4(), eligible_vacancy()
    assert vacancies.intake(vacancy_id, profile).revision == 1
    assert vacancies.intake(vacancy_id, profile).revision == 1
    old = vacancies.claim(30, 5)
    changed = profile.model_copy(deep=True)
    changed.daily = "2/2"
    with pytest.raises(ConflictError):
        vacancies.intake(vacancy_id, changed)
    changed.source_revision = 2
    vacancies.intake(vacancy_id, changed)
    with pytest.raises(ConflictError):
        vacancies.intake(vacancy_id, profile)
    assert not publish(vacancies, old)
    run(vacancies).process_once()
    assert vacancies.ready_entry(vacancy_id).daily == "2/2"


def test_query_inference_cache_and_failures_never_drop_supplied_bio(vacancies) -> None:
    vacancy_id, profile = uuid4(), eligible_vacancy(bio="API integration role")
    vacancies.intake(vacancy_id, profile)
    failed = run(vacancies, TestEncoder(fail=True), max_attempts=2)
    failed.process_once()
    assert vacancies.status(vacancy_id).state == "retrying"
    failed.process_once()
    assert vacancies.status(vacancy_id).state == "failed"
    with pytest.raises(NotFoundError):
        vacancies.ready_entry(vacancy_id)
    assert vacancies.retry(vacancy_id).state == "queued"
    encoder = TestEncoder()
    run(vacancies, encoder).process_once()
    ready = vacancies.ready_entry(vacancy_id)
    assert ready.semantic_available and len(ready.semantic_vector) == 640
    profile.source_revision = 2
    profile.daily = "2/2"
    vacancies.intake(vacancy_id, profile)
    run(vacancies, encoder).process_once()
    assert encoder.calls == 1


def test_lease_recovery_old_token_and_final_attempt_failure(vacancies) -> None:
    vacancy_id = uuid4()
    vacancies.intake(vacancy_id, eligible_vacancy())
    old = vacancies.claim(30, 2)
    assert vacancies.heartbeat(old, 30)
    with connect(vacancies.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.jobs SET lease_until=now()-interval '1 second'")
    new = vacancies.claim(30, 2)
    assert new.lease_token != old.lease_token and new.attempts == 2
    assert not publish(vacancies, old)
    with connect(vacancies.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.jobs SET lease_until=now()-interval '1 second'")
    assert vacancies.claim(30, 2) is None
    assert vacancies.status(vacancy_id).error_code == "lease_exhausted"


def test_shared_schema_backfill_invalidates_both_pools_and_stale_workers(vacancies, store) -> None:
    vacancy_id, draft_id, candidate_id = uuid4(), uuid4(), uuid4()
    vacancies.intake(vacancy_id, eligible_vacancy(stacks={"python": 0}))
    run(vacancies).process_once()
    vacancies.intake(draft_id, eligible_vacancy(**{"class": None}))
    store.intake(candidate_id, eligible_profile())
    worker(store).process_once()
    second = uuid4()
    vacancies.intake(second, eligible_vacancy())
    old = vacancies.claim(30, 5)
    schema = store.extend_schema(CoordinateAddition(group="stack", key="rust"))
    assert schema.version == 2 and vacancies.page(100, 0).items == [] and store.pool(100, 0).items == []
    assert not publish(vacancies, old)
    for _ in range(4):
        run(vacancies).process_once()
    worker(store).process_once()
    for entry in vacancies.page(100, 0).items:
        assert len(entry.strict_vector) == 38 and len(entry.requirement_mask) == 38
        assert not entry.requirement_mask[-1] and entry.strict_vector[-1] == 0
        assert entry.strict_schema_hash == store.pool(100, 0).items[0].strict_schema_hash
    assert vacancies.status(draft_id).state == "draft"
    with connect(vacancies.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM vacancy_pipeline.jobs WHERE schema_version=2").fetchone()["n"] == 3


def test_wire_alias_original_snapshot_preserved(vacancies) -> None:
    raw = eligible_vacancy().model_dump(mode="json", by_alias=True)
    raw["class"], raw["type"] = "ML", "гибрид"
    client = TestClient(create_app(store=Store(vacancies.database_url), internal_token="test"))
    vacancy_id = uuid4()
    assert client.put(f"/internal/vacancies/{vacancy_id}", json=raw, headers={"X-Internal-Token": "test"}).status_code == 200
    with connect(vacancies.database_url) as connection:
        row = connection.execute("SELECT profile,raw_input FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s", (vacancy_id,)).fetchone()
        assert row["profile"]["class"] == "ml" and row["profile"]["type"] == "hybrid"
        assert row["raw_input"]["class"] == "ML" and row["raw_input"]["type"] == "гибрид"
