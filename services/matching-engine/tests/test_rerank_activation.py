"""Manual operator authority preserves sealed research evidence and main freshness."""

from copy import deepcopy

import psycopg
import pytest
from test_combine_integration import finish, setup_pool
from test_combine_scoring import candidate, vacancy
from test_pipeline_integration import store as store  # noqa: PLC0414

from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import select_candidates
from matching_engine.database import connect
from matching_engine.feed_models import FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.rerank_activation import (
    DbManualApproval,
    _approval_from_db,
    bootstrap_packaged,
    make_receipt,
    packaged_rollout,
    read_json,
    receipt_hash,
)
from matching_engine.rerank_model import artifact_hash, promotion_eligible, rerank_cards
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.store import ConflictError
from matching_engine.strict import initial_schema


def authority(artifact, receipt):
    """Exercise the store's receipt verifier with an explicitly persisted-row shape."""
    return _approval_from_db({"receipt_hash": receipt["receipt_hash"], "artifact_hash": artifact["content_hash"],
                              "action": receipt["action"], "receipt": receipt}, artifact)


def score(artifact, **kwargs):
    """Use real feature builders and main strict filtering without embeddings or training."""
    target, values, policy = vacancy(), [candidate(rating_score=.9), candidate(rating_score=.5)], ScoringPolicy()
    cards = select_candidates(target, values, policy, 10).items
    return rerank_cards(target, values, cards, policy, artifact, initial_schema(), **kwargs), cards


def test_packaged_decision_is_truthful_and_sort_only():
    artifact, receipt = packaged_rollout()
    assert artifact["content_hash"] == artifact_hash(artifact)
    assert artifact["tail_gap"] is None
    assert artifact["gate"]["passed"] is False and artifact["training"]["test_only"] is True
    assert artifact["reviews"]["user_pairs"] == artifact["reviews"]["assistant_pairs"] == 0
    assert receipt["gate_snapshot"] == artifact["gate"] and receipt["reviews_snapshot"] == artifact["reviews"]
    assert receipt["review_evidence"]["independent_human_evaluation"] is False
    assert receipt["review_evidence"]["factual_errors"] == 0
    assert not promotion_eligible(artifact)
    result, cards = score(artifact, manual_approval=authority(artifact, receipt))
    assert result.mode == "learned" and result.fallback_reason is None
    assert {item.candidate_id for item in result.items} == {card.candidate_id for card in cards}
    assert all(item.kind == "recommended" and item.score == pytest.approx(sum(item.contributions.values()))
               and len(item.contributions) == len(artifact["feature_names"]) for item in result.items)


def test_caller_dictionary_cannot_grant_runtime_authority():
    artifact, receipt = packaged_rollout()
    for injected in (receipt, {"artifact_hash": artifact["content_hash"], "approved": True}):
        result, cards = score(artifact, manual_approval=injected)
        assert result.mode == "main_fallback" and result.fallback_reason == "manual_approval_invalid"
        assert [item.candidate_id for item in result.items] == [card.candidate_id for card in cards]
        assert all(item.score is None for item in result.items)
    with pytest.raises(ValueError, match="database"):
        DbManualApproval(artifact["content_hash"], receipt["receipt_hash"], _trust=object())
    assert score(artifact)[0].fallback_reason == "artifact_unapproved"
    assert score(artifact, approval_error="manual_approval_invalid")[0].fallback_reason == "manual_approval_invalid"


@pytest.mark.parametrize("field,value", [
    ("tail_gap", .1), ("gate.protocol_valid", False), ("gate.label_validation_passed", False),
    ("gate.passed", True), ("training.test_only", False), ("reviews.factual_issues", ["unresolved"]),
])
def test_receipt_cannot_waive_safety_requirements(field, value):
    artifact, receipt = packaged_rollout()
    path = field.split(".")
    (artifact if len(path) == 1 else artifact[path[0]])[path[-1]] = value
    artifact["content_hash"] = artifact_hash(artifact)
    evidence = {**receipt["review_evidence"], "artifact_hash": artifact["content_hash"]}
    with pytest.raises(ValueError):
        make_receipt(artifact, action="activate", actor="test-operator", reason="explicit test decision",
                     acknowledgements=receipt["acknowledgements"], review_evidence=evidence)


