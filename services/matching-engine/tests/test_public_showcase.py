"""Anonymous catalogue authentication, bounds and consent/current revision SQL regressions."""
from uuid import UUID

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from matching_engine.public_showcase import CatalogueQuery, PublicShowcaseStore, public_showcase_router
from pydantic import ValidationError


def test_catalogue_requires_internal_auth_without_fabricated_owner():
    app = FastAPI()
    def auth(x_internal_token: str | None = Header(default=None)):
        if x_internal_token != "synthetic":
            raise HTTPException(401, "unauthorized")
    app.include_router(public_showcase_router(None, auth))
    client = TestClient(app)
    for path in ("candidates", "vacancies"):
        assert client.get(f"/internal/public/showcase/{path}").status_code == 401
        assert client.get(f"/internal/public/showcase/{path}", headers={"X-Internal-Token": "synthetic"}).status_code == 503


def test_catalogue_query_bounds_and_nul_before_database():
    for data in ({"limit": 0}, {"limit": 51}, {"q": "x" * 501}, {"grade": "expert"}, {"stack": "secret@example.test"}, {"sort": "rank"}, {"owner_id": str(UUID(int=1))}):
        with pytest.raises(ValidationError):
            CatalogueQuery.model_validate(data)
    with pytest.raises(HTTPException) as error:
        PublicShowcaseStore("unused").page(CatalogueQuery(q="\x00"), candidates=True)
    assert error.value.status_code == 400
