"""Shared, registry-bound numeric features for offline training and R₀ runtime."""

import hashlib
import json
import math
from dataclasses import dataclass

from matching_engine.combine_models import CandidateCard, MatchCandidate, ScoringPolicy
from matching_engine.embeddings import EMBEDDING_SPACE_ID
from matching_engine.models import GRADE_RANK, StrictSchema
from matching_engine.strict import schema_hash
from matching_engine.vacancy_models import VacancyReady

FEATURE_VERSION = "rerank-features-v1"
STATUSES = ("confirmed", "partial", "self_reported", "pending", "rejected")
GROUPS = ("domain", "stack", "soft_skill")
ROLES = ("developer", "lead", "architect", "analyst", "designer", "researcher", "qa", "devops", "other")
CONFIDENCE = ScoringPolicy().confidence
COORDINATE_SIGNALS = ("target", "level", "proximity", *STATUSES)
BASE_SIGNALS = ("main_score", "rating", "grade", "rank_percentile", "semantic_cosine",
                "salary_overlap", "salary_affordability", "salary_midpoint_ratio")
GROUP_SIGNALS = ("coverage", "proximity", "confirmed_share", "depth", "weakest", "balance")
NARRATIVE_SIGNALS = ("relevance", "count", "duration", "duration_evidence", "evidence",
                     "production", "production_evidence", "role_evidence", *ROLES)
INTERACTIONS = ("coverage_grade", "proximity_confirmed", "project_relevance_evidence",
                "experience_relevance_evidence", "salary_technical_fit")


@dataclass(frozen=True)
class FeatureSchema:
    """Ordered feature names and compatibility hash, including frozen adjacent stages."""

    names: tuple[str, ...]
    schema_hash: str
    strict_schema_hash: str
    version: str = FEATURE_VERSION


@dataclass(frozen=True)
class FeatureVector:
    """Finite raw values with availability; train-only centering imputes missing to zero.

    Every raw signal has an explicit numeric availability feature at the end.
    Missing raw values are placeholders, not observed zero measurements.
    """

    names: tuple[str, ...]
    values: tuple[float, ...]
    available: tuple[bool, ...]
    schema_hash: str


def feature_schema(schema: StrictSchema) -> FeatureSchema:
    """Bind exact registry order, the current main policy and pinned Harrier space; never pad new keys."""
    if schema.schema_hash != schema_hash(schema.coordinates) or any(
        coordinate.index != index for index, coordinate in enumerate(schema.coordinates)
    ):
        raise ValueError("invalid strict registry order/hash")
    raw = list(BASE_SIGNALS)
    raw.extend(f"{coord.group}.{coord.key}.{signal}" for coord in schema.coordinates for signal in COORDINATE_SIGNALS)
    raw.extend(f"{group}.{signal}" for group in GROUPS for signal in GROUP_SIGNALS)
    raw.extend(f"{kind}.{signal}" for kind in ("project", "experience", "achievement") for signal in NARRATIVE_SIGNALS)
    raw.extend(INTERACTIONS)
    names = (*raw, *(f"available.{name}" for name in raw))
    payload = {"version": FEATURE_VERSION, "names": names, "registry": schema.schema_hash,
               "main_policy": ScoringPolicy().model_dump(), "semantic_space": EMBEDDING_SPACE_ID,
               "missing": "train-observed-mean-plus-availability:v1", "confidence": CONFIDENCE}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return FeatureSchema(names, digest, schema.schema_hash)


