"""Real PostgreSQL coverage of manual likes sharing the ordinary feed transition."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_store import CombineStore
from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.feed_models import FeedRequest, ShowcaseLikeRequest
from matching_engine.feed_store import FeedStore, ShowcaseMismatchError
from matching_engine.main import create_app
from matching_engine.preference_store import PreferenceStore
from matching_engine.preference_worker import PreferenceWorker
from matching_engine.store import ConflictError, NotFoundError
from test_combine_integration import finish, setup_pool
from test_feed_integration import ready_feed
from test_preference_live_integration import numeric_feed, react

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


def manual_pool(store):
    """Publish synthetic ready artifacts without running either ML selection stage."""
    combines, vacancy_id, candidates = setup_pool(store)
    with connect(store.database_url) as connection:
        owner = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s", (vacancy_id,)).fetchone()["owner_id"]
    return FeedStore(store.database_url), combines, owner, vacancy_id, candidates


def request(candidate, key=None, revision=1):
    """Build an exact source-revision operation with a synthetic retry key."""
    return ShowcaseLikeRequest(candidate_id=candidate, profile_revision=revision, idempotency_key=key or uuid4())


def test_manual_like_saved_handoff_and_combiner_exclusion_without_fake_runs(store):
    feed, combines, owner, vacancy, candidates = manual_pool(store)
    result = feed.showcase_like(owner, vacancy, request(candidates[0]))
    assert result.saved and not result.visited and result.selection_source == "showcase"
    saved = feed.saved_candidate(owner, vacancy, candidates[0])
    assert saved.card.kind == "showcase" and saved.card.mode == "manual"
    assert feed.exposure(owner, vacancy, result.exposure_id).candidate_id == candidates[0]
    with connect(store.database_url) as connection:
        session = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE source='showcase'").fetchone()
        assert session["main_run_id"] is None and session["rerank_run_id"] is None
        assert connection.execute("SELECT count(*) AS n FROM rerank_pipeline.runs").fetchone()["n"] == 0
        assert connection.execute("SELECT state FROM matching_pipeline.selection_states WHERE vacancy_id=%s AND candidate_id=%s", (vacancy, candidates[0])).fetchone()["state"] == "attractive"
    finish(combines)
    assert candidates[0] not in {item.candidate_id for item in combines.selections(vacancy, 100, 0).items}


def test_exact_retry_survives_withdrawal_and_conflicting_payload_fails(store):
    feed, _, owner, vacancy, candidates = manual_pool(store)
    operation = request(candidates[0])
    original = feed.showcase_like(owner, vacancy, operation)
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (candidates[0],))
        connection.execute("UPDATE vacancy_pipeline.vacancies SET business_status='closed' WHERE vacancy_id=%s", (vacancy,))
    repeated = feed.showcase_like(owner, vacancy, operation)
    assert repeated.replayed and repeated.receipt_id == original.receipt_id
    assert feed.showcase_receipt(owner, vacancy, operation.idempotency_key).receipt_id == original.receipt_id
    for changed in (request(candidates[1], operation.idempotency_key), request(candidates[0], operation.idempotency_key, 2)):
        with pytest.raises(ConflictError):
            feed.showcase_like(owner, vacancy, changed)
    with pytest.raises(NotFoundError):
        feed.showcase_receipt(uuid4(), vacancy, operation.idempotency_key)


def test_concurrent_same_key_and_distinct_keys_cannot_duplicate_membership(store):
    feed, _, owner, vacancy, candidates = manual_pool(store)
    operation = request(candidates[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: feed.showcase_like(owner, vacancy, operation), range(2)))
    assert len({result.receipt_id for result in responses}) == 1
    def like_other(_):
        try:
            return feed.showcase_like(owner, vacancy, request(candidates[1]))
        except ConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(like_other, range(2)))
    assert sum(result is not None for result in results) == 1
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.saved").fetchone()["n"] == 2
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.exposures").fetchone()["n"] == 2


def test_strict_salary_class_city_and_open_owner_guards(store):
    feed, _, owner, vacancy, candidates = manual_pool(store)
    with pytest.raises(NotFoundError):
        feed.showcase_like(uuid4(), vacancy, request(candidates[0]))
    with pytest.raises(ConflictError):
        feed.showcase_like(owner, vacancy, request(candidates[0], revision=2))
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET specialization='frontend' WHERE candidate_id=%s", (candidates[0],))
    with pytest.raises(ConflictError, match="strict"):
        feed.showcase_like(owner, vacancy, request(candidates[0]))
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET public_profile=jsonb_set(public_profile,'{salary}','{\"from_rub\":900000,\"to_rub\":1000000}') WHERE candidate_id=%s", (candidates[1],))
    with pytest.raises(ConflictError, match="strict"):
        feed.showcase_like(owner, vacancy, request(candidates[1]))
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.vacancies SET business_status='closed' WHERE vacancy_id=%s", (vacancy,))
    with pytest.raises(NotFoundError):
        feed.showcase_like(owner, vacancy, request(candidates[2]))


def test_existing_tinder_coexists_without_preference_control_changes(store):
    feed, vacancy, owner = ready_feed(store)
    session = feed.open(owner, FeedRequest(limit=3), vacancy)
    candidate = session.items[0].candidate_id
    with connect(store.database_url) as connection:
        before = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy,)).fetchone()
    feed.showcase_like(owner, vacancy, request(candidate))
    with connect(store.database_url) as connection:
        after = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy,)).fetchone()
        assert before == after
        assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.jobs").fetchone()["n"] == 0
        assert connection.execute("SELECT source FROM feed_pipeline.sessions WHERE session_id=%s", (session.session_id,)).fetchone()["source"] == "tinder"
    assert feed.saved_candidate(owner, vacancy, candidate).card.mode == "manual"
    finish(CombineStore(store.database_url))
    assert candidate not in {item.candidate_id for item in CombineStore(store.database_url).selections(vacancy, 100, 0).items}


@pytest.mark.parametrize("state", ["visited", "pending", "attractive"])
def test_manual_like_preserves_vacancy_local_blocking(store, state):
    feed, _, owner, vacancy, candidates = manual_pool(store)
    with connect(store.database_url) as connection:
        connection.execute("INSERT INTO matching_pipeline.selection_states(vacancy_id,candidate_id,state) VALUES(%s,%s,%s)", (vacancy, candidates[0], state))
    with pytest.raises(ConflictError, match="blocked"):
        feed.showcase_like(owner, vacancy, request(candidates[0]))


def test_manual_like_preserves_an_actually_fitted_active_learner(store):
    """Synthetic fitting proves manual source cannot reset the learned context."""
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    try:
        feed, vacancy, owner, session, rows, _ = numeric_feed(store)
        react(store, feed, vacancy, owner, session, rows, 0, 40)
        preferences = PreferenceStore(store.database_url)
        assert PreferenceWorker(preferences, retry_base_seconds=0).process_once()
        with connect(store.database_url) as connection:
            before = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy,)).fetchone()
            assert before["active_model_hash"] is not None
        liked = feed.showcase_like(owner, vacancy, request(rows[-1]["candidate_id"]))
        assert feed.saved_candidate(owner, vacancy, liked.candidate_id).card.mode == "manual"
        with connect(store.database_url) as connection:
            after = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy,)).fetchone()
            assert before == after
            assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.jobs WHERE state='queued'").fetchone()["n"] == 0
    finally:
        with connect(store.database_url) as connection:
            connection.execute("UPDATE learning_pipeline.settings SET source='operational'")


def test_lock_contention_is_bounded_retryable_and_atomic(store):
    """An unrelated global lock cannot occupy a manual request indefinitely."""
    feed, _, owner, vacancy, candidates = manual_pool(store)
    client = TestClient(create_app(store=store, feed_store=feed, internal_token="synthetic"))
    operation = request(candidates[0])
    with connect(store.database_url) as blocker:
        blocker.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
        response = client.post(f"/internal/vacancies/{vacancy}/showcase-likes", json=operation.model_dump(mode="json"),
                               headers={"X-Internal-Token": "synthetic", "X-Owner-ID": str(owner)})
        assert response.status_code == 503 and response.headers["retry-after"] == "1"
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.exposures").fetchone()["n"] == 0
    assert feed.showcase_like(owner, vacancy, operation).saved


def test_warning_override_binds_revisions_and_preserves_saved_replay(store):
    """Only suitability can be acknowledged; no exposure is written before confirmation."""
    feed, _, owner, vacancy, candidates = manual_pool(store)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET specialization='frontend' WHERE candidate_id=%s", (candidates[0],))
    operation = request(candidates[0])
    with pytest.raises(ShowcaseMismatchError) as warning:
        feed.showcase_like(owner, vacancy, operation)
    assert warning.value.warning.warnings == ["class_mismatch"]
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM feed_pipeline.exposures").fetchone()["n"] == 0
        connection.execute("UPDATE vacancy_pipeline.ready SET revision=revision+1 WHERE vacancy_id=%s", (vacancy,))
        connection.execute("UPDATE vacancy_pipeline.vacancies SET revision=revision+1 WHERE vacancy_id=%s", (vacancy,))
    old_ack = operation.model_copy(update={"mismatch_token": warning.value.warning.mismatch_token})
    with pytest.raises(ShowcaseMismatchError) as refreshed:
        feed.showcase_like(owner, vacancy, old_ack)
    assert refreshed.value.warning.mismatch_token != old_ack.mismatch_token
    acknowledged = operation.model_copy(update={"mismatch_token": refreshed.value.warning.mismatch_token})
    liked = feed.showcase_like(owner, vacancy, acknowledged)
    assert liked.strict_filter_warnings == ["class_mismatch"] and liked.mismatch_token == acknowledged.mismatch_token
    assert feed.saved_candidate(owner, vacancy, candidates[0]).card.strict_filter_warnings == ["class_mismatch"]
    assert feed.showcase_like(owner, vacancy, acknowledged).replayed
    with pytest.raises(ConflictError, match="idempotency"):
        feed.showcase_like(owner, vacancy, operation)