@pytest.mark.parametrize("change", ["ack", "factual", "snapshot", "row", "deactivate"])
def test_receipt_and_persisted_row_tampering_is_rejected(change):
    artifact, receipt = packaged_rollout()
    if change == "ack":
        receipt["acknowledgements"]["failed_quality_gate"] = False
    elif change == "factual":
        receipt["review_evidence"]["factual_errors"] = 1
    elif change == "snapshot":
        receipt["gate_snapshot"]["passed"] = True
    elif change == "deactivate":
        receipt = make_receipt(artifact, action="deactivate", actor="operator", reason="rollback")
    receipt["receipt_hash"] = receipt_hash(receipt)
    if change == "row":
        with pytest.raises(ValueError):
            _approval_from_db({"receipt_hash": "0" * 64, "artifact_hash": artifact["content_hash"],
                               "action": "activate", "receipt": receipt}, artifact)
    else:
        with pytest.raises(ValueError):
            authority(artifact, receipt)


@pytest.mark.parametrize("field,reason", [("main_policy_hash", "main_policy_mismatch"),
                                         ("semantic_space_id", "semantic_space_mismatch"),
                                         ("strict_schema_hash", "feature_schema_mismatch")])
def test_manual_authority_does_not_waive_runtime_compatibility(field, reason):
    artifact, receipt = packaged_rollout()
    artifact[field] = "changed"
    artifact["content_hash"] = artifact_hash(artifact)
    receipt = make_receipt(artifact, action="activate", actor="operator", reason="compatibility regression test",
        acknowledgements=receipt["acknowledgements"], review_evidence={**receipt["review_evidence"], "artifact_hash": artifact["content_hash"]})
    assert score(artifact, manual_approval=authority(artifact, receipt))[0].fallback_reason == reason


def test_bootstrap_uses_packaged_identity_and_uninitialized_guard():
    class StoreSpy:
        def activate_manual(self, artifact, receipt, *, only_if_uninitialized):
            assert only_if_uninitialized is True
            assert receipt["artifact_hash"] == artifact["content_hash"]
            return {"state": "observed"}
    assert bootstrap_packaged(StoreSpy()) == {"state": "observed"}


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '[]'])
def test_operator_json_is_strict(tmp_path, raw):
    path = tmp_path / "receipt.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(path)


