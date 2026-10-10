"""Real PostgreSQL search fixtures; injected vectors are explicitly test-only."""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from matching_engine.database import connect
from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    MODEL_VERSION,
    EmbeddingUnavailable,
)
from matching_engine.showcase_models import SearchRequest
from matching_engine.showcase_projection import public_profile
from matching_engine.showcase_store import ShowcaseStore
from matching_engine.store import Store
from psycopg.types.json import Jsonb
from test_contracts import eligible_profile
from test_pipeline_integration import worker

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


class FixtureQueryEncoder:
    """Synthetic deterministic encoder, never selected by production configuration."""
    model_version = "fixture-query-only"
    embedding_space_id = EMBEDDING_SPACE_ID

    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.calls = 0

    def encode(self, text):
        self.calls += 1
        if self.unavailable:
            raise EmbeddingUnavailable("fixture-only")
        return [1.0] + [0.0] * 639


@pytest.fixture
def showcase(store: Store):
    with connect(store.database_url) as connection:
        connection.execute("TRUNCATE candidate_pipeline.search_embeddings")
    for index, text in ((1, "Разработчик Python распределенные серверные системы"), (2, "Дизайнер интерфейсов"), (3, "Python инженер")):
        profile = eligible_profile(description=text, work_types=["remote"] if index != 2 else ["onsite"],
            relocation_ready=index == 1, stacks=[{"key": "python", "level": index + 2, "status": "rejected" if index == 2 else "confirmed" if index == 1 else "self_reported"}])
        store.intake(UUID(int=index), profile)
        assert worker(store).process_once()
        with connect(store.database_url) as connection:
            connection.execute("UPDATE candidate_pipeline.pool SET public_profile=%s,semantic_model_version=%s WHERE candidate_id=%s",
                (Jsonb(public_profile(profile, UUID(int=index))), MODEL_VERSION, UUID(int=index)))
    return ShowcaseStore(store.database_url, FixtureQueryEncoder()), uuid4()


def test_browse_current_profile_privacy_and_global_ranks(showcase):
    search, owner = showcase
    page = search.search(owner, SearchRequest())
    assert page.mode == "browse" and page.total == 3 and len(page.items) == 3
    assert {card.rank for card in page.items} == {1, 2, 3}
    detail = search.profile(owner, UUID(int=1)).model_dump(mode="json")
    assert detail["public_profile"]["description"].startswith("Разработчик")
    assert "email" not in detail["public_profile"] and "semantic_vector" not in detail
    assert "revision=1" in detail["resume_url"]


def test_lexical_fallback_hybrid_rrf_and_cache_when_encoder_off(showcase):
    search, owner = showcase
    search.encoder.unavailable = True
    lexical = search.search(owner, SearchRequest(q="Python"))
    assert lexical.mode == "lexical" and lexical.total == 3 and lexical.fallback_reason == "harrier_unavailable"
    assert search.search(owner, SearchRequest(q="серверные")).total == 1
    search.encoder.unavailable = False
    hybrid = search.search(owner, SearchRequest(q="Python"))
    assert hybrid.mode == "hybrid" and hybrid.total == 3
    factors = next(card.search_factors for card in hybrid.items if card.candidate_id == UUID(int=1))
    assert factors.lexical_rank is not None and factors.semantic_rank is not None
    assert factors.rrf_score == pytest.approx(1 / (60 + factors.lexical_rank) + 1 / (60 + factors.semantic_rank))
    calls = search.encoder.calls
    search.encoder.unavailable = True
    assert search.search(owner, SearchRequest(q="Python")).mode == "hybrid"
    assert search.encoder.calls == calls


def test_filters_before_channels_claims_salary_inclusive_and_relocation(showcase):
    search, owner = showcase
    filters = {"classes": ["backend"], "grades": ["middle"], "cities": ["москва"], "salary": {"from_rub": 150000, "to_rub": 200000},
        "work_types": ["remote"], "groups": {"stacks": [{"key": "python", "min_level": 3}]}}
    page = search.search(owner, SearchRequest(q="Python", filters=filters))
    assert {card.candidate_id for card in page.items} == {UUID(int=1), UUID(int=3)}
    assert all(card.search_factors.semantic_rank <= 2 for card in page.items)
    filters["groups"]["stacks"][0]["statuses"] = ["confirmed"]
    assert [card.candidate_id for card in search.search(owner, SearchRequest(filters=filters)).items] == [UUID(int=1)]
    filters["salary"]["from_rub"] = 150001
    assert search.search(owner, SearchRequest(filters=filters)).total == 0
    assert search.search(owner, SearchRequest(filters={"relocation_ready": True})).total == 1
    assert search.search(owner, SearchRequest(filters={"relocation_ready": False})).total == 2
    assert search.search(owner, SearchRequest(filters={"groups": {"stacks": [{"key": "python", "min_level": 6}]}})).total == 0


