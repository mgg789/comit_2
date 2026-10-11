"""Portable linear R₀ artifacts and pure offline inference with explicit fallback."""

import math
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from matching_engine.combine_models import CandidateCard, MatchCandidate, ScoringPolicy
from matching_engine.combine_scoring import policy_hash
from matching_engine.embeddings import EMBEDDING_SPACE_ID
from matching_engine.fsp_boost import promote, promotion_policy
from matching_engine.models import Contract, StrictSchema
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.vacancy_models import VacancyReady


class RerankArtifact(Contract):
    """JSON-only model with exact feature ordering and a sealed experiment manifest."""

    model_version: str = Field(min_length=1, max_length=100)
    feature_names: list[str] = Field(min_length=1, max_length=20000)
    feature_schema_hash: str
    strict_schema_hash: str
    main_policy_hash: str
    semantic_space_id: str = EMBEDDING_SPACE_ID
    coefficients: list[float]
    means: list[float]
    scales: list[float]
    tail_gap: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tail_policy_version: Literal["tail-gap-v1"] = "tail-gap-v1"
    dataset_hash: str
    split_hash: str
    label_cache_hash: str
    evaluation_hash: str
    training: dict
    gate: dict
    reviews: dict = Field(default_factory=lambda: {"user_pairs": 0, "assistant_pairs": 0, "factual_issues": []})
    content_hash: str = ""

    @model_validator(mode="after")
    def dimensions(self) -> "RerankArtifact":
        """Reject corrupt coefficients, mismatched dimensions and unsupported scaling."""
        n = len(self.feature_names)
        if len(set(self.feature_names)) != n or any(len(x) != n for x in (self.coefficients, self.means, self.scales)):
            raise ValueError("artifact dimensions or names invalid")
        if not all(math.isfinite(x) for values in (self.coefficients, self.means, self.scales) for x in values):
            raise ValueError("artifact contains nonfinite parameters")
        if any(value <= 0 for value in self.scales):
            raise ValueError("artifact scales must be positive")
        if self.content_hash and self.content_hash != artifact_hash(self.model_dump()):
            raise ValueError("artifact integrity mismatch")
        return self


def artifact_hash(value: dict) -> str:
    """Canonical content identity, excluding the self-referential hash field."""
    return digest({key: item for key, item in value.items() if key != "content_hash"})


def validate_artifact(value: dict) -> dict:
    """Validate and canonicalize an experimental or approved JSON artifact."""
    output = RerankArtifact.model_validate(value).model_dump()
    output["content_hash"] = artifact_hash(output)
    return output


