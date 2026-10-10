"""Real per-candidate filter decisions and privacy threshold regression tests."""

from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from matching_engine.main import create_app
from matching_engine.recommendations import capture_main, capture_rerank
from test_combine_scoring import candidate, vacancy


class Connection:
    def __init__(self):
        self.inserted = []
        self.tail = []

    def execute(self, sql, params=()):
        if "INSERT INTO recommendation_evidence" in sql:
            self.inserted.append((*params, "unranked_by_reranker") if len(params) == 3 else params)
        return SimpleNamespace(fetchall=lambda: self.tail)


def test_main_records_actual_candidate_reasons_and_real_eligible_tail(monkeypatch):
    monkeypatch.setattr("matching_engine.recommendations.consenting", lambda *_args: True)
    candidates = [candidate(candidate_id=UUID(int=1)), candidate(candidate_id=UUID(int=2), work_types=None), candidate(candidate_id=UUID(int=3))]
    job = SimpleNamespace(candidates=candidates, vacancy=vacancy(), excluded_ids=set(), run_id=UUID(int=10))
    result = SimpleNamespace(items=[SimpleNamespace(candidate_id=candidates[0].candidate_id)])
    connection = Connection()
    capture_main(connection, job, result)
    assert [(row[0], row[3]) for row in connection.inserted] == [(UUID(int=2), "work_type_unknown"), (UUID(int=3), "main_top_k_omission")]


def test_instrumentation_never_retains_nonconsented_candidates(monkeypatch):
    monkeypatch.setattr("matching_engine.recommendations.consenting", lambda *_args: False)
    connection = Connection()
    capture_main(connection, SimpleNamespace(candidates=[candidate()], vacancy=vacancy(), excluded_ids=set(), run_id=UUID(int=10)), SimpleNamespace(items=[]))
    assert connection.inserted == []


def test_reranker_fallback_is_system_context_and_reserve_is_not_skill_deficit(monkeypatch):
    monkeypatch.setattr("matching_engine.recommendations.consenting", lambda *_args: True)
    connection = Connection()
    job = SimpleNamespace(vacancy=vacancy(), run_id=UUID(int=10))
    capture_rerank(connection, job, SimpleNamespace(mode="main_fallback", items=[SimpleNamespace(candidate_id=UUID(int=1), kind="recommended")]))
    capture_rerank(connection, job, SimpleNamespace(mode="learned", items=[SimpleNamespace(candidate_id=UUID(int=2), kind="reserve")]))
    assert [row[3] for row in connection.inserted] == ["system_fallback", "reranker_reserve"]


def test_internal_evidence_endpoint_requires_configured_internal_auth():
    client = TestClient(create_app(internal_token="synthetic"))
    response = client.get("/internal/candidates/00000000-0000-4000-8000-000000000001/recommendation-evidence?after=2026-10-01T00:00:00Z&before=2026-10-08T00:00:00Z")
    assert response.status_code == 401


def test_unranked_label_exists_only_after_successful_rerank(monkeypatch):
    monkeypatch.setattr("matching_engine.recommendations.consenting", lambda *_args: True)
    connection = Connection()
    connection.tail = [{"candidate_id": UUID(int=3)}]
    capture_rerank(connection, SimpleNamespace(vacancy=vacancy(), run_id=UUID(int=10)), SimpleNamespace(mode="learned", items=[]))
    assert [(row[0], row[3]) for row in connection.inserted] == [(UUID(int=3), "unranked_by_reranker")]
