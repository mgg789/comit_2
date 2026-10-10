"""Public search contract, privacy and trusted-auth boundary unit tests."""
import json
from uuid import UUID

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from matching_engine.showcase_models import SearchRequest
from matching_engine.showcase_projection import public_profile
from matching_engine.showcase_routes import showcase_router
from matching_engine.showcase_store import _decode_cursor, _encode_cursor, _filter_sql
from pydantic import ValidationError
from test_contracts import eligible_profile


def test_public_projection_redacts_every_text_and_drops_opaque_data():
    profile = eligible_profile(description="Synthetic Candidate synthetic@example.invalid +79991234567 https://secret.invalid",
        preferences=["write synthetic@example.invalid"], relocation_cities=["Synthetic Candidate"],
        projects=[{"title": "Synthetic Candidate", "description": "synthetic@example.invalid",
                   "status": "confirmed", "details": {"my_role": "private", "proof": "secret"},
                   "provenance": ["private"], "facts": {"role": {"value": "developer", "status": "confirmed", "provenance": ["private"]}}}],
        metadata={"private": "secret"})
    result = public_profile(profile, UUID(int=1))
    text = json.dumps(result, ensure_ascii=False)
    assert result["display_name"] == f"Candidate {UUID(int=1)}"
    assert result["projects"][0]["facts"]["role"] == {"value": "developer", "status": "confirmed"}
    for private in ("Synthetic Candidate", "synthetic@example.invalid", "+79991234567", "secret.invalid", "provenance", "metadata", "details", "private"):
        assert private not in text
    assert public_profile(profile, UUID(int=1)) == result


def test_search_contract_bounds_and_explicit_skill_states():
    for data in ({"q": "a" * 501}, {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "1"}, {"filters": {"cities": [" " ]}}, {"filters": {"cities": ["a" * 301]}}, {"filters": {"groups": {"stacks": [{"key": "python", "min_level": True}]}}}, {"owner_id": str(UUID(int=1))}):
        with pytest.raises(ValidationError):
            SearchRequest.model_validate(data)
    request = SearchRequest.model_validate({"filters": {"salary": {"from_rub": 100, "to_rub": 200},
        "groups": {"stacks": [{"key": "python", "min_level": 4}]}, "work_types": ["remote"], "relocation_ready": False}})
    clause, values = _filter_sql(request)
    assert "<= %s" in clause and ">= %s" in clause and "?| %s" in clause
    assert "rejected" not in values[-1]


def test_cursor_strict_shape_and_offset_bounds():
    valid = {"v": 1, "query": "q", "snapshot": "s", "channel": "c", "offset": 10000}
    assert _decode_cursor(_encode_cursor(valid)) == valid
    assert _decode_cursor(_encode_cursor(valid | {"offset": 10001}))["offset"] == 10001
    for value in ("not-base64", _encode_cursor(valid | {"offset": 2**53}), _encode_cursor(valid | {"offset": True}), _encode_cursor(valid | {"extra": 1})):
        with pytest.raises(HTTPException) as error:
            _decode_cursor(value)
        assert error.value.status_code == 409


def test_trusted_auth_precedes_unconfigured_store():
    app = FastAPI()
    def auth(x_internal_token: str | None = Header(default=None)):
        if x_internal_token != "synthetic":
            raise HTTPException(401, "unauthorized")
    app.include_router(showcase_router(None, auth))
    client = TestClient(app)
    assert client.post("/internal/employer/candidates/search", json={}).status_code == 401
    assert client.post("/internal/employer/candidates/search", json={}, headers={"X-Internal-Token": "synthetic", "X-Owner-ID": str(UUID(int=1))}).status_code == 503


def test_nul_query_fails_before_database_access():
    from matching_engine.showcase_store import ShowcaseStore
    with pytest.raises(HTTPException) as error:
        ShowcaseStore("unused").search(UUID(int=1), SearchRequest(q="Python\x00"))
    assert error.value.status_code == 400
