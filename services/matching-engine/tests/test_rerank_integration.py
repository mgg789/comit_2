"""Real disposable PostgreSQL tests for durable baseline jobs and freshness guards."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from matching_engine.combine_models import CombineRequest, SelectionStateInput
from matching_engine.combine_scoring import policy_hash
from matching_engine.database import connect
from matching_engine.embeddings import EMBEDDING_SPACE_ID
from matching_engine.main import create_app
from matching_engine.models import CoordinateAddition
from matching_engine.rerank_features import feature_schema
from matching_engine.rerank_model import rerank_cards
from matching_engine.rerank_models import RerankRequest
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError, NotFoundError, Store
from matching_engine.vacancy_store import VacancyStore
from test_combine_integration import finish, setup_pool
from test_contracts import eligible_profile
from test_pipeline_integration import store as store  # noqa: PLC0414
from test_pipeline_integration import worker
from test_vacancy_integration import run

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("component", ["features", "tail"])
def test_runtime_schema_change_invalidates_ready_cards(store: Store, monkeypatch, component: str) -> None:
    """Code/schema updates must invalidate cards even with unchanged main and active artifact."""
    from dataclasses import replace

    import matching_engine.rerank_features as features
    import matching_engine.rerank_model as model
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert RerankWorker(reranks).process_once()
    assert reranks.status(vacancy_id).state == "ready"
    if component == "features":
        original = features.feature_schema
        monkeypatch.setattr(features, "feature_schema", lambda schema: replace(original(schema), schema_hash="changed"))
    else:
        monkeypatch.setattr(model, "tail_policy_hash", lambda _artifact: "changed")
    assert reranks.status(vacancy_id).state == "obsolete"
    with pytest.raises(ConflictError):
        reranks.selections(vacancy_id, "recommended", 10, 0)


def artifact_for(store: Store, *, approved: bool = True, tail: float | None = None) -> dict:
    """Provide a declared test-only artifact to test registry/ordering, not trained-model quality."""
    features = feature_schema(store.schema())
    values = [0.0] * len(features.names)
    values[features.names.index("salary_midpoint_ratio")] = 1.0
    from matching_engine.combine_models import ScoringPolicy
    return {"model_version": "test-only-v1", "feature_names": list(features.names),
        "feature_schema_hash": features.schema_hash, "strict_schema_hash": store.schema().schema_hash,
        "main_policy_hash": policy_hash(ScoringPolicy()), "semantic_space_id": EMBEDDING_SPACE_ID,
        "coefficients": values, "means": [0.0]*len(values), "scales": [1.0]*len(values), "tail_gap": tail,
        "dataset_hash": "test-only", "split_hash": "test-only", "label_cache_hash": "test-only", "evaluation_hash": "test-only", "training": {},
        "gate": {"passed": approved, "golden_families": 12, "minimum_decisive_pairs_per_family": 6,
            "macro_ndcg_delta": .1, "ndcg_delta_ci_low": .01, "macro_pair_accuracy": .8,
            "macro_pair_delta": .1, "pair_delta_ci_low": .01,
            "tail": {"validated": True, "calibration_recall": 1.0, "golden_relevant_count": 60,
                "golden_recall": 1.0, "golden_wilson_low": .94}},
        "reviews": {"user_pairs": 20, "assistant_pairs": 20, "disjoint_blind": True, "factual_issues": []}}


def test_atomic_enqueue_and_fallback_pages_are_contact_free(store: Store) -> None:
    combines, vacancy_id, ids = setup_pool(store)
    combines.request(vacancy_id, CombineRequest(top_k=2))
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert reranks.status(vacancy_id).state == "queued"
    assert RerankWorker(reranks).process_once()
    page = reranks.selections(vacancy_id, "recommended", 1, 1)
    assert page.run.mode == "main_fallback" and page.run.fallback_reason == "artifact_absent"
    assert page.run.unranked_by_reranker_count == 1
    assert page.items[0].candidate_id == ids[1] and page.items[0].main_position == 2
    assert page.items[0].rerank_position is None and page.items[0].rerank_score is None
    assert reranks.selections(vacancy_id, "reserve", 10, 0).items == []
    assert "synthetic@example.invalid" not in page.model_dump_json()
    key = uuid4()
    first = reranks.request(vacancy_id, RerankRequest(idempotency_key=key))
    assert reranks.request(vacancy_id, RerankRequest(idempotency_key=key)).run_id == first.run_id


def test_register_unapproved_is_inactive_and_promote_queues_new_model(store: Store) -> None:
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    bad = reranks.register(artifact_for(store, approved=False))
    with pytest.raises(ConflictError, match="quality"):
        reranks.promote(bad)
    unsupported_tail = artifact_for(store, tail=.1)
    unsupported_tail["gate"].pop("tail")
    with pytest.raises(ConflictError, match="quality"):
        reranks.promote(reranks.register(unsupported_tail))
    assert RerankWorker(reranks).process_once()
    fallback = reranks.status(vacancy_id)
    good = reranks.register(artifact_for(store))
    reranks.promote(good)
    assert reranks.status(vacancy_id).run_id != fallback.run_id
    assert RerankWorker(reranks).process_once()
    page = reranks.selections(vacancy_id, "recommended", 100, 0)
    assert page.run.mode == "learned" and page.run.artifact_hash == good
    assert all(card.rerank_position == card.position and card.rerank_score is not None for card in page.items)


def test_profile_change_and_exclusion_invalidate_before_worker_tick(store: Store) -> None:
    combines, vacancy_id, ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert RerankWorker(reranks).process_once()
    store.intake(ids[0], eligible_profile(source_revision=2, work_types=["remote"], city="Казань"))
    assert reranks.status(vacancy_id).state == "obsolete"
    with pytest.raises(ConflictError):
        reranks.selections(vacancy_id, "recommended", 10, 0)
    assert worker(store).process_once()
    finish(combines)
    assert RerankWorker(reranks).process_once()
    combines.set_selection_state(vacancy_id, ids[1], SelectionStateInput(state="visited"))
    assert reranks.status(vacancy_id).state == "obsolete"


def test_model_promotion_during_compute_rejects_old_publish(store: Store) -> None:
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    job = reranks.claim(30, 5)
    result = rerank_cards(job.vacancy, job.candidates, job.cards, job.policy, job.artifact, job.schema)
    reranks.promote(reranks.register(artifact_for(store)))
    assert not reranks.publish(job, result)
    assert RerankWorker(reranks).process_once()
    assert reranks.selections(vacancy_id, "recommended", 10, 0).run.mode == "learned"


def test_duplicate_workers_lease_recovery_retry_and_atomic_publish(store: Store) -> None:
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: reranks.claim(30, 1), range(2)))
    job = next(job for job in jobs if job)
    assert sum(value is not None for value in jobs) == 1
    with connect(store.database_url) as connection:
        connection.execute("UPDATE rerank_pipeline.runs SET lease_until=now()-interval '1 second' WHERE run_id=%s", (job.run_id,))
    assert reranks.claim(30, 1) is None
    assert reranks.status(vacancy_id).state == "failed"
    assert reranks.retry(vacancy_id).state == "queued"
    fresh = reranks.claim(30, 5)
    result = rerank_cards(fresh.vacancy, fresh.candidates, fresh.cards, fresh.policy, fresh.artifact, fresh.schema)
    assert not reranks.publish(job, result)
    assert reranks.publish(fresh, result)
    assert not reranks.publish(fresh, result)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM rerank_pipeline.cards").fetchone()["n"] == 3


def test_strict_registry_append_produces_explicit_model_fallback(store: Store) -> None:
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    reranks.promote(reranks.register(artifact_for(store)))
    assert RerankWorker(reranks).process_once()
    store.extend_schema(CoordinateAddition(group="stack", key="new-test-stack"))
    assert reranks.status(vacancy_id).state == "obsolete"
    with pytest.raises(NotFoundError):
        reranks.selections(vacancy_id, "recommended", 10, 0)
    while worker(store).process_once():
        pass
    assert run(VacancyStore(store.database_url)).process_once()
    finish(combines)
    assert RerankWorker(reranks).process_once()
    page = reranks.selections(vacancy_id, "recommended", 10, 0)
    assert page.run.mode == "main_fallback" and page.run.fallback_reason == "feature_schema_mismatch"
    assert len(page.items) == 3


def test_protected_routes_validate_disposition_and_missing_main(store: Store) -> None:
    client = TestClient(create_app(store=store, internal_token="test-only"))
    vacancy = uuid4()
    path = f"/internal/vacancies/{vacancy}/rerank"
    assert client.post(path, json={}).status_code == 401
    headers = {"x-internal-token": "test-only"}
    assert client.post(path, headers=headers, json={"extra": 1}).status_code == 422
    assert client.post(path, headers=headers, json={}).status_code == 404
    assert client.get(path+"/selections?kind=all", headers=headers).status_code == 422


def test_learned_tail_pages_preserve_main_positions_and_full_top_k(store: Store) -> None:
    combines, vacancy_id, ids = setup_pool(store)
    store.intake(ids[2], eligible_profile(source_revision=2, work_types=["remote"],
                                        salary={"from_rub": 180000, "to_rub": 200000}))
    assert worker(store).process_once()
    finish(combines)
    reranks = RerankStore(store.database_url)
    reranks.promote(reranks.register(artifact_for(store, tail=.1)))
    assert RerankWorker(reranks).process_once()
    recommended = reranks.selections(vacancy_id, "recommended", 10, 0)
    reserve = reranks.selections(vacancy_id, "reserve", 10, 0)
    assert [card.candidate_id for card in recommended.items] == [ids[2]]
    assert [card.candidate_id for card in reserve.items] == ids[:2]
    assert recommended.items[0].main_position == 3 and recommended.items[0].rerank_position == 1
    assert recommended.run.selected_count == recommended.run.recommended_count + recommended.run.reserve_count == 3
    assert recommended.run.run_id == reserve.run.run_id
    assert combines.selections(vacancy_id, 10, 0).items[0].candidate_id == ids[0]


def test_invalid_publish_rolls_back_earlier_card_rows(store: Store) -> None:
    from dataclasses import replace
    combines, _vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    job = reranks.claim(30, 5)
    outcome = rerank_cards(job.vacancy, job.candidates, job.cards, job.policy, job.artifact, job.schema)
    bad = replace(outcome, items=[outcome.items[0], replace(outcome.items[1], kind="reserve"), outcome.items[2]])
    with pytest.raises(ValueError, match="fallback"):
        reranks.publish(job, bad)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM rerank_pipeline.cards").fetchone()["n"] == 0
    assert reranks.publish(job, outcome)


def test_worker_persists_failure_and_explicit_retry(store: Store, monkeypatch) -> None:
    combines, vacancy_id, _ids = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    import matching_engine.rerank_worker as module
    original = module.rerank_cards
    def fail(*_args):
        raise ValueError("test-only")
    monkeypatch.setattr(module, "rerank_cards", fail)
    runner = RerankWorker(reranks, max_attempts=1, retry_base_seconds=0)
    assert runner.process_once()
    assert reranks.status(vacancy_id).state == "failed"
    assert reranks.status(vacancy_id).error_code == "baseline_rerank_failed"
    assert reranks.retry(vacancy_id).state == "queued"
    monkeypatch.setattr(module, "rerank_cards", original)
    assert runner.process_once()
    assert reranks.status(vacancy_id).state == "ready"
