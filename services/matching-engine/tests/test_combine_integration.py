"""Disposable PostgreSQL coverage of durable dispatch, snapshot guards and main batch publication."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_contracts import eligible_profile
from test_pipeline_integration import (
    store as store,  # noqa: PLC0414 - register shared fixture.
)
from test_pipeline_integration import worker
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import run

from matching_engine.combine_models import CombineRequest, SelectionStateInput
from matching_engine.combine_scoring import select_candidates
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import connect
from matching_engine.embeddings import EMBEDDING_SPACE_ID, MODEL_VERSION
from matching_engine.main import create_app
from matching_engine.models import CoordinateAddition
from matching_engine.store import ConflictError, NotFoundError, Store
from matching_engine.vacancy_store import VacancyStore

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("stored_version", [MODEL_VERSION, "legacy-model:document:640"])
def test_semantic_space_uses_persisted_document_fingerprint(store: Store, stored_version: str) -> None:
    """Legacy vectors must never inherit the current runtime's compatible query space."""
    combines, vacancy_id, candidates = setup_pool(store, 1)
    vacancies = VacancyStore(store.database_url)
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                      (vacancy_id,)).fetchone()["owner_id"]
    vacancies.intake(vacancy_id, eligible_vacancy(owner_id=owner_id, source_revision=2, bio="Synthetic backend role"))
    assert run(vacancies).process_once()
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET semantic_model_version=%s WHERE candidate_id=%s",
                           (stored_version, candidates[0]))
        connection.execute("UPDATE vacancy_pipeline.ready SET semantic_space_id=%s WHERE vacancy_id=%s",
                           (EMBEDDING_SPACE_ID, vacancy_id))
    combines.request(vacancy_id, CombineRequest())
    job = combines.claim(30, 5)
    candidate = job.candidates[0]
    assert candidate.semantic_model_version == stored_version
    assert (candidate.semantic_space_id == job.vacancy.semantic_space_id) == (stored_version == MODEL_VERSION)
    result = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k)
    explanation = result.items[0].explanation
    if stored_version == MODEL_VERSION:
        assert explanation.semantic_cosine == 1 and explanation.semantic_unavailable_reason is None
    else:
        assert explanation.semantic_cosine is None and explanation.semantic_contribution == 0
        assert explanation.semantic_unavailable_reason == "semantic_space_mismatch"


def setup_pool(store: Store, count: int = 3) -> tuple[CombineStore, UUID, list[UUID]]:
    """Publish synthetic candidate/vacancy artifacts using real pipeline stores, without real inference."""
    candidates = [UUID(int=index + 1) for index in range(count)]
    for candidate_id in candidates:
        store.intake(candidate_id, eligible_profile(work_types=["remote"]))
        assert worker(store).process_once()
    vacancies = VacancyStore(store.database_url)
    vacancy_id = uuid4()
    vacancies.intake(vacancy_id, eligible_vacancy())
    assert run(vacancies).process_once()
    return CombineStore(store.database_url), vacancy_id, candidates


def finish(combines: CombineStore) -> None:
    """Drain finite currently available work with immediate test-only retry scheduling."""
    runner = CombineWorker(combines, retry_base_seconds=0)
    for _ in range(30):
        if not runner.process_once():
            return
    raise AssertionError("combine worker did not become idle")


