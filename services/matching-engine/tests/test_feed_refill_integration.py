"""Real PostgreSQL coverage for durable feed refill lineage and queue semantics."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_refill import FeedRefillStore, pool_snapshot
from matching_engine.feed_refill_worker import FeedRefillWorker
from matching_engine.feed_store import FeedStore
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError, Store
from test_combine_integration import finish, setup_pool
from test_contracts import eligible_profile
from test_pipeline_integration import worker
from test_vacancy_integration import run as vacancy_worker

pytest_plugins = ["test_pipeline_integration"]

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_feed(store: Store):
    """Isolate feed rows while retaining the shared candidate pipeline fixture."""
    with connect(store.database_url) as connection:
        connection.execute("""TRUNCATE feed_pipeline.visited_history,feed_pipeline.decisions,
            feed_pipeline.saved,feed_pipeline.visited,feed_pipeline.exposures,
            feed_pipeline.refill_jobs,feed_pipeline.batches,feed_pipeline.sessions CASCADE""")
    yield
    with connect(store.database_url) as connection:
        connection.execute("""TRUNCATE feed_pipeline.visited_history,feed_pipeline.decisions,
            feed_pipeline.saved,feed_pipeline.visited,feed_pipeline.exposures,
            feed_pipeline.refill_jobs,feed_pipeline.batches,feed_pipeline.sessions CASCADE""")


def _ready_feed(store: Store, count: int = 3) -> tuple[FeedStore, UUID, UUID]:
    """Publish a real main and rerank fallback pair for the test database."""
    combines, vacancy_id, _ = setup_pool(store, count)
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert RerankWorker(reranks, retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                      (vacancy_id,)).fetchone()["owner_id"]
    return FeedStore(store.database_url), vacancy_id, owner_id


def _reserve_tail(store: Store, vacancy_id: UUID) -> None:
    """Leave the first rerank card recommended and expose the rest as reserve."""
    with connect(store.database_url) as connection:
        run_id = connection.execute("SELECT current_run_id FROM rerank_pipeline.controls WHERE vacancy_id=%s",
                                    (vacancy_id,)).fetchone()["current_run_id"]
        connection.execute("""UPDATE rerank_pipeline.cards
            SET kind='reserve',card=card || jsonb_build_object('kind','reserve')
            WHERE run_id=%s AND position>1""", (run_id,))


def test_refill_concurrent_requests_share_one_durable_job(store: Store) -> None:
    """The partial unique index deduplicates force requests from concurrent tabs."""
    feed, vacancy_id, owner_id = _ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)

    def request() -> object:
        return feed.request_refill(owner_id, vacancy_id, session.session_id, force=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _index: request(), range(4)))
    ids = {status.job_id for status in statuses if status is not None}
    assert len(ids) == 1
    with connect(store.database_url) as connection:
        assert connection.execute("""SELECT count(*) AS n FROM feed_pipeline.refill_jobs
            WHERE session_id=%s AND state IN ('queued','running','retrying')""", (session.session_id,)).fetchone()["n"] == 1


def test_n1_snapshot_uses_only_strict_compatible_candidates(store: Store) -> None:
    """Pool change identity is measured after the vacancy strict filter."""
    remote = uuid4()
    onsite = uuid4()
    store.intake(remote, eligible_profile(work_types=["remote"]))
    store.intake(onsite, eligible_profile(work_types=["onsite"]))
    assert worker(store).process_once() and worker(store).process_once()
    from matching_engine.vacancy_store import VacancyStore
    from test_vacancy_contracts import eligible_vacancy
    vacancies = VacancyStore(store.database_url)
    vacancy_id = uuid4()
    vacancies.intake(vacancy_id, eligible_vacancy())
    assert vacancy_worker(vacancies).process_once()
    with connect(store.database_url) as connection:
        ready = CombineStore._vacancy(connection, vacancy_id)
        snapshot = pool_snapshot(connection, ready)
    assert {entry["candidate_id"] for entry in snapshot} == {str(remote)}


def test_refill_batch_uses_actual_main_and_rerank_lineage(store: Store) -> None:
    """Reserve refill cards come only from the coherent rerank tail and append order."""
    feed, vacancy_id, owner_id = _ready_feed(store)
    _reserve_tail(store, vacancy_id)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    first = session.items[0]
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=first.exposure_id,
                                                            action="dislike", idempotency_key=uuid4()))
    feed.page(owner_id, vacancy_id, session.session_id, session.next_cursor, 1)
    worker_refill = FeedRefillWorker(FeedRefillStore(store.database_url), retry_base_seconds=0)
    assert worker_refill.process_once()
    with connect(store.database_url) as connection:
        batch = connection.execute("""SELECT * FROM feed_pipeline.batches
            WHERE session_id=%s ORDER BY batch_number DESC LIMIT 1""", (session.session_id,)).fetchone()
        assert batch["batch_number"] == 2
        rerank = connection.execute("SELECT main_run_id FROM rerank_pipeline.runs WHERE run_id=%s",
                                    (batch["rerank_run_id"],)).fetchone()
        assert rerank["main_run_id"] == batch["main_run_id"]
        exposures = connection.execute("""SELECT e.position,e.candidate_id,e.card,
                rc.position AS rerank_position,rc.kind FROM feed_pipeline.exposures e
            JOIN rerank_pipeline.cards rc ON rc.run_id=e.source_rerank_run_id AND rc.candidate_id=e.candidate_id
            WHERE e.batch_id=%s ORDER BY e.position""", (batch["batch_id"],)).fetchall()
    assert exposures and all(row["position"] > first.position for row in exposures)
    assert all(row["kind"] == "reserve" and row["card"]["kind"] == "reserve" for row in exposures)


def test_exhaustion_does_not_requeue_empty_jobs_forever(store: Store) -> None:
    """A depleted session is terminal until its N1 union changes materially."""
    feed, vacancy_id, owner_id = _ready_feed(store, 1)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    card = session.items[0]
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
                                                            action="dislike", idempotency_key=uuid4()))
    feed.page(owner_id, vacancy_id, session.session_id, session.next_cursor, 1)
    from matching_engine.combine_worker import CombineWorker
    assert CombineWorker(CombineStore(store.database_url), retry_base_seconds=0).process_once()
    assert RerankWorker(RerankStore(store.database_url), retry_base_seconds=0).process_once()
    assert FeedRefillWorker(FeedRefillStore(store.database_url), retry_base_seconds=0).process_once()
    for _ in range(3):
        feed.page(owner_id, vacancy_id, session.session_id, None, 1)
    with connect(store.database_url) as connection:
        states = connection.execute("SELECT state FROM feed_pipeline.refill_jobs WHERE session_id=%s",
                                   (session.session_id,)).fetchall()
    assert states and all(row["state"] == "ready" for row in states)


def test_saved_and_visited_keysets_are_scoped_and_revision_stable(store: Store) -> None:
    """Saved and visited listings use timestamp/id keysets and reject source drift."""
    feed, vacancy_id, owner_id = _ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=3), vacancy_id)
    for card in session.items[:2]:
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
                                                                action="like", idempotency_key=uuid4()))
    saved = feed.saved(owner_id, vacancy_id, limit=1)
    assert saved.next_cursor and saved.offset == 0
    saved_tail = feed.saved(owner_id, vacancy_id, limit=1, cursor=saved.next_cursor)
    assert saved_tail.items and saved_tail.items[0].candidate_id != saved.items[0].candidate_id
    with pytest.raises(ValueError, match="offset"):
        feed.saved(owner_id, vacancy_id, limit=1, offset=1, cursor=saved.next_cursor)
    with pytest.raises(ValueError, match="invalid list cursor"):
        feed.saved(owner_id, vacancy_id, cursor="a" * 1025)

    # The same keyset carries its list kind and vacancy/owner binding.
    with pytest.raises(ValueError, match="invalid list cursor"):
        feed.visited(owner_id, vacancy_id, cursor=saved.next_cursor)

    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.selection_states SET state='visited' WHERE vacancy_id=%s",
                           (vacancy_id,))
        assert feed.seed_legacy_manualvisited(connection) == 2
    visited = feed.visited(owner_id, vacancy_id, limit=1)
    assert visited.next_cursor
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.vacancies SET revision=revision+1 WHERE vacancy_id=%s", (vacancy_id,))
    with pytest.raises(ConflictError, match="listing source changed"):
        feed.visited(owner_id, vacancy_id, limit=1, cursor=visited.next_cursor)


def test_refill_lease_recovery_does_not_allow_old_worker_publish(store: Store) -> None:
    """An expired worker lease can be reclaimed, while its old token is rejected."""
    feed, vacancy_id, owner_id = _ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    refill = FeedRefillStore(store.database_url)
    assert feed.request_refill(owner_id, vacancy_id, session.session_id, force=True)
    first = refill.claim(lease_seconds=3, max_attempts=5)
    assert first is not None
    with connect(store.database_url) as connection:
        connection.execute("UPDATE feed_pipeline.refill_jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s",
                           (first.job_id,))
    second = refill.claim(lease_seconds=3, max_attempts=5)
    assert second is not None and second.job_id == first.job_id and second.lease_token != first.lease_token
    assert not refill.publish(first)
    refill.defer(second, "source_not_ready", 0)


def test_refill_job_becomes_stale_when_vacancy_closes(store: Store) -> None:
    """A closed vacancy cannot receive a tail batch from a previously queued job."""
    feed, vacancy_id, owner_id = _ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    assert feed.request_refill(owner_id, vacancy_id, session.session_id, force=True)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.vacancies SET business_status='closed' WHERE vacancy_id=%s",
                           (vacancy_id,))
    assert FeedRefillWorker(FeedRefillStore(store.database_url), retry_base_seconds=0).process_once()
    status = feed.refill_status(owner_id, vacancy_id, session.session_id)
    assert status is not None and status.state == "stale" and status.error_code == "session_source_changed"


def test_low_water_queues_new_main_for_unshown_candidates_beyond_initial_top_k(store: Store) -> None:
    """A 12-card initial run can append candidates 13..24 from a fresh coherent pair."""
    combines, vacancy_id, _ = setup_pool(store, 25)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.controls SET top_k=12,revision=revision+1 WHERE vacancy_id=%s",
                           (vacancy_id,))
    combines.request(vacancy_id, CombineRequest(top_k=12))
    finish(combines)
    assert RerankWorker(RerankStore(store.database_url), retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                      (vacancy_id,)).fetchone()["owner_id"]
    feed = FeedStore(store.database_url)
    session = feed.open(owner_id, FeedRequest(limit=100), vacancy_id)
    initial_ids = {card.candidate_id for card in session.items}
    assert len(initial_ids) == 12
    for card in session.items:
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
                                                                action="dislike", idempotency_key=uuid4()))
    feed.page(owner_id, vacancy_id, session.session_id, None, 1)
    from matching_engine.combine_worker import CombineWorker
    assert CombineWorker(combines, retry_base_seconds=0).process_once()
    assert RerankWorker(RerankStore(store.database_url), retry_base_seconds=0).process_once()
    assert FeedRefillWorker(FeedRefillStore(store.database_url), retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        batch = connection.execute("""SELECT * FROM feed_pipeline.batches
            WHERE session_id=%s ORDER BY batch_number DESC LIMIT 1""", (session.session_id,)).fetchone()
        appended = {row["candidate_id"] for row in connection.execute(
            "SELECT candidate_id FROM feed_pipeline.exposures WHERE batch_id=%s", (batch["batch_id"],))}
        rerank = connection.execute("SELECT main_run_id FROM rerank_pipeline.runs WHERE run_id=%s",
                                    (batch["rerank_run_id"],)).fetchone()
    assert batch["batch_number"] == 2 and appended and appended.isdisjoint(initial_ids)
    assert rerank["main_run_id"] == batch["main_run_id"]


def test_mismatched_new_main_old_rerank_is_retried_without_mixed_batch(store: Store) -> None:
    """A queued fresh main plus an old ready rerank never publishes mixed lineage."""
    feed, vacancy_id, owner_id = _ready_feed(store, 3)
    session = feed.open(owner_id, FeedRequest(limit=100), vacancy_id)
    for card in session.items:
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
                                                                action="dislike", idempotency_key=uuid4()))
    feed.page(owner_id, vacancy_id, session.session_id, None, 1)
    refill = FeedRefillStore(store.database_url)
    assert FeedRefillWorker(refill, retry_base_seconds=0).process_once()
    status = feed.refill_status(owner_id, vacancy_id, session.session_id)
    assert status is not None and status.state == "retrying" and status.error_code == "source_not_ready"
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.batches WHERE session_id=%s",
                                 (session.session_id,)).fetchone()["n"] == 1


def test_mixed_incompatible_pool_snapshot_keeps_eligible_session_reserve(store: Store) -> None:
    """A mixed all-pool main snapshot stays coherent while N1 counts strict members only."""
    combines, vacancy_id, _ = setup_pool(store, 3)
    incompatible_id = uuid4()
    profile = eligible_profile()
    profile.salary.from_rub = 300000
    profile.salary.to_rub = 350000
    store.intake(incompatible_id, profile)
    assert worker(store).process_once()
    finish(combines)
    assert RerankWorker(RerankStore(store.database_url), retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                      (vacancy_id,)).fetchone()["owner_id"]
    feed = FeedStore(store.database_url)
    _reserve_tail(store, vacancy_id)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    first = session.items[0]
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=first.exposure_id,
                                                            action="dislike", idempotency_key=uuid4()))
    feed.page(owner_id, vacancy_id, session.session_id, session.next_cursor, 1)
    assert FeedRefillWorker(FeedRefillStore(store.database_url), retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        batch = connection.execute("""SELECT * FROM feed_pipeline.batches
            WHERE session_id=%s ORDER BY batch_number DESC LIMIT 1""", (session.session_id,)).fetchone()
    assert batch["batch_number"] == 2
