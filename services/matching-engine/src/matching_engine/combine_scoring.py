"""Deterministic strict gating and evidence-aware main ranking; no reranker or LLM."""

import hashlib
import json
import math
import unicodedata
from collections import Counter
from uuid import UUID

from matching_engine.combine_models import (
    CandidateCard,
    FilterDecision,
    MainSelectionResult,
    MatchCandidate,
    MatchedFeature,
    ScoreBreakdown,
    ScoringPolicy,
)
from matching_engine.vacancy_models import VacancyReady

# A bounded alias registry is applied identically to candidate, vacancy and relocation cities.
_CITY_ALIASES = {"moscow": "москва", "moskva": "москва", "спб": "санкт-петербург",
                 "spb": "санкт-петербург", "saint petersburg": "санкт-петербург",
                 "st. petersburg": "санкт-петербург"}


def policy_hash(policy: ScoringPolicy) -> str:
    """Fingerprint every manual weight and evidence coefficient in canonical JSON."""
    return hashlib.sha256(json.dumps(policy.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalized_city(city: str) -> str:
    """Compare supplied city names with Unicode, whitespace and case normalization only."""
    value = " ".join(unicodedata.normalize("NFKC", city).casefold().replace("ё", "е").split())
    for prefix in ("г. ", "город "):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return _CITY_ALIASES.get(value, value)


def strict_filter(vacancy: VacancyReady, candidate: MatchCandidate, *, excluded: bool = False) -> FilterDecision:
    """Gate class, inclusive RUB overlap, explicit work type and physical location consent."""
    reasons: list[str] = []
    if excluded:
        reasons.append("selection_state_excluded")
    if vacancy.class_key is not None and candidate.category.specialization != vacancy.class_key:
        reasons.append("class_mismatch")
    if max(vacancy.zp_min, candidate.salary.from_rub) > min(vacancy.zp_max, candidate.salary.to_rub if candidate.salary.to_rub is not None else vacancy.zp_max):
        reasons.append("salary_disjoint")
    if candidate.work_types is None:
        reasons.append("work_type_unknown")
    elif vacancy.type not in candidate.work_types:
        reasons.append("work_type_mismatch")
    city, destination = normalized_city(candidate.city), normalized_city(vacancy.city)
    if not city or not destination:
        reasons.append("location_unknown")
    elif vacancy.type != "remote" and city != destination:
        if candidate.relocation_ready is None:
            reasons.append("relocation_unknown")
        elif not candidate.relocation_ready:
            reasons.append("relocation_not_ready")
        elif candidate.relocation_cities and destination not in {
            normalized_city(value) for value in candidate.relocation_cities
        }:
            reasons.append("relocation_destination_mismatch")
    if candidate.strict_schema_version != vacancy.strict_schema_version or candidate.strict_schema_hash != vacancy.strict_schema_hash:
        reasons.append("strict_schema_mismatch")
    factors: list[str] = []
    if not reasons:
        factors = ["class_match" if vacancy.class_key is not None else "class_unrestricted",
                   "salary_overlap", "work_type_accepted", "strict_schema_current"]
        factors.append("remote_location" if vacancy.type == "remote" else (
            "same_city" if city == destination else "relocation_accepted"
        ))
    return FilterDecision(passed=not reasons, reasons=reasons, factors=factors)


def score_candidate(vacancy: VacancyReady, candidate: MatchCandidate, policy: ScoringPolicy) -> ScoreBreakdown:
    """Score masked target proximity with evidence confidence, then a bounded semantic correction.

    Levels are relative target coordinates, not hard minimum thresholds. Missing
    and rejected evidence cannot earn agreement with an explicit zero target.
    The rank percentile is supplied from the entire class before strict gating.
    """
    size = len(vacancy.strict_vector)
    if len(vacancy.requirement_mask) != size or len(candidate.strict_vector) != size or len(candidate.strict_evidence) != size:
        raise ValueError("incompatible strict dimensions")
    features: list[MatchedFeature] = []
    for index, required in enumerate(vacancy.requirement_mask):
        evidence = candidate.strict_evidence[index]
        if evidence.index != index:
            raise ValueError("evidence coordinate order is invalid")
        if not required:
            continue
        target, level = vacancy.strict_vector[index], candidate.strict_vector[index]
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (target, level)):
            raise ValueError("strict levels must be finite normalized coordinates")
        confidence = policy.confidence[evidence.status] if evidence.status is not None else 0.0
        agreement = 1 - abs(level - target) if confidence > 0 else 0.0
        features.append(MatchedFeature(index=index, group=evidence.group, key=evidence.key,
            target_level=target * 7, candidate_level=evidence.level, status=evidence.status,
            agreement=agreement, confidence=confidence, contribution=agreement * confidence))
    strict_score = sum(feature.contribution for feature in features) / len(features) if features else None
    grade_status = candidate.category.grade_status
    grade_confidence = (policy.declared_grade_confidence
                        if grade_status == "self_reported" else 1.0)
    rating_weight = policy.rating_weight
    strict_weight = policy.strict_weight if strict_score is not None else 0.0
    denominator = rating_weight + strict_weight
    rating_part = rating_weight / denominator * candidate.rating_score * grade_confidence
    strict_part = strict_weight / denominator * strict_score if strict_score is not None else 0.0
    base = rating_part + strict_part
    cosine: float | None = None
    unavailable: str | None = None
    if vacancy.semantic_vector is None:
        unavailable = "vacancy_bio_absent"
    elif vacancy.semantic_space_id != candidate.semantic_space_id:
        unavailable = "semantic_space_mismatch"
    else:
        if len(vacancy.semantic_vector) != 640 or len(candidate.semantic_vector) != 640:
            raise ValueError("semantic dimensions must be 640")
        if not all(math.isfinite(value) for value in (*vacancy.semantic_vector, *candidate.semantic_vector)):
            raise ValueError("semantic vectors must be finite")
        candidate_norm = math.sqrt(sum(value * value for value in candidate.semantic_vector))
        vacancy_norm = math.sqrt(sum(value * value for value in vacancy.semantic_vector))
        if not 0.99 <= candidate_norm <= 1.01 or not 0.99 <= vacancy_norm <= 1.01:
            raise ValueError("semantic vectors must be unit normalized")
        cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(
            vacancy.semantic_vector, candidate.semantic_vector, strict=True
        )) / (candidate_norm * vacancy_norm)))
    semantic_part = 0.0 if cosine is None else policy.semantic_correction * (
        (1 - base) * max(0.0, cosine) + base * min(0.0, cosine)
    )
    return ScoreBreakdown(rating_score=candidate.rating_score, strict_score=strict_score,
        semantic_cosine=cosine, semantic_unavailable_reason=unavailable,
        rating_contribution=rating_part, strict_contribution=strict_part,
        semantic_contribution=semantic_part, score=base + semantic_part,
        top_matched_features=sorted(features, key=lambda item: (-item.contribution, item.index))[:5],
        grade_status=grade_status, grade_confidence=grade_confidence)


