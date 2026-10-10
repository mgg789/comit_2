"""Counts-only synthesis validation; no live DeepSeek traffic."""

import json
from uuid import UUID

import pytest
from agents_service.providers import ProviderError
from agents_service.recommendations import (
    RecommendationInput,
    generate,
    register_recommendation_routes,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


class Provider:
    def __init__(self, text=None, categories=None):
        self.text = text or "В отзывах повторяется тема опыта. Уточните свой вклад в проектах."
        self.categories = categories or ["experience"]
        self.messages = None

    def complete(self, messages, tools, max_tokens):
        self.messages = messages
        assert tools == []
        assert max_tokens == 1024
        return {"choices": [{"message": {"content": json.dumps({"text": self.text, "referenced_categories": self.categories})}}]}


def payload():
    return {"evidence": [{"source": "employer_feedback", "category": "experience", "count": 3}]}


def test_provider_input_is_canonical_counts_only_and_never_subject_identity():
    provider = Provider()
    result = generate(RecommendationInput.model_validate(payload()), provider)
    assert result.referenced_categories == ["experience"]
    assert json.loads(provider.messages[1]["content"]) == payload()


@pytest.mark.parametrize("evidence", [
    {"source": "matching", "category": "salary_disjoint", "count": 3},
    {"source": "employer_feedback", "category": "private arbitrary prose", "count": 3},
    {"source": "employer_feedback", "category": "experience", "count": 2},
    {"source": "employer_feedback", "category": "experience", "count": 3, "name": "private"},
])
def test_sparse_or_identifying_input_is_rejected(evidence):
    with pytest.raises(ValidationError):
        RecommendationInput.model_validate({"evidence": [evidence]})


def test_unsupported_output_claims_or_contacts_are_rejected():
    request = RecommendationInput.model_validate(payload())
    for provider in (Provider(categories=["technical_skills"]), Provider(text="Contact private@example.test to improve your chances immediately")):
        with pytest.raises(ProviderError):
            generate(request, provider)


def test_internal_endpoint_authentication_and_consent_recheck(monkeypatch):
    checks = []
    def consent(_url, candidate_id):
        checks.append(candidate_id)
    monkeypatch.setattr("agents_service.recommendations._consent", consent)
    app = FastAPI()
    def owner():
        return UUID("00000000-0000-4000-8000-000000000001")
    class Database:
        database_url = "synthetic-only"
    register_recommendation_routes(app, owner, Database, Provider())
    result = TestClient(app).post("/internal/candidate-recommendations/generate", json=payload())
    assert result.status_code == 200
    assert len(checks) == 2
    def withdrawn(*_args):
        raise HTTPException(409, "recommendation_consent_required")
    monkeypatch.setattr("agents_service.recommendations._consent", withdrawn)
    assert TestClient(app).post("/internal/candidate-recommendations/generate", json=payload()).status_code == 409
