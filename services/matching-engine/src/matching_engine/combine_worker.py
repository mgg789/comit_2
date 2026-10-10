"""Restart-safe main selection and durable outbox dispatch, without an inference dependency."""

import logging
import threading

from matching_engine.combine_scoring import select_candidates
from matching_engine.combine_store import CombineStore

logger = logging.getLogger(__name__)


class CombineWorker:
    """Dispatch pending events and compute one immutable main-selection run outside SQL locks."""

    def __init__(self, store: CombineStore, *, lease_seconds: int = 300,
                 max_attempts: int = 5, retry_base_seconds: int = 30) -> None:
        """Bound leases/retries; independent processes coordinate through PostgreSQL only."""
        if lease_seconds < 3 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid worker lease/retry configuration")
        self.store = store
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def process_once(self) -> bool:
        """Drain a bounded event batch, then publish at most one complete current run."""
        dispatched = self.store.process_events(max_attempts=self.max_attempts,
                                               retry_base_seconds=self.retry_base_seconds)
        job = self.store.claim(self.lease_seconds, self.max_attempts)
        if job is None:
            return dispatched > 0
        stop_heartbeat = threading.Event()

        def renew() -> None:
            while not stop_heartbeat.wait(max(1, self.lease_seconds / 3)):
                try:
                    if not self.store.heartbeat(job, self.lease_seconds):
                        return
                except Exception:  # noqa: BLE001 - do not expose DSNs or private input data.
                    logger.warning("combine heartbeat temporarily unavailable")

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            result = select_candidates(job.vacancy, job.candidates, job.policy, job.top_k, job.excluded_ids)
            self.store.publish(job, result)
        except Exception:  # noqa: BLE001 - persist only a safe stable error code.
            self.store.fail(job, "main_selection_failed", self.max_attempts, self.retry_base_seconds)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=6)
        return True

    def run(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        """Resume durable work after outages/restarts until graceful shutdown is requested."""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while not stop.is_set():
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - storage failure leaves outbox and jobs durable.
                logger.warning("combine worker storage temporarily unavailable")
                worked = False
            if not worked:
                stop.wait(poll_seconds)