def test_events_drain_after_startup_and_publish_single_contact_free_paginated_batch(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    status = combines.delivery_status()
    assert status.candidate_pending == 3 and status.vacancy_pending == 1
    assert CombineWorker(combines).process_once()
    status = combines.delivery_status()
    assert status.candidate_pending == status.vacancy_pending == 0 and status.acknowledged_count == 4
    page = combines.selections(vacancy_id, 2, 1)
    assert page.run.state == "ready" and page.run.scanned_count == 3
    assert [item.candidate_id for item in page.items] == candidates[1:]
    assert [item.position for item in page.items] == [2, 3]
    assert combines.selections(vacancy_id, 2, 100).items == []
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM matching_pipeline.runs").fetchone()["n"] == 1
        snapshot = connection.execute("SELECT candidates_snapshot FROM matching_pipeline.runs").fetchone()["candidates_snapshot"]
    serialized = json.dumps(snapshot)
    assert "synthetic@example.invalid" not in serialized and "Synthetic Candidate" not in serialized
    assert "Python service" not in serialized and "provenance" not in serialized
    assert all(candidate["class_size"] == 3 and candidate["rating_score"] == 1 for candidate in snapshot)


def test_manual_idempotency_and_top_k_controls_survive_future_automatic_runs(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    key = uuid4()
    request = CombineRequest(top_k=2, idempotency_key=key)
    queued = combines.request(vacancy_id, request)
    assert queued.state == "queued"
    assert combines.request(vacancy_id, request).run_id == queued.run_id
    assert combines.request(vacancy_id, CombineRequest(top_k=2)).run_id == queued.run_id
    with pytest.raises(ConflictError, match="different request"):
        combines.request(vacancy_id, CombineRequest(top_k=3, idempotency_key=key))
    finish(combines)
    assert len(combines.selections(vacancy_id, 100, 0).items) == 2
    changed = eligible_profile(source_revision=2, city="Казань", work_types=["remote"])
    store.intake(candidates[0], changed)
    assert combines.status(vacancy_id).state == "obsolete"
    with pytest.raises(ConflictError):
        combines.selections(vacancy_id, 100, 0)
    assert worker(store).process_once()
    finish(combines)
    refreshed = combines.status(vacancy_id)
    assert refreshed.run_id != queued.run_id and refreshed.top_k == 2 and refreshed.state == "ready"
    assert combines.request(vacancy_id, request).run_id == queued.run_id
    assert combines.request(vacancy_id, request).state == "obsolete"


def test_persistent_exclusions_visited_terminal_pending_releasable(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    finish(combines)
    first = combines.status(vacancy_id).run_id
    combines.set_selection_state(vacancy_id, candidates[0], SelectionStateInput(state="visited"))
    combines.set_selection_state(vacancy_id, candidates[1], SelectionStateInput(state="pending"))
    with pytest.raises(ConflictError):
        combines.selections(vacancy_id, 100, 0)
    finish(combines)
    page = combines.selections(vacancy_id, 100, 0)
    assert page.run.run_id != first and [card.candidate_id for card in page.items] == [candidates[2]]
    assert page.run.filter_counts["selection_state_excluded"] == 2
    with pytest.raises(ConflictError, match="visited"):
        combines.set_selection_state(vacancy_id, candidates[0], SelectionStateInput(state="eligible"))
    combines.set_selection_state(vacancy_id, candidates[1], SelectionStateInput(state="eligible"))
    finish(combines)
    assert {card.candidate_id for card in combines.selections(vacancy_id, 100, 0).items} == set(candidates[1:])
    combines.set_selection_state(vacancy_id, candidates[2], SelectionStateInput(state="attractive"))
    finish(combines)
    assert [card.candidate_id for card in combines.selections(vacancy_id, 100, 0).items] == [candidates[1]]


def test_pool_withdrawal_during_compute_is_rejected_and_fresh_run_queued(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    combines.process_events()
    stale = combines.claim(30, 5)
    result = select_candidates(stale.vacancy, stale.candidates, stale.policy, stale.top_k, stale.excluded_ids)
    changed = eligible_profile(source_revision=2, work_types=["remote"])
    changed.consents.pool = False
    store.intake(candidates[0], changed)
    assert not combines.publish(stale, result)
    finish(combines)
    page = combines.selections(vacancy_id, 100, 0)
    assert page.run.run_id != stale.run_id and page.run.scanned_count == 2
    assert candidates[0] not in {card.candidate_id for card in page.items}


def test_vacancy_closed_during_compute_and_old_ready_event_cannot_restore(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    combines.process_events()
    stale = combines.claim(30, 5)
    changed = eligible_vacancy(source_revision=2, status="closed", owner_id=stale.vacancy.owner_id)
    VacancyStore(store.database_url).intake(vacancy_id, changed)
    result = select_candidates(stale.vacancy, stale.candidates, stale.policy, stale.top_k)
    assert not combines.publish(stale, result)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.outbox SET delivered_at=NULL WHERE event_type='vacancy.ready'")
        connection.execute("""INSERT INTO vacancy_pipeline.outbox(event_type,vacancy_id,revision,schema_version,payload)
            VALUES ('vacancy.ready',%s,1,1,'{}')""", (vacancy_id,))
    finish(combines)
    assert combines.status(vacancy_id).state == "obsolete"
    with pytest.raises(NotFoundError):
        combines.selections(vacancy_id, 100, 0)
    assert combines.claim(30, 5) is None


def test_stale_event_uses_current_authoritative_vacancy_revision(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    first = VacancyStore(store.database_url).ready_entry(vacancy_id)
    changed = eligible_vacancy(source_revision=2, zp_min=110000, owner_id=first.owner_id)
    VacancyStore(store.database_url).intake(vacancy_id, changed)
    run(VacancyStore(store.database_url)).process_once()
    # Both revision-1 and revision-2 events are still pending; delivery cannot restore revision 1.
    assert combines.process_events() == 6
    finish(combines)
    page = combines.selections(vacancy_id, 100, 0)
    assert page.run.vacancy_revision == 2


def test_generation_changes_before_events_dispatch_and_exact_pool_guard(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    finish(combines)
    before = combines.status(vacancy_id)
    # Simulate direct artifact corruption without a source event: exact live IDs still invalidate.
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (candidates[0],))
    assert combines.status(vacancy_id).state == "obsolete"
    with pytest.raises(ConflictError):
        combines.selections(vacancy_id, 100, 0)
    with connect(store.database_url) as connection:
        connection.execute("""INSERT INTO candidate_pipeline.outbox(event_type,candidate_id,revision,schema_version,payload)
            VALUES ('candidate.withdrawn',%s,1,1,'{}')""", (candidates[0],))
        assert connection.execute("SELECT pool_generation FROM matching_pipeline.settings").fetchone()["pool_generation"] > before.pool_generation
    finish(combines)
    assert combines.selections(vacancy_id, 100, 0).run.scanned_count == 2


def test_schema_extension_invalidates_batch_until_both_sources_rebuilt(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    finish(combines)
    store.extend_schema(CoordinateAddition(group="stack", key="rust"))
    assert combines.status(vacancy_id).state == "obsolete"
    with pytest.raises(NotFoundError):
        combines.selections(vacancy_id, 100, 0)
    candidate_worker = worker(store)
    while candidate_worker.process_once():
        pass
    assert run(VacancyStore(store.database_url)).process_once()
    finish(combines)
    assert combines.selections(vacancy_id, 100, 0).run.strict_schema_version == 2


def test_replay_and_two_dispatchers_are_idempotent(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _value: combines.process_events(), range(2)))
    assert sum(counts) == 4
    original = combines.status(vacancy_id).run_id
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.outbox SET delivered_at=NULL")
        connection.execute("UPDATE vacancy_pipeline.outbox SET delivered_at=NULL")
    assert combines.process_events() == 4
    assert combines.status(vacancy_id).run_id == original
    assert combines.delivery_status().acknowledged_count == 4
    finish(combines)
    assert combines.selections(vacancy_id, 100, 0).run.run_id == original


@pytest.mark.parametrize("batch_limit", [1, 10])
def test_vacancy_delivery_does_not_wait_for_candidate_backlog_drain(store: Store, batch_limit: int) -> None:
    combines, _vacancy_id, candidates = setup_pool(store, 1)
    with connect(store.database_url) as connection:
        connection.execute("""INSERT INTO candidate_pipeline.outbox(event_type,candidate_id,revision,schema_version,payload)
            SELECT 'candidate.ready',%s,1,1,'{}' FROM generate_series(1,%s)""", (candidates[0], batch_limit * 3))
    first = combines.process_events(batch_limit=batch_limit)
    assert first <= batch_limit
    if batch_limit == 1:
        # A different process resumes using durable inbox order, not an in-memory cursor.
        restarted = CombineStore(store.database_url)
        assert restarted.delivery_status().vacancy_pending == 1
        assert restarted.process_events(batch_limit=1) == 1
    status = combines.delivery_status()
    assert status.vacancy_pending == 0 and status.candidate_pending > 0
    assert status.acknowledged_count == (2 if batch_limit == 1 else batch_limit)


def test_failed_event_savepoint_continues_and_quarantine_manual_recovery(store: Store, monkeypatch) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    original = combines._deliver

    def poisoned(connection, source, event) -> None:
        if source == "candidate" and event["id"] == 1:
            connection.execute("INSERT INTO matching_pipeline.inbox(source,event_id) VALUES ('candidate',999)")
            raise ValueError("synthetic poison")
        original(connection, source, event)

    monkeypatch.setattr(combines, "_deliver", poisoned)
    assert combines.process_events(max_attempts=1, retry_base_seconds=0) == 3
    status = combines.delivery_status()
    assert status.quarantined_count == 1 and status.candidate_pending == 1 and status.acknowledged_count == 3
    with connect(store.database_url) as connection:
        assert not connection.execute("SELECT 1 FROM matching_pipeline.inbox WHERE event_id=999").fetchone()
    finish(combines)
    assert combines.selections(vacancy_id, 100, 0).run.state == "ready"
    monkeypatch.setattr(combines, "_deliver", original)
    combines.retry_event("candidate", 1)
    assert combines.process_events() == 1 and combines.delivery_status().quarantined_count == 0


def test_storage_error_rolls_back_ack_inbox_and_queue(store: Store, monkeypatch) -> None:
    combines, _vacancy_id, _ = setup_pool(store)
    original = combines._deliver

    def unavailable(connection, source, event) -> None:
        original(connection, source, event)
        raise psycopg.OperationalError("synthetic storage outage")

    monkeypatch.setattr(combines, "_deliver", unavailable)
    with pytest.raises(psycopg.OperationalError):
        combines.process_events()
    status = combines.delivery_status()
    assert status.candidate_pending == 3 and status.vacancy_pending == 1
    assert status.acknowledged_count == status.queued_count == 0
    monkeypatch.setattr(combines, "_deliver", original)
    finish(combines)


def test_event_retry_backoff_reaches_quarantine_without_blocking_other_rows(store: Store, monkeypatch) -> None:
    combines, _vacancy_id, _ = setup_pool(store)
    original = combines._deliver

    def poisoned(connection, source, event) -> None:
        if source == "candidate" and event["id"] == 1:
            raise ValueError("synthetic poison")
        original(connection, source, event)

    monkeypatch.setattr(combines, "_deliver", poisoned)
    assert combines.process_events(max_attempts=2, retry_base_seconds=60) == 3
    assert combines.delivery_status().retrying_count == 1
    assert combines.process_events(max_attempts=2, retry_base_seconds=60) == 0
    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.delivery_errors SET retry_at=now()")
    assert combines.process_events(max_attempts=2, retry_base_seconds=0) == 0
    assert combines.delivery_status().quarantined_count == 1


def test_partial_card_write_failure_rolls_back_entire_batch(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    combines.process_events()
    job = combines.claim(30, 5)
    result = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k)
    result.items[1].position = 1
    with pytest.raises(ValueError, match="snapshot"):
        combines.publish(job, result)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM matching_pipeline.selections").fetchone()["n"] == 0
    assert combines.status(vacancy_id).state == "processing"
    with pytest.raises(ConflictError):
        combines.selections(vacancy_id, 100, 0)
    corrected = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k)
    assert combines.publish(job, corrected)


def test_leases_backoff_bounded_failure_retry_and_old_token_guard(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    combines.process_events()
    old = combines.claim(30, 2)
    assert combines.heartbeat(old, 30)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.runs SET lease_until=now()-interval '1 second'")
    fresh = combines.claim(30, 2)
    assert fresh.lease_token != old.lease_token and fresh.attempts == 2
    result = select_candidates(old.vacancy, old.candidates, old.policy, old.top_k)
    assert not combines.publish(old, result)
    combines.fail(fresh, "synthetic_failure", 2, 0)
    assert combines.status(vacancy_id).state == "failed" and combines.claim(30, 2) is None
    assert combines.retry(vacancy_id).state == "queued"
    retry = combines.claim(30, 2)
    combines.fail(retry, "synthetic_failure", 2, 60)
    assert combines.status(vacancy_id).state == "retrying" and combines.claim(30, 2) is None
    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.runs SET available_at=now()")
    finish(combines)
    assert combines.status(vacancy_id).state == "ready"


def test_expired_final_attempt_becomes_failure(store: Store) -> None:
    combines, vacancy_id, _ = setup_pool(store)
    combines.process_events()
    combines.claim(30, 1)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.runs SET lease_until=now()-interval '1 second'")
    assert combines.claim(30, 1) is None
    assert combines.status(vacancy_id).state == "failed"
    assert combines.status(vacancy_id).error_code == "lease_exhausted"


def test_full_class_rank_ties_before_filter_and_over_500_snapshot(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store, 1)
    # Replicate already validated artifacts in the disposable DB to test SQL window/full-scan scaling.
    with connect(store.database_url) as connection:
        connection.execute("""INSERT INTO candidate_pipeline.candidates
            (candidate_id,revision,source_revision,profile_hash,profile,state,missing_requirements)
            SELECT md5('bulk-candidate-'||g)::uuid,1,1,profile_hash,profile,'ready','[]'::jsonb
            FROM candidate_pipeline.candidates CROSS JOIN generate_series(1,502) AS g WHERE candidate_id=%s""", (candidates[0],))
        connection.execute("""INSERT INTO candidate_pipeline.pool
            (candidate_id,revision,schema_version,schema_hash,specialization,grade,grade_rank,confirmed_strength,
             strict_vector,strict_evidence,semantic_vector,semantic_model_version,public_profile,public_pdf,owner_pdf)
            SELECT md5('bulk-candidate-'||g)::uuid,revision,schema_version,schema_hash,specialization,grade,grade_rank,
                confirmed_strength,strict_vector,strict_evidence,semantic_vector,semantic_model_version,public_profile,public_pdf,owner_pdf
            FROM candidate_pipeline.pool CROSS JOIN generate_series(1,502) AS g WHERE candidate_id=%s""", (candidates[0],))
        connection.execute("""UPDATE candidate_pipeline.candidates SET profile=jsonb_set(profile,'{work_types}', 'null')
            WHERE candidate_id=%s""", (candidates[0],))
        # One candidate is lower grade; tied higher rows share percentile regardless of UUID.
        connection.execute("UPDATE candidate_pipeline.pool SET grade='junior',grade_rank=1 WHERE candidate_id=%s", (candidates[0],))
    queued = combines.request(vacancy_id, CombineRequest(top_k=500))
    job = combines.claim(30, 5)
    assert len(job.candidates) == 503 and all(value.class_size == 503 for value in job.candidates)
    higher = [value for value in job.candidates if value.candidate_id != candidates[0]]
    assert {value.rating_score for value in higher} == {1.0}
    assert len({value.rank for value in higher}) == 502
    lower = next(value for value in job.candidates if value.candidate_id == candidates[0])
    assert lower.rating_score == 0 and lower.rank == 503
    result = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k)
    assert result.scanned_count == 503 and result.passed_count == 502 and len(result.items) == 500
    assert combines.publish(job, result)
    assert combines.selections(vacancy_id, 500, 0).run.run_id == queued.run_id


def test_ready_snapshot_omits_private_preferences_narratives_contacts(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store, 1)
    with connect(store.database_url) as connection:
        row = connection.execute("SELECT profile FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidates[0],)).fetchone()
        profile = row["profile"]
        profile.update(relocation_ready=True, relocation_cities=["Казань"], preferences=["private narrative secret"])
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=%s WHERE candidate_id=%s", (Jsonb(profile), candidates[0]))
    combines.request(vacancy_id, CombineRequest())
    job = combines.claim(30, 5)
    assert job.candidates[0].relocation_ready is True and job.candidates[0].relocation_cities == ["Казань"]
    serialized = json.dumps(job.candidates[0].model_dump(mode="json"))
    assert "private narrative secret" not in serialized and "synthetic@example.invalid" not in serialized


def test_routes_protected_not_ready_and_selection_state_handoff(store: Store) -> None:
    combines, vacancy_id, candidates = setup_pool(store)
    client = TestClient(create_app(store=store, combine_store=combines, internal_token="test", encoder_ready=lambda: True))
    headers = {"X-Internal-Token": "test"}
    assert client.get("/internal/matching/events").status_code == 401
    assert client.post(f"/internal/vacancies/{vacancy_id}/combine", json={"top_k": True}, headers=headers).status_code == 422
    queued = client.post(f"/internal/vacancies/{vacancy_id}/combine", json={"top_k": 2}, headers=headers)
    assert queued.status_code == 200 and queued.json()["state"] == "queued"
    assert client.get(f"/internal/vacancies/{vacancy_id}/selections", headers=headers).status_code == 409
    assert client.get("/ready").json()["checks"]["matching_database"] is True
    finish(combines)
    page = client.get(f"/internal/vacancies/{vacancy_id}/selections?limit=1&offset=1", headers=headers)
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    state = client.put(f"/internal/vacancies/{vacancy_id}/selection-state/{candidates[0]}", json={"state": "visited"}, headers=headers)
    assert state.status_code == 200 and state.json()["state"] == "visited"
    assert client.get(f"/internal/vacancies/{vacancy_id}/selections?limit=0", headers=headers).status_code == 422
    assert client.get("/internal/matching/events", headers=headers).status_code == 200
    assert client.post("/internal/matching/events/retry", json={"source": "candidate", "event_id": 0}, headers=headers).status_code == 422
