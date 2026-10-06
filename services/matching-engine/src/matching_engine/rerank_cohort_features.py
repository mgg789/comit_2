"""Current runtime snapshots for sealed seven-candidate reranker cohorts."""

import fcntl
import json
import math
import os
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from matching_engine.combine_models import (
    MatchCandidate,
    ScoringPolicy,
    project_narrative_facts,
)
from matching_engine.combine_scoring import policy_hash, select_candidates
from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    HarrierEncoder,
    build_semantic_text,
)
from matching_engine.models import (
    GRADE_RANK,
    CandidateProfile,
    category_for,
    confirmed_strength,
)
from matching_engine.rerank_cohort_provider import validate_candidate
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_sample_features import (
    FIXED_PUBLISHED_AT,
    SEED_PATH,
    _candidate_profiles,
    verify_cached_sources,
)
from matching_engine.strict import (
    INITIAL_GROUPS,
    build_strict_vector,
    initial_schema,
    validate_coordinates,
)
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import build_strict_features, semantic_text
from matching_engine.vacancy_models import VacancyInput, VacancyReady, build_mode

BUILD_VERSION = "grounded-cohort-runtime-features-v1"
FAMILY_SIZE = 7
SEMANTIC_RECEIPT_VERSION = "grounded-semantic-entry-v1"


def _seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))[0]["profile"]


def candidate_profile(trait: dict, direction: str) -> CandidateProfile:
    """Project explicit traits into the seed's eligible profile without inherited skill claims.

    Registered skill groups and each fact's own status are retained. Missing scalar
    facts remain absent; prose cannot populate facts. Salaries are RUB and must
    overlap the fixed cohort vacancy. Seed narratives and preferences are removed.
    """
    validate_candidate(trait)
    levels, statuses = trait["levels"], trait["skill_statuses"]
    registered = {key for keys in INITIAL_GROUPS.values() for key in keys}
    if set(levels) != set(statuses) or not set(levels) <= registered:
        raise ValueError("trait skills must have matching registered levels and statuses")
    if type(trait["salary_min"]) is not int or not 50000 <= trait["salary_min"] <= 200000:
        raise ValueError("cohort salary_min must be between 50000 and 200000 RUB")
    raw = deepcopy(_seed())
    raw.update(primary_direction=direction, work_types=["remote"], city="Москва",
               salary={"from_rub": trait["salary_min"], "to_rub": min(200000, trait["salary_min"] + 20000)},
               description=trait["description"], experience=[], achievements=[], preferences=[], metadata={})
    raw["grade_result"]["grade"] = trait["grade"]
    for group, field in (("domain", "domains"), ("stack", "stacks"), ("soft_skill", "soft_skills")):
        raw[field] = [{"key": key, "level": levels[key], "status": statuses[key]}
                      for key in INITIAL_GROUPS[group] if key in levels]
    project = {"title": "Синтетический проект", "description": trait["description"], "status": trait["status"]}
    if trait["structured_facts_available"]:
        facts = {"domains": deepcopy(trait["project_domains"]), "stacks": deepcopy(trait["project_stacks"])}
        for value_key, status_key in (("duration_months", "duration_status"),
                                      ("production_support", "production_status"), ("role", "role_status")):
            if trait[value_key] is not None:
                facts[value_key] = {"value": trait[value_key], "status": trait[status_key]}
        project["facts"] = facts
    elif any(trait[key] is not None for key in ("duration_months", "production_support", "role")) or any(
        trait[key] for key in ("project_domains", "project_stacks")
    ):
        raise ValueError("unavailable structured facts must not contain values or references")
    raw["projects"] = [project]
    profile = CandidateProfile.model_validate(raw)
    validate_coordinates(profile, initial_schema())
    return profile