def select_candidates(vacancy: VacancyReady, candidates: list[MatchCandidate], policy: ScoringPolicy,
                      top_k: int, excluded_ids: set[UUID] | None = None) -> MainSelectionResult:
    """Scan all live candidates, collect explicit exclusions and return deterministic main top-k."""
    if not 1 <= top_k <= 500:
        raise ValueError("top_k must be between one and 500")
    counts: Counter[str] = Counter()
    selected: list[tuple[MatchCandidate, ScoreBreakdown]] = []
    for candidate in candidates:
        decision = strict_filter(vacancy, candidate, excluded=candidate.candidate_id in (excluded_ids or set()))
        if not decision.passed:
            counts.update(decision.reasons)
            continue
        selected.append((candidate, score_candidate(vacancy, candidate, policy)))
    selected.sort(key=lambda item: (-item[1].score, -item[1].rating_score,
        -(item[1].strict_score or 0.0), -(item[1].semantic_cosine or 0.0), str(item[0].candidate_id)))
    cards = [CandidateCard(candidate_id=value.candidate_id, profile_revision=value.profile_revision,
        position=position, display_name=value.display_name, city=value.city, salary=value.salary,
        category=value.category, rank=value.rank, confirmed_strength=value.confirmed_strength,
        resume_url=value.resume_url, explanation=score,
        filter_factors=strict_filter(vacancy, value).factors)
        for position, (value, score) in enumerate(selected[:top_k], start=1)]
    return MainSelectionResult(scanned_count=len(candidates), passed_count=len(selected),
                               filter_counts=dict(counts), items=cards)
