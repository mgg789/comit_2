"""Operator-only bounded experiment commands; runtime never imports the learner."""

import argparse
import fcntl
import itertools
import json
import os
import random
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from matching_engine.combine_models import (
    MatchCandidate,
    ScoringPolicy,
    project_narrative_facts,
)
from matching_engine.combine_scoring import select_candidates
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
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import validate_artifact
from matching_engine.rerank_protocol import DIRECTIONS, expected_families
from matching_engine.rerank_providers import (
    GEMMA_GENERATION_MODEL,
    GENERATION_MODELS,
    QWEN,
    SystemOneProviders,
    generation_identity,
    teacher_identity,
    validate_traits,
)
from matching_engine.strict import build_strict_vector, initial_schema
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import build_strict_features
from matching_engine.vacancy_models import VacancyInput, VacancyReady

PREFERENCE_SCENARIOS = (
    "Prefer precise technical requirement coverage over a higher grade alone.",
    "Prefer reliable evidence over stronger unverified declarations when otherwise comparable.",
    "Prefer relevant domain experience, accepting a somewhat weaker matching stack.",
    "Prefer depth on key requirements over breadth on unrelated skills.",
    "Prefer relevant projects and production support over duration alone.",
    "Prefer teamwork and economical salary expectations within an acceptable technical fit.",
    "Prefer balanced evidence, transferable skills and relevant interests; do not punish missing facts.",
    "Prefer strong fit and sustainable salary expectations, considering uncertainty and grade together.",
)
def _parallel[Input, Output](function: Callable[[Input], Output], items: list[Input], workers: int = 5) -> list[Output]:
    """Keep at most five submitted remote jobs; preserve input order and stop on failure."""
    if not 1 <= workers <= 5:
        raise ValueError("remote concurrency must be between one and five")
    iterator = iter(items)
    executor = ThreadPoolExecutor(max_workers=workers)
    pending = deque(executor.submit(function, item) for item in itertools.islice(iterator, workers))
    output = []
    sentinel = object()
    try:
        while pending:
            output.append(pending.popleft().result())
            item = next(iterator, sentinel)
            if item is not sentinel:
                pending.append(executor.submit(function, item))
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return output


class _CacheOnlyTeacher(SystemOneProviders):
    """Verify existing labels against their original cache without key reads or HTTP."""

    def request(self, path: str, payload: dict, kind: str) -> dict:
        key = digest(self._identity(path, payload, kind))
        target = self.directory / f"{key}.json"
        if not target.exists():
            raise ValueError("family label has no compatible cached teacher receipt")
        cached = _read(target)
        if (cached["request_hash"] != key or digest(cached["request"]) != key
                or digest(cached["response"]) != cached["response_hash"]):
            raise ValueError("cached teacher receipt integrity failed")
        return cached["response"]


