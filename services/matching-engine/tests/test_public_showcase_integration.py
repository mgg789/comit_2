"""Real PostgreSQL global catalogue; model/PDF worker fixtures are synthetic test-only."""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from matching_engine.database import connect
from matching_engine.public_showcase import CatalogueQuery, PublicShowcaseStore
from matching_engine.vacancy_store import VacancyStore
from test_contracts import eligible_profile
from test_pipeline_integration import store as store, worker  # noqa: PLC0414
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import run

pytestmark = pytest.mark.integration


def test_candidates_global_date_pagination_filters_and_consent(store):
    for index in range(1, 4):
        store.intake(UUID(int=index), eligible_profile(description="Python сервер", stacks=[{"key": "python", "level": 3, "status": "confirmed"}]))
        assert worker(store).process_once()
    catalogue = PublicShowcaseStore(store.database_url)
    page = catalogue.page(CatalogueQuery(limit=1), candidates=True)
    assert page.has_more and page.next_cursor
    second = catalogue.page(CatalogueQuery(limit=1, cursor=page.next_cursor), candidates=True)
    assert second.items[0].candidate_id != page.items[0].candidate_id
    assert all("resume_url" not in item.model_dump() and "evidence" not in item.model_dump() for item in page.items)
    filtered = catalogue.page(CatalogueQuery(q="Python", specialization="backend", grade="middle", stack="python"), candidates=True)
    assert len(filtered.items) == 3
    assert not catalogue.page(CatalogueQuery(stack="react"), candidates=True).items
    oldest = catalogue.page(CatalogueQuery(sort="oldest"), candidates=True)
    newest = catalogue.page(CatalogueQuery(), candidates=True)
    assert [item.candidate_id for item in oldest.items] == list(reversed([item.candidate_id for item in newest.items]))
    for query in (CatalogueQuery(cursor=page.next_cursor, sort="oldest"), CatalogueQuery(cursor=page.next_cursor, grade="senior")):
        with pytest.raises(HTTPException) as error:
            catalogue.page(query, candidates=True)
        assert error.value.status_code == 409
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=jsonb_set(profile,'{consents,pool}','false') WHERE candidate_id=%s", (UUID(int=2),))
        connection.execute("UPDATE candidate_pipeline.candidates SET revision=revision+1 WHERE candidate_id=%s", (UUID(int=3),))
    assert [item.candidate_id for item in catalogue.page(CatalogueQuery(), candidates=True).items] == [UUID(int=1)]
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=jsonb_set(profile,'{consents,processing}','false') WHERE candidate_id=%s", (UUID(int=1),))
    assert not catalogue.page(CatalogueQuery(), candidates=True).items
    with pytest.raises(HTTPException) as error:
        catalogue.page(CatalogueQuery(limit=1, cursor=page.next_cursor), candidates=True)
    assert error.value.status_code == 409


def test_vacancies_global_filters_literal_search_and_lifecycle(store):
    vacancies = VacancyStore(store.database_url)
    ids = [uuid4(), uuid4(), uuid4()]
    for index, vacancy_id in enumerate(ids):
        vacancies.intake(vacancy_id, eligible_vacancy(metadata={"title": "Python_100%" if index == 0 else "Backend engineer", "company_name": "Synthetic company", "grade": "middle"}, stacks={"python": 3}))
        assert run(vacancies).process_once()
    catalogue = PublicShowcaseStore(store.database_url)
    page = catalogue.page(CatalogueQuery(limit=1), candidates=False)
    assert page.has_more and page.next_cursor
    assert catalogue.page(CatalogueQuery(limit=1, cursor=page.next_cursor), candidates=False).items[0].vacancy_id != page.items[0].vacancy_id
    assert len(catalogue.page(CatalogueQuery(specialization="backend", grade="middle", stack="python"), candidates=False).items) == 3
    assert [item.vacancy_id for item in catalogue.page(CatalogueQuery(q="_100%"), candidates=False).items] == [ids[0]]
    assert len(catalogue.page(CatalogueQuery(q="Synthetic company"), candidates=False).items) == 3
    assert not catalogue.page(CatalogueQuery(grade="senior"), candidates=False).items
    with connect(store.database_url) as connection:
        connection.execute("UPDATE vacancy_pipeline.vacancies SET business_status='closed' WHERE vacancy_id=%s", (ids[0],))
        connection.execute("UPDATE vacancy_pipeline.vacancies SET revision=revision+1 WHERE vacancy_id=%s", (ids[1],))
    assert [item.vacancy_id for item in catalogue.page(CatalogueQuery(), candidates=False).items] == [ids[2]]
    with pytest.raises(HTTPException) as error:
        catalogue.page(CatalogueQuery(limit=1, cursor=page.next_cursor), candidates=False)
    assert error.value.status_code == 409


def test_real_internal_http_requires_token_but_no_owner_and_applies_query(store):
    from fastapi.testclient import TestClient
    from matching_engine.main import create_app
    store.intake(UUID(int=1), eligible_profile(stacks=[{"key": "python", "level": 3, "status": "confirmed"}]))
    assert worker(store).process_once()
    client = TestClient(create_app(store=store, internal_token="synthetic"))
    assert client.get("/internal/public/showcase/candidates").status_code == 401
    headers = {"X-Internal-Token": "synthetic"}
    result = client.get("/internal/public/showcase/candidates?grade=middle&specialization=backend&stack=python&limit=1&sort=oldest", headers=headers)
    assert result.status_code == 200 and len(result.json()["items"]) == 1
    assert client.get("/internal/public/showcase/candidates?stack=react", headers=headers).json()["items"] == []
    assert client.get("/internal/public/showcase/candidates?limit=51", headers=headers).status_code == 422
    assert client.get("/internal/public/showcase/candidates?owner_id=foreign", headers=headers).status_code == 422


def test_anonymous_search_cannot_probe_hidden_profile_text(store):
    """A hidden biography token must not change the anonymous catalogue result."""
    identity = UUID(int=99)
    store.intake(identity, eligible_profile(description="SecretBiographProbe", stacks=[
        {"key": "python", "level": 3, "status": "confirmed"}]))
    assert worker(store).process_once()
    catalogue = PublicShowcaseStore(store.database_url)
    assert len(catalogue.page(CatalogueQuery(q="python"), candidates=True).items) == 1
    assert not catalogue.page(CatalogueQuery(q="SecretBiographProbe"), candidates=True).items
