"""Restart-safe local worker for durable employer feed refill jobs."""

from __future__ import annotations

import logging
import threading

from matching_engine.feed_refill import FeedRefillStore, RefillNotReady

logger = logging.getLogger(__name__)


class FeedRefillWorker:
    """Claim and append deterministic refill batches without an inference provider."""

    def __init__(self, store: FeedRefillStore, *, lease_seconds: int = 300,
                 max_attempts: int = 5, retry_base_seconds: int = 30) -> None:
        """Bound lease and retry behaviour so jobs survive process restarts."""
        if lease_seconds < 3 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid feed refill worker configuration")
        self.store = store
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def process_once(self) -> bool:
        """Process at most one claimed refill job and persist safe failures."""
        job = self.store.claim(self.lease_seconds, self.max_attempts)
        if job is None:
            return False
        try:
            self.store.publish(job)
        except RefillNotReady:
            logger.info("feed refill source is not ready; retrying")
            self.store.defer(job, "source_not_ready", self.retry_base_seconds)
        except Exception:  # noqa: BLE001 - persist only a stable safe code.
            logger.warning("feed refill temporarily unavailable")
            self.store.fail(job, "feed_refill_failed", self.max_attempts, self.retry_base_seconds)
        return True

    def run(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        """Poll until graceful shutdown; durable jobs remain after outages."""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while not stop.is_set():
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - database outage leaves jobs durable.
                logger.warning("feed refill storage temporarily unavailable")
                worked = False
            if not worked:
                stop.wait(poll_seconds)
