"""Restart-safe artifact generation; publication is guarded by database leases."""

import hashlib
import logging
import math
import threading
from collections.abc import Callable
from typing import Protocol

from matching_engine.embeddings import (
    EmbeddingUnavailable,
    HarrierEncoder,
    build_semantic_text,
)
from matching_engine.models import (
    CandidateProfile,
    Category,
    category_for,
    confirmed_strength,
)
from matching_engine.resume import render_resume
from matching_engine.showcase_projection import (
    public_profile as showcase_public_profile,
)
from matching_engine.store import Store
from matching_engine.strict import build_strict_vector
from matching_engine.strict_evidence import build_strict_evidence

logger = logging.getLogger(__name__)


class Encoder(Protocol):
    """Real inference interface; dependency injection is reserved for isolated tests."""

    model_version: str

    def encode(self, text: str) -> list[float]:
        """Encode semantic text as a normalized, finite 640-dimensional Harrier vector."""
        ...


def validate_embedding(vector: list[float]) -> None:
    """Reject wrong dimensions, NaNs and degenerate fake inference outputs."""
    if len(vector) != 640 or not all(math.isfinite(value) for value in vector):
        raise ValueError("invalid semantic vector dimensions or values")
    norm = math.sqrt(sum(value * value for value in vector))
    if abs(norm - 1.0) > 0.01:
        raise ValueError("semantic vector must have unit L2 norm")


class Worker:
    """One polling worker; independent processes coordinate exclusively through PG leases."""

    def __init__(self, store: Store, encoder: Encoder | None = None, *, lease_seconds: int = 300,
                 max_attempts: int = 5, retry_base_seconds: int = 30,
                 renderer: Callable[[CandidateProfile, Category, bool], bytes] | None = None) -> None:
        """Configure bounded retries; injected encoder/renderer are only for test contours."""
        if lease_seconds < 3 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid worker lease/retry configuration")
        self.store = store
        self.encoder = encoder if encoder is not None else HarrierEncoder()
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.renderer = renderer if renderer is not None else render_resume

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
                    logger.warning("candidate job heartbeat temporarily unavailable")

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            category = category_for(job.profile)
            strict = build_strict_vector(job.profile, job.schema)
            text = build_semantic_text(job.profile)
            cache_key = hashlib.sha256(
                (self.encoder.model_version + "\0" + text).encode()
            ).hexdigest()
            semantic = self.store.cached_embedding(cache_key, self.encoder.model_version)
            if semantic is None:
                semantic = self.encoder.encode(text)
                validate_embedding(semantic)
                self.store.cache_embedding(cache_key, self.encoder.model_version, semantic)
            validate_embedding(semantic)
            public_pdf = self.renderer(job.profile, category, True)
            owner_pdf = self.renderer(job.profile, category, False)
            if not public_pdf.startswith(b"%PDF-") or not owner_pdf.startswith(b"%PDF-"):
                raise ValueError("renderer did not produce PDF artifacts")
            assert job.profile.salary is not None
            public_profile = showcase_public_profile(job.profile, job.candidate_id)
            self.store.publish(
                job, category, confirmed_strength(job.profile), strict, semantic,
                self.encoder.model_version, public_profile, public_pdf, owner_pdf,
                build_strict_evidence(job.profile, job.schema),
            )
        except EmbeddingUnavailable:
            self.store.fail(job, "embedding_unavailable", self.max_attempts, self.retry_base_seconds)
        except Exception:  # noqa: BLE001 - isolate inference/PDF failures without exposing private text.
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
                logger.warning("candidate worker storage temporarily unavailable")
                worked = False
            if not worked:
                stop.wait(poll_seconds)