def legacy_traits(directory: Path, record: dict, indices: list[int]) -> list[dict]:
    """Verify pinned legacy sources and project selected original indices without moving folds.

    Uses actual historical typed profiles, including nested evidence that differed
    from an overwritten parent status. Does not read credentials, infer, or write.
    """
    batches = verify_cached_sources(directory, record)
    profiles = _candidate_profiles(record, batches, _seed())
    if len(indices) != len(set(indices)) or any(type(i) is not int or not 0 <= i < len(profiles) for i in indices):
        raise ValueError("legacy source indices must be unique and in range")
    traits = []
    for index in indices:
        profile = profiles[index]
        project = profile.projects[0]
        facts = project.facts
        skills = (*profile.domains, *profile.stacks, *profile.soft_skills)
        trait = {"grade": profile.grade_result.grade, "status": project.status,
                 "levels": {skill.key: skill.level for skill in skills},
                 "skill_statuses": {skill.key: skill.status for skill in skills},
                 "salary_min": profile.salary.from_rub, "description": profile.description,
                 "structured_facts_available": facts is not None,
                 "project_domains": [] if facts is None else [
                     {"key": ref.key, "status": ref.status} for ref in facts.domains],
                 "project_stacks": [] if facts is None else [
                     {"key": ref.key, "status": ref.status} for ref in facts.stacks]}
        for value_key, status_key in (("duration_months", "duration_status"),
                                      ("production_support", "production_status"), ("role", "role_status")):
            fact = getattr(facts, value_key) if facts is not None else None
            trait[value_key] = fact.value if fact is not None else None
            trait[status_key] = fact.status if fact is not None else None
        traits.append(trait)
    return traits


def _cache(path: Path) -> dict:
    contents = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(contents, dict):
        raise TypeError("semantic cache must be a model/text digest mapping")
    return contents


def _embedding(value: object) -> list[float]:
    if (not isinstance(value, list) or len(value) != 640
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in value)
            or not .99 <= math.sqrt(sum(v * v for v in value)) <= 1.01):
        raise ValueError("invalid role-specific Harrier cache vector")
    return list(value)


