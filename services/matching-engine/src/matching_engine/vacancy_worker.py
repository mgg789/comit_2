"""Restart-safe artifact generation; publication is guarded by database leases."""

import hashlib
import logging
import threading

from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    EmbeddingUnavailable,
    HarrierEncoder,
)
from matching_engine.vacancy_features import build_strict_features, semantic_text
from matching_engine.vacancy_store import VacancyStore
from matching_engine.worker import Encoder, validate_embedding

logger = logging.getLogger(__name__)


class VacancyWorker:
    """One polling worker; independent processes coordinate exclusively through PG leases."""

    def __init__(self, store: VacancyStore, encoder: Encoder | None = None, *, lease_seconds: int = 300,
                 max_attempts: int = 5, retry_base_seconds: int = 30) -> None:
        """Configure bounded retries; injected encoders are reserved for isolated tests."""
        if lease_seconds < 3 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid worker lease/retry configuration")
        self.store = store
        self.encoder = encoder if encoder is not None else HarrierEncoder(role="vacancy_query")
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def process_once(self) -> bool:
        """Process one job; errors persist safe codes without logging profile data."""
        job = self.store.claim(self.lease_seconds, self.max_attempts)
        if job is None:
            return False
        stop_heartbeat = threading.Event()

        def renew() -> None:
            while not stop_heartbeat.wait(max(1, self.lease_seconds / 3)):
                try:
                    if not self.store.heartbeat(job, self.lease_seconds):
                        return
                except Exception:  # noqa: BLE001 - logging exception content could expose profile/DSN.
                    # Completion still checks actual lease expiry in the database.
                    logger.warning("vacancy job heartbeat temporarily unavailable")

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            strict, mask = build_strict_features(job.profile, job.schema)
            text = semantic_text(job.profile)
            semantic = None
            model_version = None
            space_id = None
            if text is not None:
                model_version = self.encoder.model_version
                space_id = EMBEDDING_SPACE_ID
                cache_key = hashlib.sha256((model_version + "\0" + text).encode()).hexdigest()
                semantic = self.store.cached_embedding(cache_key, model_version)
                if semantic is None:
                    semantic = self.encoder.encode(text)
                    validate_embedding(semantic)
                    self.store.cache_embedding(cache_key, model_version, semantic)
                validate_embedding(semantic)
            self.store.publish(job, strict, mask, semantic, model_version, space_id)
        except EmbeddingUnavailable:
            self.store.fail(job, "embedding_unavailable", self.max_attempts, self.retry_base_seconds)
        except Exception:  # noqa: BLE001 - isolate inference failures without exposing private text.
            # Exception strings may include DSNs, upstream text or contacts: never expose them.
            self.store.fail(job, "artifact_generation_failed", self.max_attempts,
                            self.retry_base_seconds)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=6)
        return True

    def run(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        """Poll until stopped; database outages leave durable work intact for reconnect."""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        while not stop.is_set():
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - reconnect after any storage failure, never log DSNs.
                logger.warning("vacancy worker storage temporarily unavailable")
                worked = False
            if not worked:
                stop.wait(poll_seconds)