def _bind_generation_sources(directory: Path, record: dict) -> list[dict]:
    """Resolve raw generation receipts and verify the documented controlled variants."""
    item = record["manifest"]
    existing = {b["batch"]: b for b in record.get("generation_batches", [])}
    candidates = []
    for path in (directory / "provider-cache").glob("*.json"):
        if path.name == "usage.json":
            continue
        cached = _read(path)
        if cached["request"].get("model") not in (QWEN, *GENERATION_MODELS) or "response" not in cached:
            continue
        if (cached["request_hash"] != digest(cached["request"])
                or cached["response_hash"] != digest(cached["response"])):
            raise ValueError("generation source cache integrity failed")
        content = cached["response"]["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        raw = json.loads(content)
        raw = raw["profiles"] if isinstance(raw, dict) and set(raw) == {"profiles"} else raw
        candidates.append((cached, raw))
    batches = []
    for batch in (1, 2):
        matches = []
        for cached, raw in candidates:
            instruction = cached["request"]["payload"]["messages"][0]["content"]
            correct_source = (f"for {item['direction']}." in instruction and
                              f"Scenario: {item['preference']} Batch {batch}." in instruction)
            if item["family_id"] == "backend-01" and batch == 1:
                correct_source = raw == _read(directory / "pilot-profiles.json")
            if not correct_source or not isinstance(raw, list) or len(raw) != 5:
                continue
            accepted, dropped = [], []
            for index, trait in enumerate(raw):
                try:
                    validate_traits(trait)
                except (ValueError, TypeError, KeyError) as exc:
                    dropped.append({"source_index": index, "raw_hash": digest(trait),
                                    "error_type": type(exc).__name__, "reason": str(exc)
                                    if isinstance(exc, ValueError) else "invalid trait structure"})
                else:
                    accepted.append(trait)
            old = existing.get(batch)
            if old and (old["traits"] != accepted or old["dropped"] != dropped
                        or old["generator"]["model"] != cached["request"]["model"]):
                continue
            receipt = {"family_id": item["family_id"], "batch": batch,
                "generator": {key: cached["request"][key] for key in ("model", "prompt_version")},
                "source_cache_key": cached["request_hash"], "source_response_hash": cached["response_hash"],
                "raw_count": len(raw), "traits": accepted, "dropped": dropped,
                "reuse_policy": "verified_cache_only"}
            receipt["receipt_hash"] = digest(receipt)
            matches.append(receipt)
        if len(matches) != 1:
            raise ValueError("family has missing or ambiguous generation provenance")
        batches.append(matches[0])
    expected = json.loads(json.dumps([trait for batch in batches for trait in batch["traits"]]))
    for index, trait in enumerate(expected):
        trait["grade"] = ("intern", "junior", "middle", "senior")[(index + int(item["family_id"][-2:])) % 4]
        if index == len(expected) - 1:
            trait["status"] = "rejected"
        trait["structured_facts_available"] = index % 4 != 0
        if not trait["structured_facts_available"]:
            trait["duration_months"] = None
            trait["production_support"] = None
    if expected != record["traits"]:
        raise ValueError("family facts differ from cached sources and controlled variants")
    return batches


def _validate_family_receipts(directory: Path, record: dict) -> None:
    teacher = _CacheOnlyTeacher(directory / "provider-cache")
    traits, target = record["traits"], record["vacancy"]
    size = len(traits)
    if not 2 <= size <= 10 or record["teacher"] != teacher_identity():
        raise ValueError("cached family dimensions or teacher mismatch")
    if any(len(record[key]) != size for key in ("vectors", "main_scores", "grades", "profile_order")):
        raise ValueError("cached family candidate dimensions mismatch")
    expected = list(itertools.combinations(range(size), 2))
    if [(a, b) for a, b, _ in record["pairs"]] != expected:
        raise ValueError("cached family pair coverage mismatch")
    preference = record["manifest"]["preference"]
    for a, b, label in record["pairs"]:
        if teacher.pair(target, traits[a], traits[b], preference) != label:
            raise ValueError("family label differs from cached teacher")
    for index, trait in enumerate(traits):
        if teacher.grade(target, trait, preference) != record["grades"][index]:
            raise ValueError("family grade differs from cached teacher")
    audits = [teacher.pair(target, traits[b], traits[a], preference) ==
              {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label]
              for a, b, label in record["pairs"][:3]]
    if audits != record["swap_audits"] or sum(audits) < 2:
        raise ValueError("family swap receipts differ or fail quality guard")
    schema = feature_schema(initial_schema())
    for index, vector in enumerate(record["vectors"]):
        if (vector["schema_hash"] != schema.schema_hash or tuple(vector["names"]) != schema.names
                or len(vector["values"]) != len(schema.names)
                or len(vector["available"]) != len(schema.names)
                or any(type(v) is not bool for v in vector["available"])
                or vector["values"][schema.names.index("main_score")] != record["main_scores"][index]):
            raise ValueError("cached family feature schema or main score mismatch")
    for batch in record.get("generation_batches", []):
        if (batch["receipt_hash"] != digest({k: v for k, v in batch.items() if k != "receipt_hash"})
                or batch["family_id"] != record["manifest"]["family_id"]
                or batch["generator"]["model"] not in (QWEN, *GENERATION_MODELS)):
            raise ValueError("cached family generation receipt mismatch")
    if _bind_generation_sources(directory, record) != record.get("generation_batches"):
        raise ValueError("family generation provenance differs from cached sources")


def seal_cached_family(directory: Path, family_id: str) -> dict:
    """Explicitly seal historical mixed-generator families after checking cached labels.

    Original bytes are retained in history. This migration never regenerates
    profiles, labels, embeddings or scores, and never resets the attempt ledger.
    """
    target = directory / "families" / f"{family_id}.json"
    original_bytes = target.read_bytes()
    record = _read(target)
    if record["manifest"] not in family_manifest() or record["manifest"]["family_id"] != family_id:
        raise ValueError("unknown cached family source")
    if "record_hash" in record:
        if record["record_hash"] != digest({k: v for k, v in record.items() if k != "record_hash"}):
            raise ValueError("family record integrity failed")
        _validate_family_receipts(directory, record)
        return record
    original = digest(record)
    record["generation_batches"] = _bind_generation_sources(directory, record)
    _validate_family_receipts(directory, record)
    history = directory / "family-history" / f"{family_id}-{original}.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    if history.exists() and history.read_bytes() != original_bytes:
        raise ValueError("family history already contains different original bytes")
    if not history.exists():
        temporary = history.with_suffix(".tmp")
        temporary.write_bytes(original_bytes)
        temporary.replace(history)
    record["historical_seal"] = {"source_record_hash": original, "policy": "verified_cache_only"}
    record["record_hash"] = digest(record)
    _write(target, record)
    return record


def reuse_cached_batch(directory: Path, family_id: str, batch: int, cache_key: str) -> dict:
    """Preserve valid raw traits from an explicitly selected immutable cache, never repair facts.

    This operation makes no provider calls. Drops retain source position and reason;
    the original response remains intact and counted in the cumulative budget.
    """
    if family_id not in {row["family_id"] for row in family_manifest()} or batch not in (1, 2):
        raise ValueError("unknown family or batch")
    if len(cache_key) != 64 or any(c not in "0123456789abcdef" for c in cache_key):
        raise ValueError("invalid cache key")
    cached = _read(directory / "provider-cache" / f"{cache_key}.json")
    if (cached["request_hash"] != cache_key or digest(cached["request"]) != cache_key
            or digest(cached["response"]) != cached["response_hash"]):
        raise ValueError("cached generation integrity failed")
    request = cached["request"]
    if request["path"] not in ("/api/v1/chat/completions", "/v1/chat/completions"):
        raise ValueError("source is not a generation receipt")
    family = next(row for row in family_manifest() if row["family_id"] == family_id)
    instruction = request["payload"]["messages"][0]["content"]
    if (f"for {family['direction']}." not in instruction
            or f"Scenario: {family['preference']} Batch {batch}." not in instruction):
        raise ValueError("cached generation belongs to another source family")
    content = cached["response"]["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    values = json.loads(content)
    values = values["profiles"] if isinstance(values, dict) and set(values) == {"profiles"} else values
    if not isinstance(values, list) or len(values) != 5:
        raise ValueError("cached batch must contain five raw profiles")
    accepted, dropped = [], []
    for index, value in enumerate(values):
        try:
            validate_traits(value)
        except (ValueError, TypeError, KeyError) as exc:
            dropped.append({"source_index": index, "raw_hash": digest(value), "error_type": type(exc).__name__,
                            "reason": str(exc) if isinstance(exc, ValueError) else "invalid trait structure"})
        else:
            accepted.append(value)
    receipt = {"family_id": family_id, "batch": batch, "generator": {
        key: request[key] for key in ("model", "prompt_version")}, "source_cache_key": cache_key,
        "source_response_hash": cached["response_hash"], "raw_count": 5, "traits": accepted,
        "dropped": dropped, "reuse_policy": "explicit_cache_only"}
    receipt["receipt_hash"] = digest(receipt)
    target = directory / "generation-batches" / f"{family_id}-{batch}.json"
    if target.exists() and _read(target) != receipt:
        raise ValueError("generation batch is already sealed")
    _write(target, receipt)
    return receipt


def _generation_batch(directory: Path, providers: SystemOneProviders, item: dict, batch: int) -> dict:
    target = directory / "generation-batches" / f"{item['family_id']}-{batch}.json"
    if target.exists():
        receipt = _read(target)
        if (receipt["receipt_hash"] != digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
                or receipt["family_id"] != item["family_id"] or receipt["batch"] != batch):
            raise ValueError("generation batch integrity failed")
        if receipt.get("reuse_policy") != "explicit_cache_only":
            model = receipt["generator"]["model"]
            if model not in GENERATION_MODELS or receipt["generator"] != generation_identity(model):
                raise ValueError("generation batch belongs to another generator")
        for trait in receipt["traits"]:
            validate_traits(trait)
        return receipt
    traits = providers.generate(item["direction"], item["preference"] + f" Batch {batch}")
    receipt = {"family_id": item["family_id"], "batch": batch, "generator": generation_identity(providers.generator_model),
               "raw_count": len(traits), "traits": traits, "dropped": []}
    receipt["receipt_hash"] = digest(receipt)
    _write(target, receipt)
    return receipt


def family_manifest() -> list[dict]:
    """Freeze all eight directions and source-family splits before any model is fitted."""
    output = []
    for row in expected_families():
        index = int(row["family_id"][-2:]) - 1 + DIRECTIONS.index(row["direction"])
        output.append({**row, "preference": PREFERENCE_SCENARIOS[index % 8],
                       "class_only": index % 5 == 0, "bio_absent": index % 4 == 0})
    return output


def label_manifest(directory: Path) -> dict:
    """Seal complete request/response/failure provenance and actual usage, without estimates."""
    records = []
    for path in sorted((directory / "provider-cache").glob("*.json")):
        if path.name == "usage.json":
            continue
        record = _read(path)
        if record["request_hash"] != digest(record["request"]):
            raise ValueError("provider request integrity failed")
        if "response" in record and record["response_hash"] != digest(record["response"]):
            raise ValueError("provider response integrity failed")
        records.append({"cache_file": path.name, "record_hash": digest(record), **record})
    return {"records": records, "usage": _read(directory / "provider-cache/usage.json")}


def label_validation(families: list[dict], pilot: dict) -> dict:
    """Derive label validity from auditable swap outcomes, never a manually assigned flag."""
    audits = [value for family in families for value in family["swap_audits"]]
    consistency = sum(audits) / max(1, len(audits))
    report = {"pilot_hash": pilot["report_hash"], "teacher": teacher_identity(), "families": len(families),
              "swap_correct": sum(audits), "swap_total": len(audits), "swap_consistency": consistency,
              "minimum_per_family": min((sum(f["swap_audits"]) for f in families), default=0),
              "insufficient_pairs": sum(p[2] == "insufficient" for f in families for p in f["pairs"]),
              "tie_pairs": sum(p[2] == "tie" for f in families for p in f["pairs"]),
              "unknown_grades": sum(g is None for f in families for g in f["grades"]),
              "threshold": .8}
    report["passed"] = (pilot["state"] == "passed" and pilot.get("swap_consistency", 0) >= .8
                        and pilot.get("teacher") == teacher_identity()
                        and all(f.get("teacher") == teacher_identity() for f in families)
                        and pilot.get("decisive_pairs", 0) >= 6 and len(families) == 60
                        and len(audits) == 180 and consistency >= .8
                        and report["minimum_per_family"] >= 2)
    return report


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def generate(directory: Path) -> None:
    """Expand a sealed pilot; persist a stopped receipt on validation/provider failure."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".generation-run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _generate(directory)
        except Exception as exc:
            _write(directory / "generation-report.json", {"state": "stopped", "teacher": teacher_identity(),
                "generator": generation_identity(), "generators": [generation_identity(m) for m in GENERATION_MODELS],
                "error_type": type(exc).__name__,
                "completed_families": len(list((directory / "families").glob("*.json")))})
            raise
        _write(directory / "generation-report.json", {"state": "passed", "teacher": teacher_identity(),
            "generator": generation_identity(), "generators": [generation_identity(m) for m in GENERATION_MODELS],
            "completed_families": 60})


def _generate(directory: Path) -> None:
    """Cache every family under the frozen split; never overwrite another teacher's data."""
    pilot = _read(directory / "pilot-report.json")
    if pilot["state"] != "passed":
        raise ValueError("pilot stopped; generation and training remain disabled")
    if pilot["report_hash"] != digest({k: v for k, v in pilot.items() if k != "report_hash"}):
        raise ValueError("pilot integrity failed")
    if pilot.get("teacher") != teacher_identity():
        raise ValueError("pilot teacher differs from the active provider")
    manifest = family_manifest()
    manifest_path = directory / "split-manifest.json"
    if manifest_path.exists() and _read(manifest_path) != manifest:
        raise ValueError("sealed split manifest differs; do not overwrite held-out data")
    _write(manifest_path, manifest)
    providers = SystemOneProviders(directory / "provider-cache")
    gemma = SystemOneProviders(directory / "provider-cache", generator_model=GEMMA_GENERATION_MODEL)
    schema = initial_schema()
    document, query = HarrierEncoder(), HarrierEncoder("vacancy_query")
    semantic_path = directory / "semantic-cache.json"
    semantics = _read(semantic_path) if semantic_path.exists() else {}

    def encode(encoder: HarrierEncoder, text: str) -> list[float]:
        key = digest({"model": encoder.model_version, "text": text})
        if key not in semantics:
            semantics[key] = encoder.encode(text)
            _write(semantic_path, semantics)
        return semantics[key]

    families = []
    seed_path = Path(__file__).resolve().parents[4] / "data/seed/candidate-pipeline.json"
    seed = _read(seed_path)[0]["profile"]
    for item in manifest:
        family_path = directory / "families" / f"{item['family_id']}.json"
        if family_path.exists():
            existing = _read(family_path)
            if existing["manifest"] != item:
                raise ValueError("cached family manifest changed")
            if existing.get("teacher") != teacher_identity():
                raise ValueError("cached family belongs to another teacher")
            if ("record_hash" not in existing or existing["record_hash"] !=
                    digest({k: v for k, v in existing.items() if k != "record_hash"})):
                raise ValueError("family record is unsealed or modified; explicit seal-family is required")
            _validate_family_receipts(directory, existing)
            families.append(existing)
            continue
        batches = _parallel(lambda batch, current=item: _generation_batch(
            directory, providers if batch == 1 else gemma, current, batch),
                            [2] if item["family_id"] == "backend-01" else [1, 2])
        raw_traits = ([*_read(directory / "pilot-profiles.json")] if item["family_id"] == "backend-01" else [])
        raw_traits.extend(trait for batch in batches for trait in batch["traits"])
        traits = json.loads(json.dumps(raw_traits))
        size = len(traits)
        if not 2 <= size <= 10:
            raise ValueError("family must contain two to ten validated profiles")
        targets = {"domens": {}, "stacks": {}, "soft_skills": {}} if item["class_only"] else {
            "domens": {"sql": 4}, "stacks": {"python": 5, "react": 3}, "soft_skills": {"teamwork": 3}}
        vacancy_source = VacancyInput.model_validate({"owner_id": str(uuid5(NAMESPACE_URL, item["family_id"])),
            "source_revision": 1, "status": "open", "zp_min": 50000, "zp_max": 200000,
            "daily": "full_time", "type": "remote", "city": "Москва", "class": item["direction"],
            "bio": None if item["bio_absent"] else "Разработка API и интерфейсов, SQL, взаимодействие в команде",
            **targets})
        vector, mask = build_strict_features(vacancy_source, schema)
        semantic = encode(query, vacancy_source.bio) if vacancy_source.bio else None
        target = VacancyReady(**vacancy_source.model_dump(by_alias=True), vacancy_id=uuid5(NAMESPACE_URL, item["family_id"]),
            revision=1, build_mode="class_only" if item["class_only"] else "mixed", strict_vector=vector, requirement_mask=mask,
            strict_schema_version=1, strict_schema_hash=schema.schema_hash, semantic_vector=semantic,
            semantic_model_version=query.model_version if semantic else None, semantic_space_id=EMBEDDING_SPACE_ID if semantic else None,
            semantic_available=semantic is not None, published_at=datetime.now(UTC))
        profiles = []
        for index, trait in enumerate(traits):
            # Controlled synthetic variants are explicit source facts, never inferred labels.
            status = "rejected" if index == size - 1 else trait["status"]
            trait["grade"] = ("intern", "junior", "middle", "senior")[(index + int(item["family_id"][-2:])) % 4]
            raw = json.loads(json.dumps(seed))
            raw.update(primary_direction=item["direction"], work_types=["remote"], city="Москва",
                       salary={"from_rub": trait["salary_min"], "to_rub": min(200000, trait["salary_min"] + 20000)},
                       description=trait["description"], experience=[], achievements=[], preferences=[], metadata={})
            raw["grade_result"]["grade"] = trait["grade"]
            for group, keys in (("domains", ("sql", "rest_api")), ("stacks", ("python", "react")), ("soft_skills", ("teamwork",))):
                raw[group] = [{"key": key, "level": trait["levels"][key], "status": status} for key in keys]
            raw["projects"] = [{"title": "Синтетический проект", "description": trait["description"],
                "status": trait["status"], "facts": {"domains": [{"key": "sql", "status": trait["status"]}],
                    "stacks": [{"key": "python", "status": trait["status"]}],
                    "duration_months": {"value": trait["duration_months"], "status": trait["status"]},
                    "production_support": {"value": trait["production_support"], "status": trait["status"]}}}]
            raw["projects"][0]["status"] = status
            if index % 4 == 0:
                raw["projects"][0].pop("facts")
            # The teacher must see the same controlled absence/status as feature inference.
            trait["status"] = status
            trait["structured_facts_available"] = index % 4 != 0
            if not trait["structured_facts_available"]:
                trait["duration_months"] = None
                trait["production_support"] = None
            profiles.append(CandidateProfile.model_validate(raw))
        ranking = sorted(range(size), key=lambda i: (-GRADE_RANK[profiles[i].grade_result.grade], -confirmed_strength(profiles[i]), i))
        candidates = []
        for index, profile in enumerate(profiles):
            rank = 1 + sum((GRADE_RANK[p.grade_result.grade], confirmed_strength(p)) >
                           (GRADE_RANK[profile.grade_result.grade], confirmed_strength(profile)) for p in profiles)
            candidates.append(MatchCandidate(candidate_id=uuid5(NAMESPACE_URL, f"{item['family_id']}/{index}"),
                profile_revision=1, display_name=f"Synthetic {index}", city=profile.city, salary=profile.salary,
                category=category_for(profile), rank=rank, class_size=size, rating_score=1-(rank-1)/max(size-1, 1),
                confirmed_strength=confirmed_strength(profile), strict_vector=build_strict_vector(profile, schema),
                strict_evidence=build_strict_evidence(profile, schema), strict_schema_version=1,
                strict_schema_hash=schema.schema_hash, semantic_vector=encode(document, build_semantic_text(profile)),
                semantic_model_version=document.model_version, semantic_space_id=EMBEDDING_SPACE_ID,
                published_at=datetime.now(UTC), resume_url="/synthetic-public.pdf", work_types=["remote"],
                narrative_facts=project_narrative_facts({"projects": profile.projects, "experience": profile.experience,
                                                       "achievements": profile.achievements})))
        selected = select_candidates(target, candidates, ScoringPolicy(), size)
        cards = {card.candidate_id: card for card in selected.items}
        features = [asdict(build_features(target, candidate, cards[candidate.candidate_id], schema)) for candidate in candidates]
        # Teacher receives raw declared traits and preferences, never main/model scores or feature vectors.
        teacher_target = {"class": item["direction"], **targets, "bio": vacancy_source.bio,
                          "salary": {"min": 50000, "max": 200000}}
        indices = list(itertools.combinations(range(size), 2))
        labels = _parallel(lambda pair, target=teacher_target, values=traits, preference=item["preference"]:
            providers.pair(target, values[pair[0]], values[pair[1]], preference), indices)
        pairs = [(a, b, label) for (a, b), label in zip(indices, labels, strict=True)]
        audits = []
        for a, b, label in pairs[:3]:
            swapped = providers.pair(teacher_target, traits[b], traits[a], item["preference"])
            audits.append(swapped == {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label])
        if sum(audits) < 2:
            raise ValueError(f"teacher consistency failed for {item['family_id']}; stop expansion")
        grades = _parallel(lambda trait, target=teacher_target, preference=item["preference"]:
            providers.grade(target, trait, preference), traits)
        record = {"manifest": item, "teacher": teacher_identity(), "traits": traits, "vacancy": teacher_target, "vectors": features,
                  "main_scores": [cards[c.candidate_id].explanation.score for c in candidates],
                  "grades": grades, "pairs": pairs, "swap_audits": audits,
                  "generation_batches": batches, "profile_order": ranking, "slice_tags": ["class_only" if item["class_only"] else "mixed",
                      "bio_absent" if item["bio_absent"] else "semantic_available", "mixed_evidence", "partial_facts"]}
        record["generation_batches"] = _bind_generation_sources(directory, record)
        record["record_hash"] = digest(record)
        _write(family_path, record)
        families.append(record)
        print(json.dumps({"completed_families": len(families), "family_id": item["family_id"],
                          "profiles": size, "swap_correct": sum(audits)}), flush=True)
    provenance = label_manifest(directory)
    _write(directory / "label-manifest.json", provenance)
    validation = label_validation(families, pilot)
    _write(directory / "label-validation.json", validation)
    dataset = {"families": families, "teacher": teacher_identity(), "split_hash": digest(manifest), "label_cache_hash": digest(provenance),
               "pilot_hash": pilot["report_hash"], "label_validation": validation,
               "label_validation_hash": digest(validation)}
    dataset["dataset_hash"] = digest(dataset)
    _write(directory / "dataset.json", dataset)


def train_dataset(directory: Path) -> dict:
    """Train from immutable cached data only; never calls a generator, teacher or Harrier."""
    from matching_engine.rerank_training import TrainingFamily, train
    dataset = _read(directory / "dataset.json")
    original_hash = dataset.pop("dataset_hash")
    if digest(dataset) != original_hash or dataset["split_hash"] != digest(family_manifest()):
        raise ValueError("dataset or split integrity failed")
    if [f["manifest"] for f in dataset["families"]] != family_manifest():
        raise ValueError("actual family assignments differ from the sealed manifest")
    if dataset.get("teacher") != teacher_identity() or any(
            f.get("teacher") != teacher_identity() for f in dataset["families"]):
        raise ValueError("dataset contains incompatible teacher labels")
    for family in dataset["families"]:
        if (family.get("record_hash") != digest({k: v for k, v in family.items() if k != "record_hash"})):
            raise ValueError("training family integrity failed")
        _validate_family_receipts(directory, family)
    if digest(dataset["label_validation"]) != dataset["label_validation_hash"]:
        raise ValueError("label validation receipt integrity failed")
    if digest(_read(directory / "label-manifest.json")) != dataset["label_cache_hash"]:
        raise ValueError("label provenance receipt integrity failed")
    code_hash = digest({name: (Path(__file__).parent / name).read_text(encoding="utf-8")
                        for name in ("rerank_training.py", "rerank_features.py", "rerank_model.py")})
    state_path = directory / "training-state.json"
    state = _read(state_path) if state_path.exists() else {"attempts": 0, "dataset_hash": original_hash, "code_hash": code_hash}
    if state["dataset_hash"] != original_hash or state["code_hash"] != code_hash:
        raise ValueError("sealed training context changed; previously exposed golden cannot be retuned")
    if (directory / "artifact.json").exists():
        return validate_artifact(_read(directory / "artifact.json"))
    if state["attempts"] >= 2:
        raise ValueError("training attempt budget exhausted")
    state["attempts"] += 1
    _write(state_path, state)
    families = [TrainingFamily(family_id=item["manifest"]["family_id"], direction=item["manifest"]["direction"],
        split=item["manifest"]["split"], source_id=item["manifest"]["source_id"],
        vectors=[FeatureVector(tuple(v["names"]), tuple(v["values"]), tuple(v["available"]), v["schema_hash"]) for v in item["vectors"]],
        main_scores=item["main_scores"], grades=item["grades"], pairs=[tuple(p) for p in item["pairs"]],
        slice_tags=tuple(item["slice_tags"])) for item in dataset["families"]]
    artifact = train(families, feature_schema(initial_schema()), {"dataset_hash": original_hash,
        "split_hash": dataset["split_hash"], "label_cache_hash": dataset["label_cache_hash"],
        "label_validation": dataset["label_validation"], "label_validation_hash": dataset["label_validation_hash"],
        "model_version": "r0-pairwise-v1"})
    _write(directory / "artifact.json", artifact)
    return artifact


def review_package(directory: Path) -> None:
    """Publish disjoint twenty-pair blind packets without labels or computed scores."""
    dataset = _read(directory / "dataset.json")
    items = []
    for family in dataset["families"]:
        if family["manifest"]["split"] == "golden":
            for a, b, _label in family["pairs"]:
                items.append({"pair_id": f"{family['manifest']['family_id']}/{a}/{b}",
                    "vacancy": family["vacancy"], "preferences": family["manifest"]["preference"],
                    "A": family["traits"][a], "B": family["traits"][b]})
    random.Random(42).shuffle(items)
    if len(items) < 40:
        raise ValueError("insufficient golden pairs for disjoint review")
    _write(directory / "review-user.json", items[:20])
    _write(directory / "review-assistant.json", items[20:40])


def seal_reviews(directory: Path, user_path: Path, assistant_path: Path) -> dict:
    """Attach actual operator-supplied review receipts; never synthesize human answers."""
    receipts = {}
    issues = []
    for person, path in (("user", user_path), ("assistant", assistant_path)):
        packet = _read(directory / f"review-{person}.json")
        expected = {pair["pair_id"] for pair in packet}
        answers = _read(path)
        if not isinstance(answers, list) or len(answers) != 20:
            raise ValueError("each review must contain exactly twenty real answers")
        if {answer.get("pair_id") for answer in answers} != expected or len(expected) != 20:
            raise ValueError("review answers do not match the sealed blind packet")
        for answer in answers:
            if set(answer) != {"pair_id", "choice", "factual_issue"} or answer["choice"] not in ("A", "B", "tie", "insufficient"):
                raise ValueError("invalid review receipt")
            if answer["factual_issue"] is not None and not isinstance(answer["factual_issue"], str):
                raise ValueError("factual issue must be a string or null")
            if answer["factual_issue"]:
                issues.append(f"{person}/{answer['pair_id']}: {answer['factual_issue']}")
        receipts[person] = {"hash": digest(answers), "packet_hash": digest(packet), "pair_ids": sorted(expected), "answers": answers}
    if set(receipts["user"]["pair_ids"]) & set(receipts["assistant"]["pair_ids"]):
        raise ValueError("human and assistant reviews overlap")
    artifact = _read(directory / "artifact.json")
    dataset = _read(directory / "dataset.json")
    if artifact["dataset_hash"] != dataset["dataset_hash"]:
        raise ValueError("review dataset does not belong to the trained artifact")
    artifact.pop("content_hash", None)
    artifact["reviews"] = {"user_pairs": 20, "assistant_pairs": 20, "disjoint_blind": True,
                           "factual_issues": issues, "receipts": receipts, "dataset_hash": artifact["dataset_hash"]}
    result = validate_artifact(artifact)
    _write(directory / "artifact-reviewed.json", result)
    return result


def main() -> None:
    """Run explicit offline stages or operator registration; training never activates a model."""
    parser = argparse.ArgumentParser(description="Bounded Comit R0 experiment")
    parser.add_argument("command", choices=("manifest", "reuse-batch", "seal-family", "generate", "train", "review-package", "seal-reviews", "register", "promote"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--hash")
    parser.add_argument("--user-review", type=Path)
    parser.add_argument("--assistant-review", type=Path)
    parser.add_argument("--family")
    parser.add_argument("--batch", type=int)
    parser.add_argument("--cache-key")
    args = parser.parse_args()
    if args.command == "manifest":
        _write(args.directory / "split-manifest.json", family_manifest())
    elif args.command == "reuse-batch":
        if args.family is None or args.batch is None or args.cache_key is None:
            parser.error("family, batch and cache-key are required")
        reuse_cached_batch(args.directory, args.family, args.batch, args.cache_key)
    elif args.command == "seal-family":
        if args.family is None:
            parser.error("family is required")
        seal_cached_family(args.directory, args.family)
    elif args.command == "generate":
        generate(args.directory)
    elif args.command == "train":
        train_dataset(args.directory)
    elif args.command == "review-package":
        review_package(args.directory)
    elif args.command == "seal-reviews":
        if args.user_review is None or args.assistant_review is None:
            parser.error("both review receipt paths are required")
        seal_reviews(args.directory, args.user_review, args.assistant_review)
    else:
        from matching_engine.rerank_store import RerankStore
        store = RerankStore(os.environ["DATABASE_URL"])
        if args.command == "register":
            if args.artifact is None:
                parser.error("--artifact required")
            print(store.register(validate_artifact(_read(args.artifact))))
        else:
            if not args.hash:
                parser.error("--hash required")
            store.promote(args.hash)


if __name__ == "__main__":
    main()
