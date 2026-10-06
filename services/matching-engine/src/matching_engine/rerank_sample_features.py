"""Read-only reconstruction of historical synthetic families under the current policy."""

import itertools
import json
import math
from copy import deepcopy
from datetime import UTC, datetime
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
from matching_engine.rerank_experiment import _CacheOnlyTeacher, family_manifest
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    GENERATION_MODEL,
    GENERATION_MODELS,
    PROMPT_VERSION,
    QWEN,
    generation_identity,
    teacher_identity,
    validate_traits,
)
from matching_engine.strict import build_strict_vector, initial_schema
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import build_strict_features
from matching_engine.vacancy_models import VacancyInput, VacancyReady

REBUILD_VERSION = "cached-synthetic-family-current-features-v1"
FIXED_PUBLISHED_AT = datetime(2026, 10, 5, 12, tzinfo=UTC)
SEED_PATH = Path(__file__).resolve().parents[4] / "data/seed/candidate-pipeline.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _approved_generation_source_identity(request: dict) -> str:
    """Allow current identities and the explicitly preserved historical Qwen v1 cache."""
    model = request.get("model")
    if model not in (QWEN, *GENERATION_MODELS):
        raise ValueError("incompatible generation source model")
    if model == QWEN:
        # Legacy LocalProviders identities have exactly these four fields.
        expected = {"path": "/api/v1/chat/completions", "payload": request.get("payload"),
                    "prompt_version": PROMPT_VERSION, "model": QWEN}
        if request == expected:
            return "legacy_local_qwen_cache_only"
    else:
        expected = {"path": "/v1/chat/completions", "kind": "generation", **generation_identity(model)}
        if all(request.get(field) == value for field, value in expected.items()):
            return "current_remote_generation_identity"
        # test_rerank_resume's sealed partial-batch reuse explicitly retains v1.
        # This exception is cache verification only, never generator configuration.
        historical = {**expected, "model": GENERATION_MODEL, "prompt_version": "rerank-sverk-generation-v1"}
        if model == GENERATION_MODEL and all(request.get(field) == value for field, value in historical.items()):
            return "historical_qwen_v1_cache_only"
    raise ValueError("unapproved generation source identity")