def _merge_embedding(path: Path, key: str, vector: list[float]) -> list[float]:
    """Serialize atomic cache merges; concurrent writers retain all previous entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = _cache(path)
        if key in current:
            return _embedding(current[key])
        current[key] = vector
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", delete=False) as output:
                temporary = output.name
                json.dump(current, output, ensure_ascii=False, allow_nan=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    return vector


def _seal_semantic_receipt(path: Path, expected: dict) -> None:
    """Publish an immutable complete receipt atomically, never replacing an existing seal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as output:
            temporary = output.name
            json.dump(expected, output, ensure_ascii=False, allow_nan=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _cache(path) != expected:
                raise ValueError("immutable semantic entry receipt differs") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def build_family_features(family_item: dict, vacancy_input: dict, traits: list[dict],
                          semantic_cache_path: Path, namespace: str,
                          class_profiles: list[CandidateProfile] | None = None,
                          class_source_lineage: dict[str, str] | None = None,
                          semantic_seed: dict | None = None
                          ) -> tuple[list[FeatureVector], list[float], list[int], dict]:
    """Build all seven actual current-policy vectors with sequential Harrier cache misses.

    Inputs carry source-order traits and a vacancy's explicit requirements. Returns
    source-order vectors/scores, grade/strength profile ordering, and deterministic
    snapshot provenance. Missing cards fail explicitly; no teacher or model scores
    enter the matching features. Namespace separates cohort and golden snapshots.
    Optional class_profiles is the complete same-direction pool, already including
    this family's profiles exactly once per source candidate; it is never extended
    with the family again. None retains the explicit seven-profile fallback.
    Global profiles must be unique. Optional class_source_lineage binds every
    frozen family/index source identity to its exact JSON profile hash, rejecting
    extra or omitted pool members before any embedding inference.
    Verified semantic_seed entries must retain exact original vector hashes. New
    entries require immutable namespace-scoped receipts. After an interrupted cache
    write, a missing receipt is recovered only by exact local re-encoding equality.
    """
    if len(traits) != FAMILY_SIZE or not namespace:
        raise ValueError("cohort family must contain seven traits and a namespace")
    semantic_cache_path = Path(semantic_cache_path)
    semantics = _cache(semantic_cache_path)
    if semantic_seed is not None and not isinstance(semantic_seed, dict):
        raise TypeError("semantic seed must be a verified digest mapping")
    seed_semantics = deepcopy(semantic_seed) if semantic_seed is not None else {}
    receipt_directory = semantic_cache_path.parent / f"{semantic_cache_path.stem}.entries" / digest(namespace)
    semantic_keys, semantic_roles, semantic_receipts = [], [], []

    def encoded(encoder: HarrierEncoder, text: str) -> list[float]:
        key = digest({"model": encoder.model_version, "text": text})
        vector = _embedding(semantics[key]) if key in semantics else None
        if key in seed_semantics:
            seed_vector = _embedding(seed_semantics[key])
            if vector is None:
                vector = _merge_embedding(semantic_cache_path, key, seed_vector)
                semantics[key] = vector
            if digest(vector) != digest(seed_vector):
                raise ValueError("semantic seed vector differs from original verified cache")
            source = {"key": key, "source": "verified_seed", "vector_hash": digest(vector)}
        else:
            receipt_path = receipt_directory / f"{key}.json"
            receipt = {"version": SEMANTIC_RECEIPT_VERSION, "namespace": namespace, "key": key,
                       "model": encoder.model_version, "text_hash": digest(text),
                       "vector_hash": digest(vector) if vector is not None else None}
            if receipt_path.exists():
                receipt["receipt_hash"] = digest(receipt)
                if vector is None or _cache(receipt_path) != receipt:
                    raise ValueError("semantic vector differs from immutable entry receipt")
            else:
                # Both initial inference and interrupted local-write recovery remain sequential.
                inferred = _embedding(encoder.encode(text))
                if vector is not None and digest(inferred) != digest(vector):
                    raise ValueError("unsealed semantic cache vector differs from current Harrier inference")
                vector = _merge_embedding(semantic_cache_path, key, inferred)
                if digest(vector) != digest(inferred):
                    raise ValueError("concurrent semantic cache vector differs from current Harrier inference")
                semantics[key] = vector
                receipt["vector_hash"] = digest(vector)
                receipt["receipt_hash"] = digest(receipt)
                _seal_semantic_receipt(receipt_path, receipt)
            source = {"key": key, "source": "local_harrier_receipt", "receipt_hash": receipt["receipt_hash"],
                      "vector_hash": receipt["vector_hash"]}
        semantic_receipts.append(source)
        semantic_keys.append(key)
        semantic_roles.append({"key": key, "model": encoder.model_version, "role": encoder.role,
                               "text_hash": digest(text)})
        return vector

    family_id = family_item["family_id"]
    identifier = uuid5(NAMESPACE_URL, f"{namespace}/{family_id}")
    raw_vacancy = deepcopy(vacancy_input)
    # Legacy teacher targets use salary as a small nested mapping, never a runtime field.
    salary = raw_vacancy.pop("salary", {})
    for field, value in (("source_revision", 1), ("daily", "full_time"),
                         ("zp_min", salary.get("min", 50000)), ("zp_max", salary.get("max", 200000)),
                         ("type", "remote"), ("city", "Москва")):
        raw_vacancy.setdefault(field, value)
    raw_vacancy.update(owner_id=str(identifier), status="open")
    source = VacancyInput.model_validate(raw_vacancy)
    schema = initial_schema()
    strict_vector, mask = build_strict_features(source, schema)
    profiles = [candidate_profile(trait, family_item["direction"]) for trait in traits]
    pool = profiles if class_profiles is None else class_profiles
    if not pool or any(category_for(profile).specialization != family_item["direction"] for profile in pool):
        raise ValueError("class profiles must be a nonempty eligible same-direction pool")
    profile_hashes = [digest(profile.model_dump(mode="json")) for profile in profiles]
    pool_hashes = [digest(profile.model_dump(mode="json")) for profile in pool]
    if class_profiles is not None and len(set(pool_hashes)) != len(pool_hashes):
        raise ValueError("duplicate class profile snapshots")
    if class_source_lineage is not None and (
        not isinstance(class_source_lineage, dict)
        or any(type(key) is not str or not key for key in class_source_lineage)
        or any(type(value) is not str or len(value) != 64
               or any(c not in "0123456789abcdef" for c in value)
               for value in class_source_lineage.values())
        or Counter(class_source_lineage.values()) != Counter(pool_hashes)
    ):
        raise ValueError("class source lineage must exactly match frozen profile snapshots")
    if Counter(profile_hashes) - Counter(pool_hashes):
        raise ValueError("class profiles must already contain every source family profile")
    pool_ranks = [(GRADE_RANK[profile.grade_result.grade], confirmed_strength(profile)) for profile in pool]
    document, query = HarrierEncoder(), HarrierEncoder("vacancy_query")
    query_text = semantic_text(source)
    semantic = encoded(query, query_text) if query_text else None
    vacancy = VacancyReady(**source.model_dump(by_alias=True), vacancy_id=identifier, revision=1,
        build_mode=build_mode(source), strict_vector=strict_vector, requirement_mask=mask,
        strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash, semantic_vector=semantic,
        semantic_model_version=query.model_version if semantic is not None else None,
        semantic_space_id=EMBEDDING_SPACE_ID if semantic is not None else None,
        semantic_available=semantic is not None, published_at=FIXED_PUBLISHED_AT)
    strengths = [confirmed_strength(profile) for profile in profiles]
    ordering = sorted(range(len(profiles)), key=lambda i: (-GRADE_RANK[profiles[i].grade_result.grade], -strengths[i], i))
    candidates = []
    for index, profile in enumerate(profiles):
        rank = 1 + sum(other > (GRADE_RANK[profile.grade_result.grade], strengths[index]) for other in pool_ranks)
        candidates.append(MatchCandidate(candidate_id=uuid5(NAMESPACE_URL, f"{namespace}/{family_id}/{index}"),
            profile_revision=1, display_name=f"Synthetic {index}", city=profile.city, salary=profile.salary,
            category=category_for(profile), rank=rank, class_size=len(pool),
            rating_score=1 - (rank - 1) / max(len(pool) - 1, 1), confirmed_strength=strengths[index],
            strict_vector=build_strict_vector(profile, schema), strict_evidence=build_strict_evidence(profile, schema),
            strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash,
            semantic_vector=encoded(document, build_semantic_text(profile)), semantic_model_version=document.model_version,
            semantic_space_id=EMBEDDING_SPACE_ID, published_at=FIXED_PUBLISHED_AT, resume_url="/synthetic-public.pdf",
            work_types=profile.work_types, narrative_facts=project_narrative_facts({"projects": profile.projects,
                "experience": profile.experience, "achievements": profile.achievements})))
    policy = ScoringPolicy()
    selected = select_candidates(vacancy, candidates, policy, len(candidates))
    cards = {card.candidate_id: card for card in selected.items}
    if set(cards) != {candidate.candidate_id for candidate in candidates}:
        raise ValueError(f"cohort family strict filter excluded cards: {selected.filter_counts}")
    vectors = [build_features(vacancy, candidate, cards[candidate.candidate_id], schema) for candidate in candidates]
    scores = [cards[candidate.candidate_id].explanation.score for candidate in candidates]
    provenance = {"build_version": BUILD_VERSION, "namespace": namespace, "family_id": family_id,
        "current_feature_schema_hash": feature_schema(schema).schema_hash, "current_policy_hash": policy_hash(policy),
        "strict_schema_hash": schema.schema_hash, "strict_dimensions": len(schema.coordinates),
        "seed_profile_hash": digest(_seed()), "trait_hashes": [digest(trait) for trait in traits],
        "semantic_space_id": EMBEDDING_SPACE_ID, "semantic_keys": semantic_keys, "semantic_roles": semantic_roles,
        "semantic_entry_receipts": semantic_receipts,
        "semantic_seed_hash": digest(seed_semantics) if semantic_seed is not None else None,
        "semantic_vectors_hash": digest({key: semantics[key] for key in sorted(set(semantic_keys))}),
        "profile_snapshot_hashes": profile_hashes,
        "class_profile_snapshot_hashes": sorted(pool_hashes), "class_size": len(pool),
        "class_rank_scope": "family" if class_profiles is None else "global_direction_pool",
        "class_source_lineage": None if class_source_lineage is None else dict(sorted(class_source_lineage.items())),
        "candidate_snapshot_hashes": [digest(candidate.model_dump(mode="json")) for candidate in candidates],
        "vacancy_snapshot_hash": digest(vacancy.model_dump(mode="json")), "published_at": FIXED_PUBLISHED_AT.isoformat(),
        "source_profiles": len(profiles), "passed_profiles": selected.passed_count,
        "mode": "current_runtime_features_sequential_harrier_cache"}
    return vectors, scores, ordering, provenance
