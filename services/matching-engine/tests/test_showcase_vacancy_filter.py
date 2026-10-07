"""Vacancy showcase eligibility shares the exact combiner gate and owner scope."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from matching_engine.combine_scoring import strict_filter
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.showcase_models import SearchRequest
from matching_engine.showcase_store import ShowcaseStore
from matching_engine.store import NotFoundError
from test_combine_integration import setup_pool
from test_showcase_search_integration import FixtureQueryEncoder

pytestmark = pytest.mark.integration
pytest_plugins = ["test_pipeline_integration"]


def test_vacancy_filter_is_owned_open_and_uses_combiner_rules(store):
    _, vacancy_id, _ = setup_pool(store)
    search = ShowcaseStore(store.database_url, FixtureQueryEncoder())
    with connect(store.database_url) as connection:
        vacancy = CombineStore._vacancy(connection, vacancy_id)
        expected = {c.candidate_id for c in CombineStore._candidates(connection) if strict_filter(vacancy,c).passed}
    request = SearchRequest(filters={"vacancy_id": vacancy_id})
    result = search.search(vacancy.owner_id, request)
    assert {card.candidate_id for card in result.items} == expected
    with pytest.raises(HTTPException) as error:
        search.search(uuid4(), request)
    assert error.value.status_code == 404
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.vacancies SET business_status='closed' WHERE vacancy_id=%s", (vacancy_id,))
    with pytest.raises(NotFoundError):
        search.search(vacancy.owner_id, request)