@pytest.mark.integration
def test_manual_activation_atomic_queue_deactivation_and_bootstrap(store):
    combines, vacancy_id, _ = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    assert RerankWorker(reranks).process_once()
    original = reranks.status(vacancy_id)
    artifact, receipt = packaged_rollout()
    assert reranks.register(artifact) == artifact["content_hash"]
    with pytest.raises(ConflictError, match="quality"):
        reranks.promote(artifact["content_hash"])
    result = bootstrap_packaged(reranks)
    assert result["activation_revision"] == 1 and result["queued_mains"] == 1
    assert reranks.status(vacancy_id).run_id != original.run_id
    assert RerankWorker(reranks).process_once()
    page = reranks.selections(vacancy_id, "recommended", 100, 0)
    assert page.run.mode == "learned" and page.run.activation_kind == "manual_override"
    assert page.run.approval_receipt_hash == receipt["receipt_hash"] and page.run.activation_revision == 1
    assert all(card.activation_revision == 1 and card.approval_receipt_hash == receipt["receipt_hash"]
               and card.rerank_score is not None for card in page.items)
    assert reranks.selections(vacancy_id, "reserve", 100, 0).items == []
    assert bootstrap_packaged(reranks)["state"] == "existing_operator_decision_preserved"
    with connect(store.database_url) as connection:
        stored = connection.execute("SELECT * FROM rerank_pipeline.artifacts").fetchone()
        assert stored["approved"] is False and stored["artifact"] == artifact
        assert connection.execute("SELECT receipt FROM rerank_pipeline.approval_receipts").fetchone()["receipt"] == receipt
    with pytest.raises(psycopg.Error), connect(store.database_url) as connection:
        connection.execute("UPDATE rerank_pipeline.approval_receipts SET receipt=receipt")
    rollback = reranks.deactivate(artifact["content_hash"], actor="test-operator", reason="explicit rollback")
    assert rollback["activation_revision"] == 2
    assert bootstrap_packaged(reranks)["state"] == "existing_operator_decision_preserved"
    assert RerankWorker(reranks).process_once()
    assert reranks.status(vacancy_id).fallback_reason == "artifact_absent"
    assert reranks.status(vacancy_id).activation_kind == "none"
    with connect(store.database_url) as connection:
        event = connection.execute("SELECT * FROM rerank_pipeline.activation_events ORDER BY activation_revision DESC LIMIT 1").fetchone()
        assert event["approval_receipt_hash"] == rollback["deactivation_receipt_hash"]


@pytest.mark.integration
def test_same_artifact_new_receipt_rejects_stale_publish(store):
    combines, vacancy_id, _ = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    artifact, receipt = packaged_rollout()
    reranks.activate_manual(artifact, receipt)
    job = reranks.claim(30, 5)
    outcome = rerank_cards(job.vacancy, job.candidates, job.cards, job.policy, job.artifact, job.schema,
                           manual_approval=job.manual_approval)
    next_receipt = deepcopy(receipt)
    next_receipt["reason"] = "new operator decision with exactly the same coefficients"
    next_receipt["receipt_hash"] = receipt_hash(next_receipt)
    assert reranks.activate_manual(artifact, next_receipt)["activation_revision"] == 2
    assert not reranks.publish(job, outcome)
    assert RerankWorker(reranks).process_once()
    assert reranks.status(vacancy_id).approval_receipt_hash == next_receipt["receipt_hash"]
    assert reranks.status(vacancy_id).activation_revision == 2


@pytest.mark.integration
def test_same_hash_reactivation_invalidates_existing_feed(store):
    combines, vacancy_id, _ = setup_pool(store)
    finish(combines)
    reranks = RerankStore(store.database_url)
    artifact, receipt = packaged_rollout()
    reranks.activate_manual(artifact, receipt)
    assert RerankWorker(reranks).process_once()
    with connect(store.database_url) as connection:
        owner = connection.execute("SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                   (vacancy_id,)).fetchone()["owner_id"]
    feed = FeedStore(store.database_url)
    session = feed.open(owner, FeedRequest(limit=1), vacancy_id)
    next_receipt = deepcopy(receipt)
    next_receipt["reason"] = "same model hash, a distinct audited activation epoch"
    next_receipt["receipt_hash"] = receipt_hash(next_receipt)
    reranks.activate_manual(artifact, next_receipt)
    with pytest.raises(ConflictError):
        feed.page(owner, vacancy_id, session.session_id, session.next_cursor, 10)


@pytest.mark.integration
def test_incompatible_bootstrap_does_not_install_decision(store):
    from matching_engine.models import CoordinateAddition
    store.extend_schema(CoordinateAddition(group="stack", key="activation-compatibility-test"))
    reranks = RerankStore(store.database_url)
    with pytest.raises(ConflictError, match="incompatible"):
        bootstrap_packaged(reranks)
    with connect(store.database_url) as connection:
        assert connection.execute("SELECT activation_revision FROM rerank_pipeline.settings").fetchone()["activation_revision"] == 0
        assert connection.execute("SELECT count(*) AS n FROM rerank_pipeline.approval_receipts").fetchone()["n"] == 0
