"""Unit coverage for versioned N1 pool snapshots and refill contracts."""

from uuid import uuid4

import pytest
from matching_engine.feed_models import FeedPage, FeedRefillStatus
from matching_engine.feed_refill import (
    FEED_LOW_WATER,
    FEED_REFILL_BATCH,
    POOL_CHANGE_THRESHOLD,
    POOL_SNAPSHOT_VERSION,
    pool_change_fraction,
    pool_snapshot_hash,
)


def member(candidate_id: str, revision: int = 1) -> dict:
    """Build the public identity used by the N1 calculation."""
    return {"candidate_id": candidate_id, "profile_revision": revision, "schema_version": 1,
            "schema_hash": "schema", "specialization": "backend", "grade": "middle",
            "grade_rank": 2, "confirmed_strength": 3}


def test_pool_change_fraction_counts_union_added_removed_and_changed() -> None:
    first = [member("a"), member("b"), member("c")]
    current = [member("a"), member("b", revision=2), member("d")]
    assert pool_change_fraction(first, current) == pytest.approx(3 / 4)
    assert pool_change_fraction([], []) == 0
    assert POOL_CHANGE_THRESHOLD == pytest.approx(0.10)
    assert POOL_SNAPSHOT_VERSION == "pool-n1-v1"


def test_pool_snapshot_hash_is_order_stable_only_for_canonical_input() -> None:
    values = [member("a"), member("b")]
    assert pool_snapshot_hash(values) == pool_snapshot_hash(values)
    assert pool_snapshot_hash(values) != pool_snapshot_hash(list(reversed(values)))


def test_refill_defaults_match_lowwater_batch_contract() -> None:
    assert FEED_LOW_WATER == 4 and FEED_REFILL_BATCH == 12
    status = FeedRefillStatus(job_id=uuid4(), session_id=uuid4(), vacancy_id=uuid4(), reason="low_water",
                              state="queued", created_at=__import__("datetime").datetime.now())
    assert status.state == "queued"
    page = FeedPage(session_id=uuid4(), vacancy_id=uuid4(), vacancy_revision=1, policy_hash="p",
                    expires_at=__import__("datetime").datetime.now(), cursor=None, next_cursor=None,
                    has_more=True, items=[])
    assert page.refill_state == "active"
