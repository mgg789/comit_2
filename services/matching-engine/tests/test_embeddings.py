"""Semantic source coverage and real-model availability failures."""

import sys
from types import ModuleType

import pytest

from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    MODEL_VERSION,
    EmbeddingUnavailable,
    HarrierEncoder,
    build_semantic_text,
)
from matching_engine.models import CandidateProfile


def test_semantic_input_retains_unconfirmed_claims_and_all_narrative_groups() -> None:
    profile = CandidateProfile.model_validate({
        "source_revision": 1, "display_name": "Private Name", "email": "private@example.test",
        "phone": "+7 999 123 45 67", "description": "О себе private@example.test",
        "projects": [{"title": "Каталог", "description": "Python сервис", "status": "partial"}],
        "experience": [{"title": "Разработчик", "status": "self_reported"}],
        "achievements": [{"title": "Хакатон", "status": "confirmed"},
                         {"title": "Не принято", "status": "rejected"}],
        "preferences": ["Удалённая работа"],
    })
    text = build_semantic_text(profile)
    assert all(value in text for value in ("Каталог", "Python сервис", "Разработчик", "Хакатон", "Удалённая"))
    assert "Не принято" not in text
    assert "private@example.test" not in text


def test_model_unavailable_is_explicit_and_never_fake(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    encoder = HarrierEncoder()
    assert not encoder.is_available
    # Avoid probing the host HF cache and never fetch a model in tests.
    unavailable = ModuleType("sentence_transformers")
    def fail_load(*args, **kwargs):
        raise OSError("secret-or-private-path")
    unavailable.SentenceTransformer = fail_load
    monkeypatch.setitem(sys.modules, "sentence_transformers", unavailable)
    with pytest.raises(EmbeddingUnavailable, match="^harrier_unavailable$"):
        encoder.encode("test")


def test_vacancy_query_and_candidate_documents_share_space_but_not_cache_version() -> None:
    document = HarrierEncoder()
    query = HarrierEncoder(role="vacancy_query")
    assert document.model_version == MODEL_VERSION
    assert query.model_version != document.model_version
    assert document.embedding_space_id == query.embedding_space_id == EMBEDDING_SPACE_ID
    assert query.role == "vacancy_query"
    with pytest.raises(ValueError, match="unsupported embedding role"):
        HarrierEncoder(role="unknown")
