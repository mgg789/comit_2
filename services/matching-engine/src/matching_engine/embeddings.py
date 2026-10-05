"""Offline, pinned Harrier document embeddings; never substitute fake vectors."""

import importlib.util
import math
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from matching_engine.models import CandidateProfile
from matching_engine.privacy import sanitize_public_text

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

MODEL_ID = "microsoft/harrier-oss-v1-270m"
MODEL_REVISION = "31de22b673913c7d658c0f03f792d77c2dcf8ebd"
MODEL_VERSION = f"{MODEL_ID}@{MODEL_REVISION}:document:chunks1024:v1"
CHUNK_TOKENS = 1024


class EmbeddingUnavailable(RuntimeError):
    """Retryable failure to load or run the real, local model."""


def build_semantic_text(profile: CandidateProfile) -> str:
    """Serialize all narrative groups and preferences deterministically.

    Claims retain their verification state. Rejected claims are excluded;
    candidate identity and contact information are not model input.
    """
    parts = [f"Описание: {profile.description}"]
    for label, claims in (
        ("Проект", profile.projects),
        ("Опыт", profile.experience),
        ("Достижение", profile.achievements),
    ):
        for claim in claims:
            if claim.status != "rejected":
                parts.append(f"{label} [{claim.status}]: {claim.title}\n{claim.description}")
                # Only narrative fields contribute; opaque metadata/evidence
                # may contain contact info or machine-owned scores.
                for key in ("summary", "my_role", "role", "position", "stack", "duration"):
                    value = claim.details.get(key)
                    if isinstance(value, (str, list)):
                        parts.append(f"{key}: {value}")
    parts.extend(f"Предпочтение: {value}" for value in profile.preferences)
    return sanitize_public_text("\n\n".join(parts), profile)


class HarrierEncoder:
    """Lazy CPU encoder with bounded chunks and a pinned local HF snapshot.

    Runtime never downloads a model. Pre-provision the exact revision in the
    HF cache. Chunk embeddings are weighted by token count and L2-normalized.
    """

    model_version = MODEL_VERSION

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        """Check installation and exact cached files without running inference."""
        if importlib.util.find_spec("sentence_transformers") is None:
            return False
        hub = Path(os.environ.get("HF_HUB_CACHE", str(
            Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))) / "hub"
        )))
        snapshot = hub / "models--microsoft--harrier-oss-v1-270m" / "snapshots" / MODEL_REVISION
        return all((snapshot / name).is_file() for name in (
            "model.safetensors", "tokenizer.json", "modules.json", "config.json"
        ))

    def encode(self, text: str) -> list[float]:
        """Return exactly 640 finite unit-length floats or a retryable error."""
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer

            with self._lock:
                if self._model is None:
                    self._model = SentenceTransformer(
                        MODEL_ID, revision=MODEL_REVISION, device="cpu",
                        local_files_only=True, trust_remote_code=False,
                    )
                    self._model.max_seq_length = CHUNK_TOKENS + 8
                tokenizer = self._model.tokenizer
                tokens = tokenizer.encode(text, add_special_tokens=False)
                token_chunks = [tokens[i:i + CHUNK_TOKENS] for i in range(0, len(tokens), CHUNK_TOKENS)]
                if not token_chunks:
                    token_chunks = [tokens]
                chunks = [tokenizer.decode(chunk, skip_special_tokens=True) for chunk in token_chunks]
                vectors = self._model.encode(
                    chunks, batch_size=1, normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True,
                )
                pooled = np.average(vectors, axis=0, weights=[max(1, len(c)) for c in token_chunks])
                norm = float(np.linalg.norm(pooled))
                if norm == 0 or not math.isfinite(norm):
                    raise EmbeddingUnavailable("invalid_embedding")
                result = (pooled / norm).tolist()
                if len(result) != 640 or not all(math.isfinite(x) for x in result):
                    raise EmbeddingUnavailable("invalid_embedding")
                return result
        except EmbeddingUnavailable:
            raise
        except Exception:  # noqa: BLE001 - inference failures must not expose private document text.
            # Do not leak paths, document text, tokens or upstream exceptions.
            raise EmbeddingUnavailable("harrier_unavailable") from None
