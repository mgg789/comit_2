"""PostgreSQL acceptance tests for immutable employer-feed sessions and feedback."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError, NotFoundError, Store
from test_combine_integration import finish, setup_pool
from test_contracts import eligible_profile
from test_pipeline_integration import worker

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


@pytest.fixture(autouse=True)
def cleanup_feed(store: Store):
    """Keep feed rows isolated while preserving the shared pipeline fixture."""
    yield
    with connect(store.database_url) as connection:
        connection.execute("""TRUNCATE feed_pipeline.visited_history,feed_pipeline.decisions,feed_pipeline.saved,
            feed_pipeline.visited,feed_pipeline.exposures,feed_pipeline.sessions CASCADE""")


def ready_feed(store: Store, count: int = 3) -> tuple[FeedStore, UUID, UUID]:
    """Build one real main and fallback rerank result from the existing pipeline fixture."""
    combines, vacancy_id, _ = setup_pool(store, count)
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert RerankWorker(reranks, retry_base_seconds=0).process_once()
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                      (vacancy_id,)).fetchone()["owner_id"]
    return FeedStore(store.database_url), vacancy_id, owner_id


def test_session_cursor_feedback_replay_and_single_exposure_action(store: Store) -> None:
    """Feedback removes one exposure while the immutable session keeps its cursor order."""
    feed, vacancy_id, owner_id = ready_feed(store)
    first = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    assert len(first.items) == 1 and first.items[0].served_at is not None
    exposure = first.items[0].exposure_id
    key = uuid4()
    decision = feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=exposure, action="dislike", idempotency_key=key))
    assert decision.visited and not decision.saved
    assert feed.receipt(owner_id, vacancy_id, key).replayed
    with pytest.raises(ConflictError, match="already has a decision"):
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=exposure, action="like", idempotency_key=uuid4()))
    continuation = feed.page(owner_id, vacancy_id, first.session_id, first.next_cursor, 10)
    assert exposure not in {item.exposure_id for item in continuation.items}


def test_owner_and_withdrawn_candidate_guards_are_scoped(store: Store) -> None:
    """Foreign owners cannot read sessions; a withdrawn card is suppressed individually."""
    feed, vacancy_id, owner_id = ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=3), vacancy_id)
    with pytest.raises(NotFoundError):
        feed.page(uuid4(), vacancy_id, session.session_id, None, 10)
    withdrawn = session.items[0].candidate_id
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (withdrawn,))
    page = feed.page(owner_id, vacancy_id, session.session_id, None, 10)
    assert withdrawn not in {item.candidate_id for item in page.items}


def test_single_saved_handoff_checks_owner_and_preserves_stale_membership(store: Store) -> None:
    """Recruitment can fetch only one liked member; withdrawal retains metadata without a card."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    with pytest.raises(NotFoundError):
        feed.saved_candidate(owner_id, vacancy_id, card.candidate_id)
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id, action="like", idempotency_key=uuid4()))
    assert feed.saved_candidate(owner_id, vacancy_id, card.candidate_id).card is not None
    with pytest.raises(NotFoundError):
        feed.saved_candidate(uuid4(), vacancy_id, card.candidate_id)
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (card.candidate_id,))
    saved = feed.saved_candidate(owner_id, vacancy_id, card.candidate_id)
    assert saved.candidate_id == card.candidate_id and saved.card is None