def _generation_sources(directory: Path, record: dict) -> list[dict]:
    """Resolve only sealed receipt keys; unrelated malformed probes are not sources."""
    batches = record.get("generation_batches", [])
    item = record["manifest"]
    if [batch.get("batch") for batch in batches] != [1, 2]:
        raise ValueError("missing or unordered generation source batches")
    for batch in batches:
        key = batch["source_cache_key"]
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid generation source key")
        path = directory / "provider-cache" / f"{key}.json"
        if not path.is_file():
            raise ValueError("missing pinned generation source receipt")
        cached = _read(path)
        request, response = cached["request"], cached["response"]
        if (cached["request_hash"] != key or digest(request) != key
                or digest(response) != cached["response_hash"]):
            raise ValueError("generation source cache integrity failed")
        _approved_generation_source_identity(request)
        content = response["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        raw = json.loads(content)
        raw = raw["profiles"] if isinstance(raw, dict) and set(raw) == {"profiles"} else raw
        if not isinstance(raw, list) or len(raw) != 5:
            raise ValueError("generation source must contain five raw profiles")
        instruction = request["payload"]["messages"][0]["content"]
        if item["family_id"] == "backend-01" and batch["batch"] == 1:
            if raw != json.loads((directory / "pilot-profiles.json").read_text(encoding="utf-8")):
                raise ValueError("pilot source profiles differ")
        elif (f"for {item['direction']}." not in instruction or
              f"Scenario: {item['preference']} Batch {batch['batch']}." not in instruction):
            raise ValueError("generation source does not belong to family/batch")
        accepted, dropped = [], []
        for index, trait in enumerate(raw):
            try:
                validate_traits(trait)
            except (ValueError, TypeError, KeyError) as exc:
                dropped.append({"source_index": index, "raw_hash": digest(trait),
                    "error_type": type(exc).__name__, "reason": str(exc) if isinstance(exc, ValueError)
                    else "invalid trait structure"})
            else:
                accepted.append(trait)
        expected = {"family_id": item["family_id"], "batch": batch["batch"],
            "generator": {field: request[field] for field in ("model", "prompt_version")},
            "source_cache_key": key, "source_response_hash": cached["response_hash"], "raw_count": len(raw),
            "traits": accepted, "dropped": dropped, "reuse_policy": "verified_cache_only"}
        expected["receipt_hash"] = digest(expected)
        if expected != batch:
            raise ValueError("family generation provenance differs from cached source")
    transformed = deepcopy([trait for batch in batches for trait in batch["traits"]])
    for index, trait in enumerate(transformed):
        trait["grade"] = ("intern", "junior", "middle", "senior")[(index + int(item["family_id"][-2:])) % 4]
        if index == len(transformed) - 1:
            trait["status"] = "rejected"
        trait["structured_facts_available"] = index % 4 != 0
        if not trait["structured_facts_available"]:
            trait["duration_months"] = None
            trait["production_support"] = None
    if transformed != record["traits"]:
        raise ValueError("family facts differ from cached sources and controlled variants")
    return batches


def _source_vacancy(record: dict) -> dict:
    item = record["manifest"]
    targets = {"domens": {}, "stacks": {}, "soft_skills": {}} if item["class_only"] else {
        "domens": {"sql": 4}, "stacks": {"python": 5, "react": 3}, "soft_skills": {"teamwork": 3}}
    expected = {"class": item["direction"], **targets,
                "bio": "" if item["bio_absent"] else "Разработка API и интерфейсов, SQL, взаимодействие в команде",
                "salary": {"min": 50000, "max": 200000}}
    if record["vacancy"] != expected:
        raise ValueError("source vacancy differs from generation recipe")
    return expected


def verify_cached_sources(directory: Path, record: dict) -> list[dict]:
    """Verify seals, labels, swaps and generation using cache only, returning raw batches.

    Historical feature hashes are retained as source metadata, not asserted to be
    compatible with today's policy. Exact current feature names and source sizes
    are required; this does not relax the production family receipt validator.
    Missing provider directories fail before constructing a provider.
    """
    if not (directory / "provider-cache").is_dir():
        raise ValueError("missing provider source cache")
    if record.get("record_hash") != digest({k: v for k, v in record.items() if k != "record_hash"}):
        raise ValueError("family record integrity failed")
    if record.get("manifest") not in family_manifest():
        raise ValueError("family manifest differs from frozen source allocation")
    _source_vacancy(record)
    size = len(record["traits"])
    if not 2 <= size <= 10 or record.get("teacher") != teacher_identity():
        raise ValueError("cached family dimensions or teacher mismatch")
    if any(len(record[key]) != size for key in ("vectors", "main_scores", "grades", "profile_order")):
        raise ValueError("cached family candidate dimensions mismatch")
    if sorted(record["profile_order"]) != list(range(size)):
        raise ValueError("cached family profile order is not a permutation")
    contract = feature_schema(initial_schema())
    hashes = {vector["schema_hash"] for vector in record["vectors"]}
    if len(hashes) != 1 or any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in hashes):
        raise ValueError("inconsistent historical feature schema hashes")
    for index, vector in enumerate(record["vectors"]):
        if (tuple(vector["names"]) != contract.names or len(vector["values"]) != len(contract.names)
                or len(vector["available"]) != len(contract.names)
                or any(type(v) is not bool for v in vector["available"])
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in vector["values"])
                or vector["values"][contract.names.index("main_score")] != record["main_scores"][index]):
            raise ValueError("cached family feature names, values or main score mismatch")
    if [(a, b) for a, b, _ in record["pairs"]] != list(itertools.combinations(range(size), 2)):
        raise ValueError("cached family pair coverage mismatch")
    teacher = _CacheOnlyTeacher(directory / "provider-cache")
    target, preference = record["vacancy"], record["manifest"]["preference"]
    for a, b, label in record["pairs"]:
        if teacher.pair(target, record["traits"][a], record["traits"][b], preference) != label:
            raise ValueError("family label differs from cached teacher")
    for index, trait in enumerate(record["traits"]):
        if teacher.grade(target, trait, preference) != record["grades"][index]:
            raise ValueError("family grade differs from cached teacher")
    swaps = [teacher.pair(target, record["traits"][b], record["traits"][a], preference) ==
             {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label]
             for a, b, label in record["pairs"][:3]]
    if swaps != record["swap_audits"] or sum(swaps) < 2:
        raise ValueError("family swap receipts differ or fail quality guard")
    for batch in record.get("generation_batches", []):
        if batch.get("receipt_hash") != digest({k: v for k, v in batch.items() if k != "receipt_hash"}):
            raise ValueError("cached family generation receipt mismatch")
    return _generation_sources(directory, record)


