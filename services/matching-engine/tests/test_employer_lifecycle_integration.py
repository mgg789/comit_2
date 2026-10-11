"""Real PostgreSQL employer graph cleanup, replay and stale-worker regression tests."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from uuid import uuid4

import pytest
from matching_engine.combine_store import CombineStore
from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError, Store
from matching_engine.vacancy_models import VacancyDeactivate
from matching_engine.vacancy_store import VacancyStore
from test_combine_integration import finish
from test_feed_integration import ready_feed
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import publish, run

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


def test_employer_delete_removes_active_graph_preserves_other_owner_and_candidates(store: Store) -> None:
    """Active vacancy graphs cascade; global pool and another owner graph survive."""
    feed, vacancy_id, owner_id = ready_feed(store, 2)
    session = feed.open(owner_id, FeedRequest(), vacancy_id)
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(
        exposure_id=session.items[0].exposure_id, action="like", idempotency_key=uuid4()))
    vacancies = VacancyStore(store.database_url)
    other_id, other_owner = uuid4(), uuid4()
    vacancies.intake(other_id, eligible_vacancy(owner_id=other_owner))
    assert run(vacancies).process_once()
    finish(CombineStore(store.database_url))
    rerank_worker = RerankWorker(RerankStore(store.database_url), retry_base_seconds=0)
    for _ in range(10):
        if not rerank_worker.process_once():
            break
    else:
        raise AssertionError("rerank worker did not become idle")
    other_session = FeedStore(store.database_url).open(other_owner, FeedRequest(), other_id)
    queued_id = uuid4()
    vacancies.intake(queued_id, eligible_vacancy(owner_id=owner_id))
    stale_job = vacancies.claim(30, 5)
    assert stale_job is not None and stale_job.vacancy_id == queued_id
    fence_id = uuid4()
    vacancies.deactivate(fence_id, VacancyDeactivate(owner_id=owner_id, source_revision=1, status="draft"))
    with connect(store.database_url) as connection:
        event_id = connection.execute("SELECT id FROM vacancy_pipeline.outbox WHERE vacancy_id=%s LIMIT 1",
                                      (vacancy_id,)).fetchone()["id"]
        connection.execute("INSERT INTO matching_pipeline.delivery_errors(source,event_id,attempts,retry_at,error_code) VALUES('vacancy',%s,1,now(),'synthetic')", (event_id,))
        main_runs = [row["run_id"] for row in connection.execute("SELECT run_id FROM matching_pipeline.runs WHERE vacancy_id=%s", (vacancy_id,)).fetchall()]
        rerank_runs = [row["run_id"] for row in connection.execute("SELECT run_id FROM rerank_pipeline.runs WHERE vacancy_id=%s", (vacancy_id,)).fetchall()]
        candidates = connection.execute("SELECT candidate_id FROM candidate_pipeline.pool ORDER BY candidate_id").fetchall()
    receipt = {"owner_id": str(owner_id), "deleted": True}
    assert vacancies.delete_employer(owner_id) == receipt
    assert vacancies.delete_employer(owner_id) == receipt
    assert not publish(vacancies, stale_job)
    with connect(store.database_url) as connection:
        for table in ("vacancy_pipeline.vacancies", "vacancy_pipeline.lifecycle_fences", "feed_pipeline.sessions",
                      "feed_pipeline.exposures", "feed_pipeline.saved", "learning_pipeline.events",
                      "learning_pipeline.cohorts", "preference_pipeline.controls", "hypothesis_pipeline.controls"):
            assert connection.execute(f"SELECT 1 FROM {table} WHERE owner_id=%s", (owner_id,)).fetchone() is None
        for table in ("vacancy_pipeline.jobs", "vacancy_pipeline.ready", "vacancy_pipeline.outbox",
                      "matching_pipeline.runs", "matching_pipeline.controls", "matching_pipeline.selection_states",
                      "rerank_pipeline.runs", "rerank_pipeline.controls", "rerank_pipeline.manual_requests"):
            assert connection.execute(f"SELECT 1 FROM {table} WHERE vacancy_id IN (%s,%s)", (vacancy_id, queued_id)).fetchone() is None
        assert connection.execute("SELECT 1 FROM matching_pipeline.selections WHERE run_id=ANY(%s)", (main_runs,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM rerank_pipeline.cards WHERE run_id=ANY(%s)", (rerank_runs,)).fetchone() is None
        for table in ("matching_pipeline.inbox", "matching_pipeline.delivery_errors"):
            assert connection.execute(f"SELECT 1 FROM {table} WHERE source='vacancy' AND event_id=%s", (event_id,)).fetchone() is None
        assert connection.execute("SELECT candidate_id FROM candidate_pipeline.pool ORDER BY candidate_id").fetchall() == candidates
        assert connection.execute("SELECT 1 FROM feed_pipeline.sessions WHERE session_id=%s", (other_session.session_id,)).fetchone()
        assert connection.execute("SELECT count(*) AS n FROM vacancy_pipeline.tombstones WHERE owner_id=%s", (owner_id,)).fetchone()["n"] == 3
    for replay_id in (vacancy_id, queued_id, fence_id, uuid4()):
        with pytest.raises(ConflictError, match="employer_deleted"):
            vacancies.intake(replay_id, eligible_vacancy(owner_id=owner_id, source_revision=100))
    with pytest.raises(ConflictError, match="employer_deleted"):
        vacancies.deactivate(uuid4(), VacancyDeactivate(owner_id=owner_id, source_revision=100, status="draft"))
    with pytest.raises(ConflictError, match="vacancy_deleted"):
        vacancies.intake(vacancy_id, eligible_vacancy(owner_id=other_owner, source_revision=100))


def test_employer_delete_absent_projection_still_fences_new_vacancies(store: Store) -> None:
    """Deleting before the first projection permanently prevents delayed creation."""
    vacancies = VacancyStore(store.database_url)
    owner_id = uuid4()
    assert vacancies.delete_employer(owner_id) == {"owner_id": str(owner_id), "deleted": True}
    with pytest.raises(ConflictError, match="employer_deleted"):
        vacancies.intake(uuid4(), eligible_vacancy(owner_id=owner_id))


def test_employer_delete_waits_for_pipeline_lock(store: Store) -> None:
    """Deletion cannot race a pipeline transaction which holds the common lifecycle lock."""
    vacancies = VacancyStore(store.database_url)
    owner_id = uuid4()
    started = Event()

    def delete() -> dict[str, object]:
        started.set()
        return vacancies.delete_employer(owner_id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with connect(store.database_url) as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            future = executor.submit(delete)
            assert started.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        assert future.result(timeout=5) == {"owner_id": str(owner_id), "deleted": True}
    with pytest.raises(ConflictError, match="employer_deleted"):
        vacancies.intake(uuid4(), eligible_vacancy(owner_id=owner_id))