def promotion_eligible(value: dict) -> bool:
    """Require held-out quality and completed disjoint reviews before activation."""
    artifact = RerankArtifact.model_validate(value)
    gate, reviews = artifact.gate, artifact.reviews
    receipts = reviews.get("receipts", {})
    if not isinstance(receipts, dict) or set(receipts) != {"user", "assistant"} or reviews.get("dataset_hash") != artifact.dataset_hash:
        return False
    review_ids = []
    for person in ("user", "assistant"):
        receipt = receipts[person]
        if not isinstance(receipt, dict):
            return False
        answers = receipt.get("answers", [])
        if not isinstance(answers, list) or len(answers) != 20 or any(not isinstance(answer, dict) for answer in answers):
            return False
        ids = [answer.get("pair_id") for answer in answers]
        if any(not isinstance(identifier, str) for identifier in ids) or len(set(ids)) != 20:
            return False
        if receipt.get("hash") != digest(answers) or receipt.get("pair_ids") != sorted(ids):
            return False
        if not isinstance(receipt.get("packet_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["packet_hash"]):
            return False
        if any(answer.get("choice") not in ("A", "B", "tie", "insufficient") or answer.get("factual_issue") for answer in answers):
            return False
        review_ids.append(set(ids))
    if review_ids[0] & review_ids[1]:
        return False
    if artifact.tail_gap is not None:
        tail = gate.get("tail", {})
        if (tail.get("validated") is not True or tail.get("calibration_recall", 0) < .95
                or tail.get("golden_relevant_count", 0) < 50 or tail.get("golden_recall", 0) < .95
                or tail.get("golden_wilson_low", 0) < .90):
            return False
    return (not artifact.training.get("test_only", False)
            and gate.get("passed") is True
            and gate.get("protocol_valid") is True
            and gate.get("label_validation_passed") is True
            and gate.get("golden_families", 0) >= 12
            and gate.get("minimum_decisive_pairs_per_family", 0) >= 6
            and gate.get("macro_ndcg_delta", 0) >= .02
            and gate.get("ndcg_delta_ci_low", 0) > 0
            and gate.get("macro_pair_accuracy", 0) >= .65
            and gate.get("macro_pair_delta", 0) >= .03
            and gate.get("pair_delta_ci_low", 0) > 0
            and reviews.get("user_pairs", 0) >= 20
            and reviews.get("assistant_pairs", 0) >= 20
            and reviews.get("disjoint_blind") is True
            and not reviews.get("factual_issues", ["review_missing"]))


def tail_policy_hash(value: dict | None) -> str:
    """Give sort-only and calibrated-tail policies distinct durable identities."""
    return digest({"version": "tail-gap-v1", "gap": value.get("tail_gap") if value else None,
                   "promotion": promotion_policy().snapshot()})


def scaled_features(vector: FeatureVector, artifact: RerankArtifact) -> list[float]:
    """Mean-impute unavailable signals; explicit availability features remain observable."""
    if vector.schema_hash != artifact.feature_schema_hash or list(vector.names) != artifact.feature_names:
        raise ValueError("feature_schema_mismatch")
    return [(value - mean) / scale if available else 0.0
            for value, available, mean, scale in zip(vector.values, vector.available,
                                                    artifact.means, artifact.scales, strict=True)]


@dataclass(frozen=True)
class RerankScore:
    """One candidate's model score and additive explanation, excluding private facts."""

    candidate_id: UUID
    score: float | None
    contributions: dict[str, float]
    kind: Literal["recommended", "reserve"]


@dataclass(frozen=True)
class RerankOutcome:
    """An entire immutable shortlist outcome; fallback keeps every main card."""

    mode: Literal["learned", "main_fallback"]
    fallback_reason: str | None
    feature_schema_hash: str
    tail_policy_hash: str
    items: list[RerankScore]


def rerank_cards(vacancy: VacancyReady, candidates: list[MatchCandidate], cards: list[CandidateCard],
                 policy: ScoringPolicy, artifact: dict | None, schema: StrictSchema, *,
                 manual_approval: object | None = None, approval_error: str | None = None) -> RerankOutcome:
    """Score only selected main cards; exact compatibility failures explicitly preserve main order."""
    current = feature_schema(schema)
    reason = None
    model = None
    if artifact is None:
        reason = "artifact_absent"
    else:
        try:
            model = RerankArtifact.model_validate(artifact)
            from matching_engine.rerank_activation import DbManualApproval
            manual = (isinstance(manual_approval, DbManualApproval)
                      and manual_approval.artifact_hash == model.content_hash
                      and model.content_hash == artifact_hash(artifact) and model.tail_gap is None)
            if approval_error:
                reason = approval_error
            elif manual_approval is not None and not manual:
                reason = "manual_approval_invalid"
            elif not manual and not promotion_eligible(artifact):
                reason = "artifact_unapproved"
            elif model.strict_schema_hash != schema.schema_hash or model.feature_schema_hash != current.schema_hash:
                reason = "feature_schema_mismatch"
            elif model.main_policy_hash != policy_hash(policy):
                reason = "main_policy_mismatch"
            elif model.semantic_space_id != EMBEDDING_SPACE_ID:
                reason = "semantic_space_mismatch"
        except ValueError:
            reason = "artifact_invalid"
    tail_hash = tail_policy_hash(artifact)
    if reason:
        return RerankOutcome("main_fallback", reason, current.schema_hash, tail_hash,
                             promote([RerankScore(card.candidate_id, None, {}, "recommended") for card in cards],
                                     candidates, cards, promotion_policy()))
    assert model is not None
    inputs = {candidate.candidate_id: candidate for candidate in candidates}
    results = []
    for card in cards:
        candidate = inputs[card.candidate_id]
        vector = build_features(vacancy, candidate, card, schema)
        features = scaled_features(vector, model)
        contributions = {name: coefficient * value for name, coefficient, value in
                         zip(model.feature_names, model.coefficients, features, strict=True)}
        score = math.fsum(contributions.values())
        if not math.isfinite(score):
            raise ValueError("nonfinite_rerank_score")
        results.append((card, score, contributions))
    results.sort(key=lambda row: (-row[1], -row[0].explanation.score, str(row[0].candidate_id)))
    maximum = results[0][1] if results else 0.0
    return RerankOutcome("learned", None, current.schema_hash, tail_hash, promote([
        RerankScore(card.candidate_id, score, contributions,
                    "reserve" if model.tail_gap is not None and maximum - score > model.tail_gap else "recommended")
        for card, score, contributions in results], candidates, cards, promotion_policy()))