def _candidate_profiles(record: dict, batches: list[dict], seed: dict) -> list[CandidateProfile]:
    sources = [trait for batch in batches for trait in batch["traits"]]
    if len(sources) != len(record["traits"]):
        raise ValueError("profile source count mismatch")
    profiles = []
    for trait, source in zip(record["traits"], sources, strict=True):
        raw = deepcopy(seed)
        raw.update(primary_direction=record["manifest"]["direction"], work_types=["remote"], city="Москва",
                   salary={"from_rub": trait["salary_min"], "to_rub": min(200000, trait["salary_min"] + 20000)},
                   description=trait["description"], experience=[], achievements=[], preferences=[], metadata={})
        raw["grade_result"]["grade"] = trait["grade"]
        for group, keys in (("domains", ("sql", "rest_api")), ("stacks", ("python", "react")),
                            ("soft_skills", ("teamwork",))):
            raw[group] = [{"key": key, "level": trait["levels"][key], "status": trait["status"]} for key in keys]
        project = {"title": "Синтетический проект", "description": trait["description"], "status": trait["status"]}
        if trait["structured_facts_available"]:
            # The generation recipe overwrote only the parent status for the final
            # rejected profile; nested facts kept their original generator status.
            project["facts"] = {"domains": [{"key": "sql", "status": source["status"]}],
                                "stacks": [{"key": "python", "status": source["status"]}]}
            for key in ("duration_months", "production_support"):
                if trait[key] is not None:
                    project["facts"][key] = {"value": trait[key], "status": source["status"]}
        raw["projects"] = [project]
        profiles.append(CandidateProfile.model_validate(raw))
    return profiles


