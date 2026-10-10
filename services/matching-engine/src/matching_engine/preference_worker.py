"""Restart-safe background preference fitting, without Harrier inference or external services."""

import logging
import threading

from matching_engine.preference_store import PreferenceStore

logger = logging.getLogger(__name__)


class PreferenceWorker:
    """Drain durable local training jobs; failed evaluations preserve the last valid pointer."""

    def __init__(self, store: PreferenceStore, *, lease_seconds: int = 300,
                 max_attempts: int = 5, retry_base_seconds: int = 30) -> None:
        """Bound claims/retries; independent processes coordinate solely through PostgreSQL."""
        if lease_seconds < 3 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid preference worker configuration")
        self.store = store
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def process_once(self) -> bool:
        """Reconcile revocations and process at most one leased fit with heartbeat protection."""
        self.store.reconcile()
        job = self.store.claim(self.lease_seconds, self.max_attempts)
        if job is None:
            return False
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(max(1, self.lease_seconds / 3)):
                try:
                    if not self.store.heartbeat(job, self.lease_seconds):
                        return
                except Exception:  # noqa: BLE001 - never log private observations or credentials.
                    logger.warning("preference heartbeat temporarily unavailable")

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            self.store.fit_and_publish(job)
        except Exception:  # noqa: BLE001 - durable retries use only a stable non-sensitive code.
            self.store.fail(job, "preference_fit_failed", self.max_attempts, self.retry_base_seconds)
        finally:
            stop.set()
            heartbeat.join(timeout=6)
        return True

    def run(self, stop: threading.Event, poll_seconds: float = 2.) -> None:
        """Resume work after restarts and stop gracefully without discarding durable jobs."""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while not stop.is_set():
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - source/storage outages retain jobs for a later poll.
                logger.warning("preference worker storage temporarily unavailable")
                worked = False
            if not worked:
                stop.wait(poll_seconds)