def build_features(vacancy: VacancyReady, candidate: MatchCandidate, card: CandidateCard,
                   schema: StrictSchema) -> FeatureVector:
    """Compute exactly the same raw pair features from immutable train/runtime snapshots.

    Only explicit requirements define relevance. Unknown and rejected facts earn
    no technical credit; durations use the longest relevant fact, never a sum
    of potentially overlapping jobs. Narratives never supply text-derived facts.
    """
    contract = feature_schema(schema)
    size = len(schema.coordinates)
    if any(len(values) != size for values in (vacancy.strict_vector, vacancy.requirement_mask,
                                              candidate.strict_vector, candidate.strict_evidence)):
        raise ValueError("incompatible strict dimensions")
    if any(item.strict_schema_hash != schema.schema_hash or item.strict_schema_version != schema.version
           for item in (vacancy, candidate)):
        raise ValueError("incompatible strict schema")
    if card.candidate_id != candidate.candidate_id or card.profile_revision != candidate.profile_revision:
        raise ValueError("card does not belong to candidate snapshot")
    raw: dict[str, tuple[float, bool]] = {}

    def put(name: str, value: float, available: bool = True) -> None:
        if not math.isfinite(value):
            raise ValueError(f"non-finite feature: {name}")
        raw[name] = (float(value) if available else 0.0, available)

    put("main_score", card.explanation.score)
    put("rating", candidate.rating_score)
    put("grade", GRADE_RANK[candidate.category.grade] / 3)
    put("rank_percentile", 1 if candidate.class_size == 1 else 1 - (candidate.rank - 1) / (candidate.class_size - 1))
    semantic = card.explanation.semantic_cosine
    semantic_available = semantic is not None and vacancy.semantic_space_id == candidate.semantic_space_id == EMBEDDING_SPACE_ID
    put("semantic_cosine", semantic or 0, semantic_available)
    # An open expectation is bounded by this vacancy for finite pair features.
    candidate_upper = candidate.salary.to_rub if candidate.salary.to_rub is not None else max(candidate.salary.from_rub, vacancy.zp_max)
    overlap = min(vacancy.zp_max, candidate_upper) - max(vacancy.zp_min, candidate.salary.from_rub)
    span = max(vacancy.zp_max, candidate_upper) - min(vacancy.zp_min, candidate.salary.from_rub)
    put("salary_overlap", max(0, overlap) / max(1, span))
    put("salary_affordability", (vacancy.zp_max - candidate.salary.from_rub) / max(1, vacancy.zp_max))
    put("salary_midpoint_ratio", (candidate.salary.from_rub + candidate_upper) / max(1, vacancy.zp_min + vacancy.zp_max))
    grouped: dict[str, list[tuple[float, float, float, bool]]] = {group: [] for group in GROUPS}
    required_keys: set[tuple[str, str]] = set()
    for coord in schema.coordinates:
        idx = coord.index
        evidence = candidate.strict_evidence[idx]
        if (evidence.index, evidence.group, evidence.key) != (idx, coord.group, coord.key):
            raise ValueError("invalid evidence coordinate order")
        target, value = vacancy.strict_vector[idx], candidate.strict_vector[idx]
        if any(not math.isfinite(x) or not 0 <= x <= 1 for x in (target, value)):
            raise ValueError("strict coordinates must be finite normalized levels")
        required = vacancy.requirement_mask[idx]
        declared = evidence.status is not None
        active = declared and evidence.status != "rejected"
        level = evidence.level / 7
        prefix = f"{coord.group}.{coord.key}"
        put(f"{prefix}.target", target, required)
        put(f"{prefix}.level", level, declared)
        proximity = 1 - abs(level - target) if active else 0.0
        put(f"{prefix}.proximity", proximity, required and declared)
        for status in STATUSES:
            put(f"{prefix}.{status}", float(evidence.status == status), declared)
        if required:
            if coord.group in ("domain", "stack"):
                required_keys.add((coord.group, coord.key))
            grouped[coord.group].append((proximity, level if active else 0.0,
                                        float(evidence.status == "confirmed"), bool(active)))
    for group, entries in grouped.items():
        count = len(entries)
        available = count > 0
        levels = [entry[1] for entry in entries]
        proximity = sum(entry[0] for entry in entries) / max(1, count)
        put(f"{group}.coverage", sum(entry[3] for entry in entries) / max(1, count), available)
        put(f"{group}.proximity", proximity, available)
        put(f"{group}.confirmed_share", sum(entry[2] for entry in entries) / max(1, count), available)
        put(f"{group}.depth", sum(levels) / max(1, count), available)
        put(f"{group}.weakest", min((entry[0] for entry in entries), default=0), available)
        mean = sum(levels) / max(1, count)
        put(f"{group}.balance", 1 - math.sqrt(sum((level - mean) ** 2 for level in levels) / max(1, count)), available)
    for kind in ("project", "experience", "achievement"):
        claims = [claim for claim in candidate.narrative_facts if claim.kind == kind and claim.status != "rejected"]
        relevant = []
        for claim in claims:
            matched = [ref for group, refs in (("domain", claim.facts.domains), ("stack", claim.facts.stacks))
                       for ref in refs if ref.status != "rejected" and (group, ref.key) in required_keys]
            if matched:
                relevant.append((claim, matched))
        available = any(claim.facts.domains or claim.facts.stacks for claim in claims) and bool(required_keys)
        keys = {(group, ref.key) for claim, _ in relevant
                for group, refs in (("domain", claim.facts.domains), ("stack", claim.facts.stacks))
                for ref in refs if ref.status != "rejected" and (group, ref.key) in required_keys}
        put(f"{kind}.relevance", len(keys) / max(1, len(required_keys)), available)
        put(f"{kind}.count", min(len(relevant), 10) / 10, available)
        durations = [claim.facts.duration_months.value for claim, _ in relevant
                     if claim.facts.duration_months is not None and claim.facts.duration_months.status != "rejected"]
        put(f"{kind}.duration", min(max(durations, default=0), 120) / 120, bool(durations))
        duration_evidence = [min(CONFIDENCE[claim.status], CONFIDENCE[claim.facts.duration_months.status])
                             for claim, _ in relevant if claim.facts.duration_months is not None
                             and claim.facts.duration_months.status != "rejected"]
        put(f"{kind}.duration_evidence", sum(duration_evidence) / max(1, len(duration_evidence)), bool(duration_evidence))
        evidence = [min(CONFIDENCE[claim.status], CONFIDENCE[ref.status]) for claim, refs in relevant for ref in refs]
        put(f"{kind}.evidence", sum(evidence) / max(1, len(evidence)), available)
        production = [claim.facts.production_support for claim, _ in relevant
                      if claim.facts.production_support is not None and claim.facts.production_support.status != "rejected"]
        put(f"{kind}.production", sum(float(fact.value) * CONFIDENCE[fact.status] for fact in production) / max(1, len(production)), bool(production))
        put(f"{kind}.production_evidence", sum(CONFIDENCE[fact.status] for fact in production) / max(1, len(production)), bool(production))
        roles = [claim.facts.role for claim, _ in relevant
                 if claim.facts.role is not None and claim.facts.role.status != "rejected"]
        put(f"{kind}.role_evidence", sum(CONFIDENCE[fact.status] for fact in roles) / max(1, len(roles)), bool(roles))
        for role in ROLES:
            put(f"{kind}.{role}", sum(float(fact.value == role) * CONFIDENCE[fact.status] for fact in roles) / max(1, len(roles)), bool(roles))
    available_groups = [group for group in GROUPS if grouped[group]]
    coverage = sum(raw[f"{group}.coverage"][0] for group in available_groups) / max(1, len(available_groups))
    technical = sum(raw[f"{group}.proximity"][0] for group in available_groups) / max(1, len(available_groups))
    confirmation = sum(raw[f"{group}.confirmed_share"][0] for group in available_groups) / max(1, len(available_groups))
    put("coverage_grade", coverage * raw["grade"][0], bool(available_groups))
    put("proximity_confirmed", technical * confirmation, bool(available_groups))
    for kind in ("project", "experience"):
        put(f"{kind}_relevance_evidence", raw[f"{kind}.relevance"][0] * raw[f"{kind}.evidence"][0], raw[f"{kind}.relevance"][1])
    put("salary_technical_fit", raw["salary_affordability"][0] * technical, bool(available_groups))
    signals = tuple(raw)
    values = tuple(raw[name][0] for name in signals) + tuple(float(raw[name][1]) for name in signals)
    availability = tuple(raw[name][1] for name in signals) + (True,) * len(signals)
    if (*signals, *(f"available.{name}" for name in signals)) != contract.names:
        raise ValueError("feature builder order differs from schema")
    return FeatureVector(contract.names, values, availability, contract.schema_hash)
