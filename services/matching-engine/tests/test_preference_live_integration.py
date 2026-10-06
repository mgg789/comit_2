"""Real PostgreSQL scheduling, fitting and live suffix ordering on explicit numeric fixtures."""

import math
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest
from matching_engine.database import connect
from matching_engine.feed_models import FeedDecisionRequest, FeedRequest
from matching_engine.feed_store import FeedStore, _encode_cursor
from matching_engine.main import create_app
from matching_engine.models import CandidateProfile
from matching_engine.preference_model import apply
from matching_engine.preference_store import PreferenceStore
from matching_engine.preference_worker import PreferenceWorker
from matching_engine.rerank_activation import bootstrap_packaged, packaged_rollout
from matching_engine.rerank_hash import digest
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError, NotFoundError, Store
from psycopg.types.json import Jsonb
from test_combine_integration import finish, setup_pool
from test_pipeline_integration import (
    store as store,  # noqa: PLC0414 - shared disposable DB fixture.
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def synthetic_source(store):
    """Mark numeric actions as synthetic in this disposable test database only."""
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    yield
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='operational'")


def numeric_feed(store: Store, count: int = 64):
    """Seed full-schema numeric observations explicitly; these are not employer quality evidence."""
    combines, vacancy_id, candidates = setup_pool(store, count)
    combines.request(vacancy_id, CombineRequest(top_k=count))
    finish(combines)
    reranks = RerankStore(store.database_url)
    bootstrap_packaged(reranks)
    assert RerankWorker(reranks).process_once()
    artifact, _receipt = packaged_rollout()
    grade = artifact["feature_names"].index("grade")
    # Small counter-preference contrasts remain inside the bounded raw R0 scale.
    with connect(store.database_url) as connection:
        owner_id = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s", (vacancy_id,)).fetchone()["owner_id"]
        main_run = connection.execute("SELECT main_run_id FROM rerank_pipeline.runs WHERE state='ready'").fetchone()["main_run_id"]
        for index, candidate_id in enumerate(candidates):
            values = list(artifact["means"])
            values[grade] += (.8 if index % 2 == 0 else -.8) * artifact["scales"][grade]
            features = {"names": artifact["feature_names"], "values": values,
                        "available": [True] * len(values), "schema_hash": artifact["feature_schema_hash"]}
            score = math.fsum(weight * (value - mean) / scale for weight, value, mean, scale in zip(
                artifact["coefficients"], values, artifact["means"], artifact["scales"], strict=True))
            connection.execute("UPDATE learning_pipeline.samples SET features=%s,feature_hash=%s WHERE main_run_id=%s AND candidate_id=%s", (Jsonb(features), digest(features), main_run, candidate_id))
            row = connection.execute("SELECT run_id,card FROM rerank_pipeline.cards WHERE candidate_id=%s", (candidate_id,)).fetchone()
            row["card"]["rerank_score"] = score
            connection.execute("UPDATE rerank_pipeline.cards SET card=%s WHERE run_id=%s AND candidate_id=%s", (Jsonb(row["card"]), row["run_id"], candidate_id))
    feed = FeedStore(store.database_url)
    page = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    # Allocate a training prefix and an unserved suffix before any feedback.
    with connect(store.database_url) as connection:
        rows = connection.execute("SELECT * FROM feed_pipeline.exposures WHERE session_id=%s ORDER BY candidate_id", (page.session_id,)).fetchall()
        connection.execute("SET CONSTRAINTS feed_pipeline.exposures_session_id_position_key DEFERRED")
        for position, row in enumerate(rows, 1):
            connection.execute("UPDATE feed_pipeline.exposures SET position=%s,served_at=NULL WHERE exposure_id=%s", (position, row["exposure_id"]))
        connection.execute("DELETE FROM learning_pipeline.events WHERE event_type='delivered'")
    return feed, vacancy_id, owner_id, page.session_id, rows, artifact


def react(store, feed, vacancy_id, owner_id, session_id, rows, start: int, end: int):
    """Deliver one card at a time then use the real atomic feedback/enqueue path."""
    for index in range(start, end):
        cursor = None if index == 0 else _encode_cursor(index)
        page = feed.page(owner_id, vacancy_id, session_id, cursor, 1)
        assert len(page.items) == 1
        # Candidate parity defines the declared numeric counter-preference.
        positive = page.items[0].candidate_id.int % 2 == 0
        feed.decision(owner_id, vacancy_id, FeedDecisionRequest(exposure_id=page.items[0].exposure_id,
            action="like" if positive else "dislike", idempotency_key=uuid4()))
    return page


def test_real_fit_activation_suffix_and_rollback_preserve_receipts(store):
    """Actually fit a model, score remaining cards and rollback without moving delivered cards."""
    feed, vacancy_id, owner_id, session_id, rows, artifact = numeric_feed(store)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 39)
    preferences = PreferenceStore(store.database_url)
    assert preferences.status(owner_id, vacancy_id)["job_id"] is None
    react(store, feed, vacancy_id, owner_id, session_id, rows, 39, 40)
    queued = preferences.status(owner_id, vacancy_id)
    assert queued["job_state"] == "queued" and queued["eligible_reactions"] == 40
    with connect(store.database_url) as connection:
        delivered = connection.execute("SELECT exposure_id,position,card FROM feed_pipeline.exposures WHERE served_at IS NOT NULL ORDER BY position").fetchall()
    assert PreferenceWorker(preferences, retry_base_seconds=0).process_once()
    active = preferences.status(owner_id, vacancy_id)
    assert active["state"] == "active", active
    with connect(store.database_url) as connection:
        model = connection.execute("SELECT * FROM preference_pipeline.models WHERE model_hash=%s", (active["active_model_hash"],)).fetchone()
        report = connection.execute("SELECT plan,report FROM preference_pipeline.jobs WHERE job_id=%s", (model["job_id"],)).fetchone()
        assert len(report["plan"]["splits"]["train"]) == 24
        assert len(report["plan"]["splits"]["golden"]) == 8
        assert report["report"]["fit_performed"] and "model_full_json" not in report["report"]
    page = feed.page(owner_id, vacancy_id, session_id, _encode_cursor(40), 1)
    assert page.items[0].preference_state == "personalized"
    assert page.items[0].preference_model_hash == active["active_model_hash"]
    assert page.items[0].preference_adjustment == pytest.approx(sum(page.items[0].preference_contributions.values()))
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT exposure_id,position,card FROM feed_pipeline.exposures WHERE position<=40 ORDER BY position").fetchall() == delivered
        remaining = connection.execute("SELECT e.*,s.features FROM feed_pipeline.exposures e JOIN feed_pipeline.sessions f USING(session_id) JOIN learning_pipeline.samples s ON s.main_run_id=f.main_run_id AND s.candidate_id=e.candidate_id WHERE e.position>40 ORDER BY position").fetchall()
        expected = sorted(remaining, key=lambda row: (-apply(row["features"], row["card"]["rerank_score"], model["model"], artifact)["score"], -row["card"]["explanation"]["score"], str(row["candidate_id"])))
        assert [row["candidate_id"] for row in remaining] == [row["candidate_id"] for row in expected]
    key = uuid4()
    rolled = preferences.command(owner_id, vacancy_id, "rollback", key, active["revision"])
    assert rolled["enabled"] is False and rolled["active_model_hash"] is None
    assert preferences.command(owner_id, vacancy_id, "rollback", key, active["revision"])["revision"] == rolled["revision"]
    later = feed.page(owner_id, vacancy_id, session_id, _encode_cursor(41), 1)
    assert later.items[0].preference_state == "baseline" and later.order_revision > page.order_revision
    assert feed.exposure(owner_id, vacancy_id, page.items[0].exposure_id).preference_model_hash == active["active_model_hash"]
    reset = preferences.command(owner_id, vacancy_id, "reset", uuid4(), rolled["revision"])
    assert reset["enabled"] and reset["eligible_reactions"] == 0


