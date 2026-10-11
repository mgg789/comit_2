"""Isolated real-PostgreSQL tests; test-only inference never substitutes production Harrier."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_store import CombineStore
from matching_engine.database import PIPELINE_LOCK, connect, migrate
from matching_engine.embeddings import EmbeddingUnavailable
from matching_engine.main import create_app
from matching_engine.models import CoordinateAddition, category_for, confirmed_strength
from matching_engine.resume import has_current_resume_template, render_resume
from matching_engine.store import ConflictError, NotFoundError, Store
from matching_engine.strict import build_strict_vector
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.worker import Worker
from pypdf import PdfReader
from test_contracts import eligible_profile

pytestmark = pytest.mark.integration


@pytest.fixture
def store() -> Store:
    """Use a dedicated database only; PIPELINE_TEST_DATABASE_URL explicitly opts into destructive cleanup."""
    database_url = os.environ.get("PIPELINE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PIPELINE_TEST_DATABASE_URL is not configured (dedicated test database required)")
    migrate(database_url)
    with connect(database_url) as connection:
        if connection.execute("SELECT to_regclass('matching_pipeline.controls') AS matching").fetchone()["matching"]:
            # Referencing tables join the same TRUNCATE without touching unrelated schemas.
            matching_tables = "rerank_pipeline.settings,rerank_pipeline.manual_requests,rerank_pipeline.cards,rerank_pipeline.controls,rerank_pipeline.runs,rerank_pipeline.artifacts,matching_pipeline.manual_requests,matching_pipeline.selections,matching_pipeline.selection_states,matching_pipeline.controls,matching_pipeline.runs,matching_pipeline.inbox,matching_pipeline.delivery_errors,"
            connection.execute("UPDATE matching_pipeline.settings SET pool_generation=0")
        else:
            matching_tables = ""
        connection.execute("""
            TRUNCATE """ + matching_tables + """ vacancy_pipeline.outbox,vacancy_pipeline.jobs,vacancy_pipeline.ready,
                     vacancy_pipeline.vacancies,candidate_pipeline.outbox,candidate_pipeline.jobs,candidate_pipeline.pool,
                     candidate_pipeline.candidates,candidate_pipeline.embedding_cache
            RESTART IDENTITY CASCADE
        """)
        connection.execute("INSERT INTO rerank_pipeline.settings(singleton) VALUES(true)")
        connection.execute("UPDATE candidate_pipeline.settings SET active_schema_version=1")
        connection.execute("DELETE FROM candidate_pipeline.strict_schemas WHERE version>1")
    return Store(database_url)


class TestEncoder:
    """Explicit unit/integration test double; production worker uses actual Harrier."""

    __test__ = False
    model_version = "test-only:640:v1"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def encode(self, _text: str) -> list[float]:
        """Return a deterministic unit vector only in this isolated test contour."""
        self.calls += 1
        if self.fail:
            raise EmbeddingUnavailable("test-only unavailable")
        return [1.0] + [0.0] * 639


def renderer(_profile, _category, _public) -> bytes:
    """Artifact marker for storage race tests; PDF rendering has its own real tests."""
    return b"%PDF-1.4\n%test-only\n"


def worker(store: Store, encoder: TestEncoder | None = None, **kwargs) -> Worker:
    """Build an explicitly injected test worker with zero retry delay."""
    return Worker(store, encoder or TestEncoder(), renderer=renderer, retry_base_seconds=0, **kwargs)


def test_migrations_are_idempotent_and_checksum_guarded(store: Store) -> None:
    assert migrate(store.database_url) == []
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.migrations SET checksum='changed'")
    try:
        with pytest.raises(RuntimeError, match="applied migration changed"):
            migrate(store.database_url)
    finally:
        from hashlib import sha256
        from importlib.resources import files
        with connect(store.database_url) as connection:
            for migration in files("matching_engine").joinpath("migrations").iterdir():
                if migration.name.endswith(".sql"):
                    connection.execute("UPDATE candidate_pipeline.migrations SET checksum=%s WHERE name=%s",
                                       (sha256(migration.read_text().encode()).hexdigest(), migration.name))


def test_legacy_duplicate_version_alias_never_replays_sql(store: Store) -> None:
    """Upgrade the historical filename without replaying its non-idempotent DDL."""
    canonical = "012_hypothesis_exploration.sql"
    legacy = "011_hypothesis_exploration.sql"
    with connect(store.database_url) as connection:
        connection.execute("DELETE FROM candidate_pipeline.migrations WHERE name=%s", (legacy,))
        connection.execute("UPDATE candidate_pipeline.migrations SET name=%s WHERE name=%s", (legacy, canonical))
    assert migrate(store.database_url) == []
    with connect(store.database_url) as connection:
        rows = connection.execute("SELECT checksum FROM candidate_pipeline.migrations WHERE name=ANY(%s)", ([legacy, canonical],)).fetchall()
        assert len(rows) == 2 and rows[0]["checksum"] == rows[1]["checksum"]
        connection.execute("UPDATE candidate_pipeline.migrations SET checksum='invalid' WHERE name=%s", (legacy,))
    try:
        with pytest.raises(RuntimeError, match="applied migration changed"):
            migrate(store.database_url)
    finally:
        with connect(store.database_url) as connection:
            connection.execute("DELETE FROM candidate_pipeline.migrations WHERE name=%s", (legacy,))


def test_complete_pipeline_api_rank_and_contact_free_pool(store: Store) -> None:
    client = TestClient(create_app(store=store, internal_token="synthetic-token",
                                   encoder_ready=lambda: True))
    headers = {"X-Internal-Token": "synthetic-token"}
    ids = [UUID(int=index) for index in (2, 1, 3)]
    for candidate_id, grade in zip(ids, ("middle", "middle", "senior"), strict=True):
        profile = eligible_profile()
        profile.grade_result.grade = grade
        response = client.put(f"/internal/candidates/{candidate_id}/profile",
                              json=profile.model_dump(mode="json"), headers=headers)
        assert response.status_code == 200 and response.json()["state"] == "queued"
    run = worker(store)
    assert all(run.process_once() for _ in ids)
    response = client.get("/internal/pool", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["candidate_id"] for item in items] == [str(ids[2]), str(ids[1]), str(ids[0])]
    assert [item["rank"] for item in items] == [1, 2, 3]
    serialized = json.dumps(items)
    assert "synthetic@example.invalid" not in serialized and "Synthetic Candidate" not in serialized
    assert "provenance" not in serialized
    assert len(items[0]["strict_vector"]) == 37 and len(items[0]["semantic_vector"]) == 640
    assert client.get(items[0]["resume_url"], headers=headers).headers["content-type"] == "application/pdf"
    assert client.get("/ready").status_code == 200
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM candidate_pipeline.outbox").fetchone()["n"] == 3


def test_resume_lazily_upgrades_both_artifacts_without_changing_publication(store: Store, monkeypatch) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    assert worker(store).process_once()
    with connect(store.database_url) as connection:
        before = connection.execute("SELECT * FROM candidate_pipeline.pool WHERE candidate_id=%s",
                                    (candidate_id,)).fetchone()
    calls = []

    def counted(profile, category, public=True):
        calls.append(public)
        return render_resume(profile, category, public)

    monkeypatch.setattr("matching_engine.store.render_resume", counted)
    public_pdf = store.resume(candidate_id)
    owner_pdf = store.resume(candidate_id, public=False)
    assert calls == [True, False]
    assert has_current_resume_template(public_pdf) and has_current_resume_template(owner_pdf)
    public_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(public_pdf)).pages)
    owner_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(owner_pdf)).pages)
    assert "synthetic@example.invalid" not in public_text
    assert "Synthetic Candidate" not in public_text
    assert "synthetic@example.invalid" in owner_text
    assert "Synthetic Candidate" in owner_text
    assert store.resume(candidate_id) == public_pdf
    with connect(store.database_url) as connection:
        after = connection.execute("SELECT * FROM candidate_pipeline.pool WHERE candidate_id=%s",
                                   (candidate_id,)).fetchone()
        candidate = connection.execute("SELECT revision,state FROM candidate_pipeline.candidates WHERE candidate_id=%s",
                                       (candidate_id,)).fetchone()
        events = connection.execute("SELECT count(*) AS n FROM candidate_pipeline.outbox").fetchone()["n"]
    assert after["public_pdf"] == public_pdf and after["owner_pdf"] == owner_pdf
    for key in before.keys() - {"public_pdf", "owner_pdf"}:
        assert after[key] == before[key], key
    assert candidate == {"revision": before["revision"], "state": "ready"}
    assert events == 1


def test_concurrent_legacy_resume_gets_cache_one_upgrade(store: Store, monkeypatch) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    assert worker(store).process_once()
    calls = []

    def counted(profile, category, public=True):
        calls.append(public)
        return render_resume(profile, category, public)

    monkeypatch.setattr("matching_engine.store.render_resume", counted)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.resume, candidate_id) for _ in range(2)]
        artifacts = [future.result(timeout=10) for future in futures]
    assert artifacts[0] == artifacts[1]
    assert calls == [True, False]


@pytest.mark.parametrize("fence", ["revision", "state", "schema", "withdrawal"])
def test_lazy_resume_upgrade_rechecks_fences_after_waiting_for_lock(store: Store, monkeypatch, fence: str) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    assert worker(store).process_once()
    waiting = Event()

    class ObservedConnection:
        """Signal after the old snapshot read and before its advisory lock wait."""

        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, query, params=None):
            if "pg_advisory_xact_lock" in query:
                waiting.set()
            return self.connection.execute(query, params)

    monkeypatch.setattr("matching_engine.store.connect", lambda url: ObservedConnection(connect(url)))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with connect(store.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            future = executor.submit(store.resume, candidate_id)
            assert waiting.wait(5)
            if fence == "revision":
                connection.execute("UPDATE candidate_pipeline.candidates SET revision=revision+1 WHERE candidate_id=%s", (candidate_id,))
            elif fence == "state":
                connection.execute("UPDATE candidate_pipeline.candidates SET state='draft' WHERE candidate_id=%s", (candidate_id,))
            elif fence == "schema":
                connection.execute("""INSERT INTO candidate_pipeline.strict_schemas(version,schema_hash,coordinates)
                    SELECT 2,schema_hash || '-next',coordinates FROM candidate_pipeline.strict_schemas WHERE version=1""")
                connection.execute("UPDATE candidate_pipeline.settings SET active_schema_version=2")
            else:
                connection.execute("DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s", (candidate_id,))
        with pytest.raises(NotFoundError, match="published resume not found"):
            future.result(timeout=10)
    with connect(store.database_url) as connection:
        row = connection.execute("SELECT public_pdf,owner_pdf FROM candidate_pipeline.pool WHERE candidate_id=%s",
                                 (candidate_id,)).fetchone()
        if row:
            assert not has_current_resume_template(bytes(row["public_pdf"]))
            assert not has_current_resume_template(bytes(row["owner_pdf"]))


def test_drafts_persist_original_claims_without_jobs_or_publication(store: Store) -> None:
    candidate_id = uuid4()
    profile = eligible_profile(email_verified=False)
    status = store.intake(candidate_id, profile)
    assert status.state == "draft" and status.missing_requirements == ["email_verified"]
    assert store.pool(100, 0).items == []
    assert not worker(store).process_once()
    with connect(store.database_url) as connection:
        row = connection.execute("SELECT profile FROM candidate_pipeline.candidates").fetchone()
        assert row["profile"]["projects"][0]["details"]["my_role"] == "developer"
        assert row["profile"]["stacks"][0]["status"] == "partial"


def test_idempotent_source_revisions_and_stale_revocation_restore_rejected(store: Store) -> None:
    candidate_id = uuid4()
    profile = eligible_profile()
    assert store.intake(candidate_id, profile).profile_revision == 1
    assert store.intake(candidate_id, profile).profile_revision == 1
    changed = profile.model_copy(deep=True)
    changed.city = "Other"
    with pytest.raises(ConflictError):
        store.intake(candidate_id, changed)
    changed.source_revision = 2
    changed.consents.pool = False
    assert store.intake(candidate_id, changed).state == "draft"
    with pytest.raises(ConflictError):
        store.intake(candidate_id, profile)


def test_consent_withdrawal_invalidates_pool_pdfs_and_creates_event(store: Store) -> None:
    candidate_id = uuid4()
    profile = eligible_profile()
    store.intake(candidate_id, profile)
    worker(store).process_once()
    profile.source_revision = 2
    profile.consents.processing = False
    assert store.intake(candidate_id, profile).state == "draft"
    assert store.pool(100, 0).items == []
    with pytest.raises(NotFoundError):
        store.resume(candidate_id)
    with connect(store.database_url) as connection:
        assert connection.execute("""SELECT event_type FROM candidate_pipeline.outbox
                                     ORDER BY id DESC LIMIT 1""").fetchone()["event_type"] == "candidate.withdrawn"


def publish_job(store: Store, job) -> bool:
    """Invoke guarded storage publication directly for stale job race assertions."""
    return store.publish(
        job, category_for(job.profile), confirmed_strength(job.profile),
        build_strict_vector(job.profile, job.schema), [1.0] + [0.0] * 639, "test-only",
        {"display_name": "Candidate", "city": "City", "salary": job.profile.salary.model_dump()},
        renderer(None, None, None), renderer(None, None, None),
        build_strict_evidence(job.profile, job.schema),
    )


@pytest.mark.parametrize("grade_status", ["self_reported", "approved"])
def test_publish_persists_grade_status_across_pool_status_and_combine_candidates(
    store: Store, grade_status: str,
) -> None:
    """A published grade keeps its source evidence in every matching read model."""
    candidate_id = uuid4()
    profile = eligible_profile(
        grade_result=(
            {"grade": "middle", "status": "self_reported"}
            if grade_status == "self_reported"
            else eligible_profile().grade_result.model_dump(mode="json")
        ),
    )
    store.intake(candidate_id, profile)
    job = store.claim(lease_seconds=30, max_attempts=5)
    assert job is not None
    assert publish_job(store, job)

    with connect(store.database_url) as connection:
        row = connection.execute(
            "SELECT grade_status FROM candidate_pipeline.pool WHERE candidate_id=%s",
            (candidate_id,),
        ).fetchone()
        assert row is not None and row["grade_status"] == grade_status
        candidate = next(
            value for value in CombineStore._candidates(connection)
            if value.candidate_id == candidate_id
        )

    pool_entry = next(value for value in store.pool(100, 0).items if value.candidate_id == candidate_id)
    assert pool_entry.category.grade_status == grade_status
    assert store.status(candidate_id).category is not None
    assert store.status(candidate_id).category.grade_status == grade_status
    assert candidate.category.grade_status == grade_status


def test_stale_worker_cannot_publish_after_profile_change(store: Store) -> None:
    candidate_id = uuid4()
    profile = eligible_profile()
    store.intake(candidate_id, profile)
    stale = store.claim(30, 5)
    profile.source_revision = 2
    profile.city = "New city"
    store.intake(candidate_id, profile)
    assert not publish_job(store, stale)
    assert store.pool(100, 0).items == []
    worker(store).process_once()
    assert store.pool(100, 0).items[0].profile_revision == 2


def test_registry_extension_migrates_every_candidate_without_mixed_versions(store: Store) -> None:
    ready_id, queued_id, draft_id = uuid4(), uuid4(), uuid4()
    encoder = TestEncoder()
    store.intake(ready_id, eligible_profile())
    worker(store, encoder).process_once()
    store.intake(queued_id, eligible_profile())
    stale = store.claim(30, 5)
    store.intake(draft_id, eligible_profile(email_verified=False))
    schema = store.extend_schema(CoordinateAddition(group="stack", key="rust"))
    assert schema.version == 2 and len(schema.coordinates) == 38
    assert schema.coordinates[-1].index == 37
    assert not publish_job(store, stale)
    assert store.pool(100, 0).items == []
    with pytest.raises(ConflictError):
        store.extend_schema(CoordinateAddition(group="stack", key="rust"))
    run = worker(store, encoder)
    for _ in range(4):
        run.process_once()
    items = store.pool(100, 0).items
    assert len(items) == 2
    assert all(item.strict_schema_version == 2 and len(item.strict_vector) == 38 for item in items)
    assert all(item.strict_vector[-1] == 0 for item in items)
    assert store.status(draft_id).state == "draft"
    assert encoder.calls == 1  # Semantic text stayed identical: backfill used persistent cache.
    with connect(store.database_url) as connection:
        assert connection.execute("""SELECT count(*) AS n FROM candidate_pipeline.jobs
                                     WHERE schema_version=2""").fetchone()["n"] == 3


def test_embedding_outage_retries_terminal_failure_and_manual_recovery(store: Store) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    run = worker(store, TestEncoder(fail=True), max_attempts=2)
    run.process_once()
    assert store.status(candidate_id).state == "retrying"
    run.process_once()
    status = store.status(candidate_id)
    assert status.state == "failed" and status.error_code == "embedding_unavailable"
    assert status.attempt_count == 2 and not run.process_once()
    assert store.pool(100, 0).items == []
    assert store.retry(candidate_id).state == "queued"
    worker(store).process_once()
    assert store.status(candidate_id).state == "ready"


def test_expired_lease_is_recovered_and_old_token_cannot_publish(store: Store) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    old = store.claim(30, 3)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.jobs SET lease_until=now()-interval '1 second'")
    new = store.claim(30, 3)
    assert new.lease_token != old.lease_token and new.attempts == 2
    assert not publish_job(store, old)
    assert publish_job(store, new)


def test_expired_final_attempt_becomes_terminal_failure(store: Store) -> None:
    candidate_id = uuid4()
    store.intake(candidate_id, eligible_profile())
    job = store.claim(30, 1)
    assert store.heartbeat(job, 30)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.jobs SET lease_until=now()-interval '1 second'")
    assert store.claim(30, 1) is None
    assert store.status(candidate_id).state == "failed"
    assert store.status(candidate_id).error_code == "lease_exhausted"


def test_unknown_coordinates_rejected_before_database_write(store: Store) -> None:
    with pytest.raises(ValueError, match="unknown coordinate"):
        store.intake(uuid4(), eligible_profile(stacks=[{"key": "rust", "level": 6, "status": "pending"}]))
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM candidate_pipeline.candidates").fetchone()["n"] == 0
