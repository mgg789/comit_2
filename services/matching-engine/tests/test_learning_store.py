"""Real PostgreSQL tests of pre-feedback snapshots and scoped observation export."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_feed_integration import ready_feed

from matching_engine.database import connect
from matching_engine.feed_models import (
    FeedDecisionRequest,
    FeedImpressionRequest,
    FeedRequest,
)
from matching_engine.learning_store import LearningStore, pseudonym
from matching_engine.models import CandidateProfile
from matching_engine.store import ConflictError, NotFoundError, Store

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]
KEY = b"synthetic-test-only-pseudonym-key-32"


@pytest.fixture(autouse=True)
def synthetic_origin(store: Store):
    """Declare this isolated contour synthetic rather than infer origin from UUIDs."""
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='synthetic'")
    yield
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.settings SET source='operational'")


def read_rows(directory: Path) -> list[dict]:
    """Read the synthetic export only; no operational data is printed."""
    return [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]


def test_all_cards_snapshot_before_response_without_fabricated_views(store: Store, tmp_path: Path) -> None:
    """All top-k are captured, but only the returned page records delivery and no labels."""
    feed, vacancy_id, owner_id = ready_feed(store, 3)
    page = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM learning_pipeline.samples").fetchone()["n"] == 3
        events = connection.execute("SELECT event_type,count(*) AS n FROM learning_pipeline.events GROUP BY event_type").fetchall()
        assert {r["event_type"]: r["n"] for r in events} == {"allocated": 3, "delivered": 1}
    feed.page(owner_id, vacancy_id, page.session_id, None, 1)
    directory = tmp_path / "export"
    report = LearningStore(store.database_url).export(directory, KEY, source="synthetic")
    assert report["rows"] == 3 and report["explicit_decisions"] == 0
    assert report["observations_sha256"] == hashlib.sha256((directory / "observations.jsonl").read_bytes()).hexdigest()
    rows = read_rows(directory)
    assert all(exposure["label"] is None for row in rows for exposure in row["exposures"])
    assert sum(event["kind"] == "delivered" for row in rows for exposure in row["exposures"] for event in exposure["events"]) == 1
    assert sum(event["kind"] == "impression" for row in rows for exposure in row["exposures"] for event in exposure["events"]) == 0
    exported = (directory / "observations.jsonl").read_text()
    assert str(owner_id) not in exported and str(page.items[0].candidate_id) not in exported
    assert "resume_url" not in exported and "display_name" not in exported
    assert report["training_started"] is False


def test_impression_idempotency_scope_undelivered_and_explicit_action(store: Store, tmp_path: Path) -> None:
    """Only current delivered exposures receive first-view receipts, independent from feedback."""
    feed, vacancy_id, owner_id = ready_feed(store, 3)
    page = feed.open(owner_id, FeedRequest(limit=1), vacancy_id)
    card = page.items[0]
    request = FeedImpressionRequest(exposure_id=card.exposure_id, idempotency_key=uuid4())
    receipt = feed.impression(owner_id, vacancy_id, request)
    assert not receipt.replayed
    assert feed.impression(owner_id, vacancy_id, request).event_id == receipt.event_id
    assert feed.impression_receipt(owner_id, vacancy_id, request.idempotency_key).replayed
    with pytest.raises(NotFoundError):
        feed.impression(uuid4(), vacancy_id, request)
    with connect(store.database_url) as connection:
        unserved = connection.execute("SELECT exposure_id FROM feed_pipeline.exposures WHERE served_at IS NULL LIMIT 1").fetchone()["exposure_id"]
        original = connection.execute("SELECT feature_hash FROM learning_pipeline.samples WHERE candidate_id=%s", (card.candidate_id,)).fetchone()["feature_hash"]
    with pytest.raises(ConflictError, match="not been delivered"):
        feed.impression(owner_id, vacancy_id, FeedImpressionRequest(exposure_id=unserved, idempotency_key=uuid4()))
    with pytest.raises(ConflictError, match="another exposure"):
        feed.impression(owner_id, vacancy_id, FeedImpressionRequest(exposure_id=unserved, idempotency_key=request.idempotency_key))
    decision = FeedDecisionRequest(exposure_id=card.exposure_id, action="like", idempotency_key=uuid4())
    feed.decision(owner_id, vacancy_id, decision)
    assert feed.decision(owner_id, vacancy_id, decision).replayed
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT feature_hash FROM learning_pipeline.samples WHERE candidate_id=%s", (card.candidate_id,)).fetchone()["feature_hash"] == original
        assert connection.execute("SELECT count(*) AS n FROM learning_pipeline.events WHERE event_type='like'").fetchone()["n"] == 1
    LearningStore(store.database_url).export(tmp_path / "export", KEY, source="synthetic")
    observed = [e for row in read_rows(tmp_path / "export") for e in row["exposures"] if e["action"]]
    assert len(observed) == 1 and observed[0]["label"] == 1
    assert observed[0]["label_kind"] == "explicit_behavior" and observed[0]["propensity"] is None


def test_withdrawal_purges_features_and_blocks_new_observation(store: Store, tmp_path: Path) -> None:
    """Revocation destroys learning payloads, retains audit hashes, and excludes export rows."""
    feed, vacancy_id, owner_id = ready_feed(store, 1)
    card = feed.open(owner_id, FeedRequest(), vacancy_id).items[0]
    with connect(store.database_url) as connection:
        profile = CandidateProfile.model_validate(connection.execute("SELECT profile FROM candidate_pipeline.candidates WHERE candidate_id=%s", (card.candidate_id,)).fetchone()["profile"])
    profile.source_revision += 1
    profile.consents.pool = False
    store.intake(card.candidate_id, profile)
    with connect(store.database_url) as connection:
        sample = connection.execute("SELECT * FROM learning_pipeline.samples").fetchone()
        assert sample["features"] is None and sample["withdrawn_at"] is not None and sample["feature_hash"]
    with pytest.raises(ConflictError):
        feed.impression(owner_id, vacancy_id, FeedImpressionRequest(exposure_id=card.exposure_id, idempotency_key=uuid4()))
    assert LearningStore(store.database_url).export(tmp_path / "export", KEY, source="synthetic")["rows"] == 0


def test_snapshot_integrity_and_export_scope_fail_closed(store: Store, tmp_path: Path) -> None:
    """Corrupt snapshots and missing authoritative consent cannot become operational datasets."""
    ready_feed(store, 1)
    with connect(store.database_url) as connection:
        authority = connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]
    if not authority:
        with pytest.raises(ConflictError, match="authoritative"):
            LearningStore(store.database_url).export(tmp_path / "operational", KEY)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE learning_pipeline.samples SET feature_hash='invalid'")
    with pytest.raises(ConflictError, match="integrity"):
        LearningStore(store.database_url).export(tmp_path / "corrupt", KEY, source="synthetic")


def test_never_served_cards_are_captured_without_exposures_and_export_limit(store: Store, tmp_path: Path) -> None:
    """Rerank publication need not open a feed to make the top-k available to collection."""
    ready_feed(store, 3)
    report = LearningStore(store.database_url).export(tmp_path / "export", KEY, source="synthetic", limit=1)
    assert report["rows"] == 1 and report["truncated"] is True
    assert read_rows(tmp_path / "export")[0]["exposures"] == []


def test_pseudonym_is_domain_separated_and_private_key_required() -> None:
    """Stable grouping can identify cross-split overlap without exporting account UUIDs."""
    assert pseudonym(KEY, "candidate", "1") == pseudonym(KEY, "candidate", "1")
    assert pseudonym(KEY, "candidate", "1") != pseudonym(KEY, "owner", "1")
    with pytest.raises(ValueError):
        pseudonym(b"short", "candidate", "1")