def test_queue_idempotency_crash_reclaim_and_stale_worker(store):
    """Only one lease survives restart and duplicate reaction cannot create another job."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 42)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    preferences = PreferenceStore(store.database_url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: preferences.claim(30, 5), range(2)))
    old = next(job for job in claims if job)
    assert sum(job is not None for job in claims) == 1
    with connect(store.database_url) as connection:
        connection.execute("UPDATE preference_pipeline.jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s", (old["job_id"],))
    current = preferences.claim(30, 5)
    assert current["lease_token"] != old["lease_token"]
    assert not preferences.heartbeat(old, 30)
    assert not preferences.fit_and_publish(old)
    assert preferences.fit_and_publish(current)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.models").fetchone()["n"] == 1


def test_owner_auth_reset_and_incompatible_context(store):
    """HTTP controls enforce ownership, CAS and reset cutoff; context changes block old fitting."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 42)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    preferences = PreferenceStore(store.database_url)
    job = preferences.claim(30, 5)
    client = TestClient(create_app(store=store, internal_token="synthetic-test-token"))
    path = f"/internal/vacancies/{vacancy_id}/preferences"
    headers = {"X-Internal-Token": "synthetic-test-token", "X-Owner-ID": str(owner_id)}
    assert client.get(path).status_code == 401
    assert client.get(path, headers={**headers, "X-Owner-ID": str(uuid4())}).status_code == 404
    assert client.post(path + "/reset", headers=headers, json={"idempotency_key": str(uuid4()), "expected_revision": 99}).status_code == 409
    reset = client.post(path + "/reset", headers=headers, json={"idempotency_key": str(uuid4()), "expected_revision": 0})
    assert reset.status_code == 200 and reset.json()["eligible_reactions"] == 0
    assert not preferences.fit_and_publish(job)
    assert preferences.status(owner_id, vacancy_id)["active_model_hash"] is None