def rebuild_family_features(directory: Path, record: dict) -> tuple[list[FeatureVector], list[float], dict]:
    """Recompute current features/main scores from sealed sources without any writes or inference.

    Returns new feature vectors, current main scores and deterministic provenance.
    All 640-dimensional Harrier vectors must already exist in semantic-cache.json
    under the pinned role-specific model/text digest. A cache miss fails explicitly;
    unknown duration/production remains absent, never substituted with zero/false.
    """
    batches = verify_cached_sources(directory, record)
    seed_bytes = SEED_PATH.read_bytes()
    seed = json.loads(seed_bytes)[0]["profile"]
    profiles = _candidate_profiles(record, batches, seed)
    item = record["manifest"]
    expected_vacancy = _source_vacancy(record)
    targets = {key: expected_vacancy[key] for key in ("domens", "stacks", "soft_skills")}
    semantic_path = directory / "semantic-cache.json"
    if not semantic_path.is_file():
        raise ValueError("missing semantic source cache")
    semantic_bytes = semantic_path.read_bytes()
    semantics = json.loads(semantic_bytes)
    semantic_keys = []

    def cached(encoder: HarrierEncoder, text: str) -> list[float]:
        key = digest({"model": encoder.model_version, "text": text})
        if key not in semantics:
            raise ValueError(f"missing pinned semantic cache entry: {key}")
        vector = semantics[key]
        if (not isinstance(vector, list) or len(vector) != 640
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                or not .99 <= math.sqrt(sum(v * v for v in vector)) <= 1.01):
            raise ValueError("invalid pinned semantic cache vector")
        semantic_keys.append(key)
        return list(vector)

    schema = initial_schema()
    document, query = HarrierEncoder(), HarrierEncoder("vacancy_query")
    identifier = uuid5(NAMESPACE_URL, item["family_id"])
    source = VacancyInput.model_validate({"owner_id": str(identifier), "source_revision": 1, "status": "open",
        "zp_min": 50000, "zp_max": 200000, "daily": "full_time", "type": "remote", "city": "Москва",
        "class": expected_vacancy["class"], "bio": expected_vacancy["bio"], **targets})
    vector, mask = build_strict_features(source, schema)
    semantic = cached(query, source.bio) if source.bio else None
    vacancy = VacancyReady(**source.model_dump(by_alias=True), vacancy_id=identifier, revision=1,
        build_mode="class_only" if item["class_only"] else "mixed", strict_vector=vector, requirement_mask=mask,
        strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash, semantic_vector=semantic,
        semantic_model_version=query.model_version if semantic else None,
        semantic_space_id=EMBEDDING_SPACE_ID if semantic else None, semantic_available=semantic is not None,
        published_at=FIXED_PUBLISHED_AT)
    strengths = [confirmed_strength(profile) for profile in profiles]
    ordering = sorted(range(len(profiles)), key=lambda i: (-GRADE_RANK[profiles[i].grade_result.grade], -strengths[i], i))
    if ordering != record["profile_order"]:
        raise ValueError("source profile ranking differs from generation recipe")
    candidates = []
    for index, profile in enumerate(profiles):
        rank = 1 + sum((GRADE_RANK[other.grade_result.grade], strengths[i]) >
                       (GRADE_RANK[profile.grade_result.grade], strengths[index]) for i, other in enumerate(profiles))
        candidates.append(MatchCandidate(candidate_id=uuid5(NAMESPACE_URL, f"{item['family_id']}/{index}"),
            profile_revision=1, display_name=f"Synthetic {index}", city=profile.city, salary=profile.salary,
            category=category_for(profile), rank=rank, class_size=len(profiles),
            rating_score=1 - (rank - 1) / max(len(profiles) - 1, 1), confirmed_strength=strengths[index],
            strict_vector=build_strict_vector(profile, schema), strict_evidence=build_strict_evidence(profile, schema),
            strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash,
            semantic_vector=cached(document, build_semantic_text(profile)), semantic_model_version=document.model_version,
            semantic_space_id=EMBEDDING_SPACE_ID, published_at=FIXED_PUBLISHED_AT, resume_url="/synthetic-public.pdf",
            work_types=["remote"], narrative_facts=project_narrative_facts({"projects": profile.projects,
                "experience": profile.experience, "achievements": profile.achievements})))
    policy = ScoringPolicy()
    selected = select_candidates(vacancy, candidates, policy, len(candidates))
    cards = {card.candidate_id: card for card in selected.items}
    if set(cards) != {candidate.candidate_id for candidate in candidates}:
        raise ValueError("source family contains candidates excluded by current strict filter")
    rebuilt = [build_features(vacancy, candidate, cards[candidate.candidate_id], schema) for candidate in candidates]
    scores = [cards[candidate.candidate_id].explanation.score for candidate in candidates]
    provenance = {"rebuild_version": REBUILD_VERSION, "source_record_hash": record["record_hash"],
        "source_feature_schema_hash": record["vectors"][0]["schema_hash"],
        "current_feature_schema_hash": feature_schema(schema).schema_hash, "current_policy_hash": policy_hash(policy),
        "strict_schema_hash": schema.schema_hash, "strict_dimensions": len(schema.coordinates),
        "seed_profile_hash": digest(seed), "semantic_cache_hash": digest(semantics), "semantic_keys": semantic_keys,
        "generation_receipt_hashes": [batch["receipt_hash"] for batch in batches],
        "generation_source_identities": [{"batch": batch["batch"], "source_cache_key": batch["source_cache_key"],
            "approved_source_identity": _approved_generation_source_identity(
                _read(directory / "provider-cache" / f"{batch['source_cache_key']}.json")["request"])} for batch in batches],
        "profile_snapshot_hashes": [digest(profile.model_dump(mode="json")) for profile in profiles],
        "candidate_snapshot_hashes": [digest(candidate.model_dump(mode="json")) for candidate in candidates],
        "vacancy_snapshot_hash": digest(vacancy.model_dump(mode="json")), "published_at": FIXED_PUBLISHED_AT.isoformat(),
        "source_profiles": len(profiles), "source_raw_profiles": sum(batch["raw_count"] for batch in batches),
        "source_dropped_profiles": sum(len(batch["dropped"]) for batch in batches),
        "changed_feature_values": sum(old != new for source_vector, current in zip(record["vectors"], rebuilt, strict=True)
                                      for old, new in zip(source_vector["values"], current.values, strict=True)),
        "changed_availability_values": sum(old != new for source_vector, current in zip(record["vectors"], rebuilt, strict=True)
                                           for old, new in zip(source_vector["available"], current.available, strict=True)),
        "changed_main_scores": sum(old != new for old, new in zip(record["main_scores"], scores, strict=True)),
        "mode": "verified_cache_only_no_inference"}
    return rebuilt, scores, provenance