def test_legacy_visited_and_reconsider_keep_frozen_baseline(store: Store) -> None:
    """Legacy manual visited rows migrate without historic timestamps and refresh on N>K only."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(limit=1), vacancy_id).items[0]
    with connect(store.database_url) as connection:
        connection.execute("""INSERT INTO matching_pipeline.selection_states(vacancy_id,candidate_id,state)
            VALUES (%s,%s,'visited')""", (vacancy_id, card.candidate_id))
        assert feed.seed_legacy_manualvisited(connection) == 1
        migrated = connection.execute("SELECT profile_revision,visited_at,baseline_origin FROM feed_pipeline.visited").fetchone()
        assert migrated["visited_at"] is None and migrated["baseline_origin"] == "legacy_migration"
    # A changed ready profile is assessed by the parent candidate.ready transaction.
    assert feed.visited(owner_id, vacancy_id).items[0].profile_revision == card.profile_revision


def test_candidate_events_release_against_last_dislike_and_keep_history(store: Store) -> None:
    """Text edits retain the block; structural events release it once with a frozen baseline."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id, action="dislike", idempotency_key=uuid4()))
    profile = eligible_profile(source_revision=2, work_types=["remote"])
    profile.description = "Changed prose only"
    store.intake(card.candidate_id, profile)
    assert worker(store).process_once()
    combines = CombineStore(store.database_url)
    combines.process_events()
    visited = feed.visited(owner_id, vacancy_id).items[0]
    assert visited.state == "visited" and visited.profile_revision == 1 and visited.evaluated_revision == 2
    assert visited.assessment["distance"] == 0
    profile.source_revision = 3
    profile.grade_result.grade = "senior"
    store.intake(card.candidate_id, profile)
    assert worker(store).process_once()
    combines.process_events()
    visited = feed.visited(owner_id, vacancy_id).items[0]
    assert visited.state == "visited_updated" and visited.profile_revision == 1 and visited.evaluated_revision == 3
    assert visited.assessment["distance"] > visited.assessment["threshold"]
    assert "visited_updated" in visited.history_badges
    combines.process_events()
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.visited_history").fetchone()["n"] == 2
        assert feed.reconsider(connection, card.candidate_id) == 0


def test_saved_pdf_membership_survives_session_expiry_but_not_withdrawal(store: Store) -> None:
    """Saved exposure remains readable after TTL while its current source is mandatory."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id, action="like", idempotency_key=uuid4()))
    with connect(store.database_url) as connection:
        connection.execute("UPDATE feed_pipeline.sessions SET created_at=now()-interval '25 hours',expires_at=now()-interval '1 second'")
    assert feed.saved(owner_id, vacancy_id).items[0].card is not None
    assert feed.exposure(owner_id, vacancy_id, card.exposure_id).candidate_id == card.candidate_id
    with pytest.raises(ConflictError, match="expired"):
        feed.page(owner_id, vacancy_id, card.session_id, None)
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (card.candidate_id,))
    assert feed.saved(owner_id, vacancy_id).items[0].card is None
    with pytest.raises(ConflictError, match="stale or withdrawn"):
        feed.exposure(owner_id, vacancy_id, card.exposure_id)


def test_concurrent_decisions_and_conflicting_key_have_one_winner(store: Store) -> None:
    """Two tabs cannot apply different actions to one exposure or reuse its key for another action."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    keys = [uuid4(), uuid4()]
    def act(index: int):
        try:
            return feed.decision(owner_id, vacancy_id, FeedDecisionRequest(
                exposure_id=card.exposure_id, action="like" if index == 0 else "dislike", idempotency_key=keys[index]))
        except ConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(act, range(2)))
    winner = next(result for result in results if result is not None)
    assert sum(result is not None for result in results) == 1
    with pytest.raises(ConflictError, match="idempotency key conflicts"):
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=card.exposure_id,
            action="dislike" if winner.action == "like" else "like", idempotency_key=winner.idempotency_key))


def test_legacy_baseline_uses_current_source_revision_when_pool_lags(store: Store) -> None:
    """Migration provenance must pair the source snapshot with its revision, even before pool refresh."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    profile = eligible_profile(source_revision=2, work_types=["remote"])
    profile.grade_result.grade = "senior"
    store.intake(card.candidate_id, profile)
    with connect(store.database_url) as connection:
        connection.execute("INSERT INTO matching_pipeline.selection_states(vacancy_id,candidate_id,state) VALUES (%s,%s,'visited')",
                           (vacancy_id, card.candidate_id))
        assert feed.seed_legacy_manualvisited(connection) == 1
        baseline = connection.execute("SELECT profile_revision,baseline FROM feed_pipeline.visited").fetchone()
        assert baseline["profile_revision"] == 2
        with pytest.raises(ConflictError):
            feed._profile_snapshot(connection, card.candidate_id, 1)


def test_promotion_week_rollover_invalidates_existing_feed(store, monkeypatch):
    """A stored reward-bearing feed cannot survive the UTC reward week boundary."""
    from datetime import UTC, datetime, timedelta

    import matching_engine.fsp_boost as boost
    feed, vacancy_id, owner_id = ready_feed(store)
    session = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    class NextWeek(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=7)
    monkeypatch.setattr(boost, "datetime", NextWeek)
    with pytest.raises(ConflictError, match="model identity"):
        feed.page(owner_id, vacancy_id, session.session_id, session.next_cursor, 10)