def test_corrupt_features_retry_is_sealed_and_bounded(store):
    """Snapshot corruption never activates; bounded failure stays observable and retryable."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 42)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.samples SET feature_hash='corrupt' WHERE candidate_id=%s", (rows[0]["candidate_id"],))
    preferences = PreferenceStore(store.database_url)
    assert PreferenceWorker(preferences, max_attempts=1, retry_base_seconds=0).process_once()
    status = preferences.status(owner_id, vacancy_id)
    assert status["job_state"] == "failed" and status["active_model_hash"] is None
    with pytest.raises(NotFoundError):
        preferences.status(uuid4(), vacancy_id)
    with pytest.raises(ConflictError):
        preferences.command(owner_id, vacancy_id, "rollback", uuid4(), 99)


def test_withdrawal_erases_model_and_source_only_revocation_blocks_runtime(store):
    """Fitted participant withdrawal removes coefficients/seals and falls back on undelivered cards."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 42)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    preferences = PreferenceStore(store.database_url)
    assert PreferenceWorker(preferences).process_once()
    assert preferences.status(owner_id, vacancy_id)["state"] == "active"
    candidate_id = rows[0]["candidate_id"]
    with connect(store.database_url) as connection:
        profile = CandidateProfile.model_validate(connection.execute("SELECT profile FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()["profile"])
    profile.source_revision += 1
    profile.consents.pool = False
    store.intake(candidate_id, profile)
    assert preferences.status(owner_id, vacancy_id)["active_model_hash"] is None
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT model,revoked_at FROM preference_pipeline.models").fetchone()["model"] is None
        assert connection.execute("SELECT plan,report FROM preference_pipeline.jobs").fetchone() == {"plan": None, "report": None}


def test_followup_fit_requires_sixteen_new_unique_candidates(store):
    """Continuous learning consumes fresh audit candidates rather than replaying its prior holdout."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 60)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    preferences = PreferenceStore(store.database_url)
    assert PreferenceWorker(preferences).process_once()
    initial = preferences.status(owner_id, vacancy_id)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 40, 55)
    assert preferences.status(owner_id, vacancy_id)["job_id"] == initial["job_id"]
    react(store, feed, vacancy_id, owner_id, session_id, rows, 55, 56)
    assert preferences.status(owner_id, vacancy_id)["job_id"] != initial["job_id"]
    assert PreferenceWorker(preferences).process_once()
    with connect(store.database_url) as connection:
        jobs = connection.execute("SELECT state,plan FROM preference_pipeline.jobs ORDER BY requested_at").fetchall()
        assert len(jobs) == 2 and all(job["state"] in ("ready", "rejected") for job in jobs)
        original = {row["candidate_key"] for split in ("train", "validation", "golden") for row in jobs[0]["plan"]["splits"][split]}
        fresh = {row["candidate_key"] for split in ("validation", "golden") for row in jobs[1]["plan"]["splits"][split]}
        assert len(fresh) == 16 and not original.intersection(fresh)


def test_source_only_revocation_and_fit_lock(store, monkeypatch):
    """Source updates cannot race fit/publication; later source-only revocation suppresses immediately."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 44)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    candidate_id = rows[0]["candidate_id"]
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT to_regclass('public.candidate_profiles') AS name").fetchone()["name"] is None
        connection.execute("CREATE TABLE public.candidate_profiles(candidate_id uuid PRIMARY KEY,processing_consent boolean NOT NULL,pool_consent boolean NOT NULL)")
        connection.execute("INSERT INTO public.candidate_profiles SELECT candidate_id,true,true FROM candidate_pipeline.candidates")
    try:
        import matching_engine.preference_store as module
        original_evaluate = module.evaluate

        def evaluate_under_consent_lock(*args, **kwargs):
            def revoke_attempt():
                with connect(store.database_url) as other:
                    other.execute("SET LOCAL lock_timeout='150ms'")
                    other.execute("UPDATE public.candidate_profiles SET pool_consent=false WHERE candidate_id=%s", (candidate_id,))
            with ThreadPoolExecutor(max_workers=1) as executor, pytest.raises(psycopg.errors.LockNotAvailable):
                executor.submit(revoke_attempt).result(timeout=3)
            return original_evaluate(*args, **kwargs)

        monkeypatch.setattr(module, "evaluate", evaluate_under_consent_lock)
        preferences = PreferenceStore(store.database_url)
        assert PreferenceWorker(preferences).process_once()
        assert preferences.status(owner_id, vacancy_id)["state"] == "active"
        page = feed.page(owner_id, vacancy_id, session_id, _encode_cursor(40), 1)
        assert page.items[0].preference_state == "personalized"
        with connect(store.database_url) as connection:
            connection.execute("UPDATE public.candidate_profiles SET pool_consent=false WHERE candidate_id=%s", (candidate_id,))
            assert connection.execute("SELECT (profile->'consents'->>'pool')::boolean AS consent FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()["consent"]
        assert preferences.status(owner_id, vacancy_id)["active_model_hash"] is None
        assert feed.page(owner_id, vacancy_id, session_id, _encode_cursor(41), 1).items[0].preference_state == "baseline"
        preferences.reconcile()
        with connect(store.database_url) as connection:
            assert connection.execute("SELECT model FROM preference_pipeline.models").fetchone()["model"] is None
    finally:
        with connect(store.database_url) as connection:
            connection.execute("DROP TABLE public.candidate_profiles")


def test_lease_expiry_during_fit_rejects_publication(store, monkeypatch):
    """Publication checks wall time after fitting, rather than the fit transaction's start time."""
    feed, vacancy_id, owner_id, session_id, rows, _artifact = numeric_feed(store, 42)
    react(store, feed, vacancy_id, owner_id, session_id, rows, 0, 40)
    preferences = PreferenceStore(store.database_url)
    job = preferences.claim(300, 5)
    import matching_engine.preference_store as module
    original_evaluate = module.evaluate

    def expire_after_fit(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        with connect(store.database_url) as connection:
            connection.execute("UPDATE preference_pipeline.jobs SET lease_until=clock_timestamp()-interval '1 microsecond' WHERE job_id=%s", (job["job_id"],))
        return result

    monkeypatch.setattr(module, "evaluate", expire_after_fit)
    assert not preferences.fit_and_publish(job)
    assert preferences.status(owner_id, vacancy_id)["active_model_hash"] is None
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM preference_pipeline.models").fetchone()["n"] == 0