def test_cursor_rejects_source_query_owner_and_mode_drift(showcase):
    search, owner = showcase
    first = search.search(owner, SearchRequest(limit=1))
    second = search.search(owner, SearchRequest(limit=1, cursor=first.next_cursor))
    assert first.items[0].candidate_id != second.items[0].candidate_id
    for changed_owner, request in ((uuid4(), SearchRequest(limit=1, cursor=first.next_cursor)),
                                    (owner, SearchRequest(q="Python", cursor=first.next_cursor))):
        with pytest.raises(HTTPException) as error:
            search.search(changed_owner, request)
        assert error.value.status_code == 409
    lexical = ShowcaseStore(search.database_url, FixtureQueryEncoder(unavailable=True))
    before = lexical.search(owner, SearchRequest(q="Python", limit=1))
    lexical.encoder.unavailable = False
    with pytest.raises(HTTPException) as error:
        lexical.search(owner, SearchRequest(q="Python", limit=1, cursor=before.next_cursor))
    assert error.value.status_code == 409
    with connect(search.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=jsonb_set(profile,'{consents,pool}','false') WHERE candidate_id=%s", (UUID(int=3),))
    with pytest.raises(HTTPException) as error:
        search.search(owner, SearchRequest(cursor=first.next_cursor))
    assert error.value.status_code == 409
    assert search.search(owner, SearchRequest()).total == 2
    with pytest.raises(HTTPException) as error:
        search.profile(owner, UUID(int=3))
    assert error.value.status_code == 404


def test_mismatched_candidate_models_never_contribute_semantic_rank(showcase):
    search, owner = showcase
    with connect(search.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET semantic_model_version='different-space' WHERE candidate_id=%s", (UUID(int=1),))
        connection.execute("UPDATE candidate_pipeline.pool SET semantic_model_version='different' WHERE candidate_id=%s", (UUID(int=3),))
    page = search.search(owner, SearchRequest(q="Python"))
    assert page.mode == "hybrid"
    assert all(card.search_factors.semantic_rank is None for card in page.items if card.candidate_id in (UUID(int=1), UUID(int=3)))
    search.encoder.embedding_space_id = "different-query-space"
    assert search.search(owner, SearchRequest(q="Python")).fallback_reason == "embedding_space_mismatch"
    search.encoder.embedding_space_id = EMBEDDING_SPACE_ID
    with connect(search.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.pool SET semantic_model_version='different-space'")
    page = search.search(owner, SearchRequest(q="Python"))
    assert page.mode == "lexical" and page.fallback_reason == "no_compatible_embeddings"


def test_revision_schema_and_generation_drift_fail_closed(showcase):
    search, owner = showcase
    first = search.search(owner, SearchRequest(limit=1))
    with pytest.raises(HTTPException) as error:
        search.search(owner, SearchRequest(limit=1, cursor=first.next_cursor, filters={"cities": ["Москва"]}))
    assert error.value.status_code == 409
    with connect(search.database_url) as connection:
        connection.execute("UPDATE matching_pipeline.settings SET pool_generation=pool_generation+1")
    with pytest.raises(HTTPException) as error:
        search.search(owner, SearchRequest(cursor=first.next_cursor))
    assert error.value.status_code == 409
    with connect(search.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET revision=revision+1 WHERE candidate_id=%s", (UUID(int=1),))
        connection.execute("UPDATE candidate_pipeline.pool SET schema_hash='obsolete-schema' WHERE candidate_id=%s", (UUID(int=2),))
    assert [card.candidate_id for card in search.search(owner, SearchRequest()).items] == [UUID(int=3)]
    for candidate_id in (UUID(int=1), UUID(int=2)):
        with pytest.raises(HTTPException) as error:
            search.profile(owner, candidate_id)
        assert error.value.status_code == 404


def test_cache_expires_and_remains_bounded(showcase):
    search, owner = showcase
    search.search(owner, SearchRequest(q="Python"))
    with connect(search.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.search_embeddings SET created_at=now()-interval '25 hours'")
    search.encoder.unavailable = True
    assert search.search(owner, SearchRequest(q="Python")).mode == "lexical"
    search.encoder.unavailable = False
    with connect(search.database_url) as connection:
        connection.execute("""INSERT INTO candidate_pipeline.search_embeddings(cache_key,model_version,semantic_space_id,semantic_vector)
            SELECT 'fixture-'||n,'fixture','fixture',%s::vector FROM generate_series(1,1001) n""", ("[1," + "0," * 638 + "0]",))
    search.search(owner, SearchRequest(q="новый запрос"))
    with connect(search.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM candidate_pipeline.search_embeddings").fetchone()["n"] == 1000
        assert connection.execute("SELECT count(*) AS n FROM candidate_pipeline.search_embeddings WHERE created_at <= now()-interval '24 hours'").fetchone()["n"] == 0
