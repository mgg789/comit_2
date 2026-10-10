"""Explicit train-only prose derivations and pre-label current-feature drift audits."""

import itertools
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import policy_hash
from matching_engine.embeddings import HarrierEncoder, build_semantic_text
from matching_engine.rerank_cohort_features import (
    build_family_features,
    candidate_profile,
)
from matching_engine.rerank_cohort_provider import validate_candidate
from matching_engine.rerank_features import FeatureVector, feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import expected_families
from matching_engine.strict import initial_schema

DESCRIPTION_REPAIRS = {
    "data-03": [
        {"index": 0,
         "old_text": "Разработка ETL-процессов и оптимизация SQL-запросов для аналитических отчетов.",
         "new_text": "Разрабатывал ETL-процессы для аналитических отчётов и оптимизировал соответствующие SQL-запросы.",
         "claim_set": ["ETL development", "SQL query optimization", "analytical reports"]},
        {"index": 2,
         "old_text": "Помощь в очистке данных и написании базовых скриптов на Python.",
         "new_text": "Помогал очищать данные и писать базовые Python-скрипты.",
         "claim_set": ["assisted data cleaning", "basic Python scripts"]},
    ],
    "fullstack-03": [
        {"index": 1,
         "old_text": "Поддержка клиентской части приложения на React и написание скриптов автоматизации на Python.",
         "new_text": "Поддерживал интерфейс приложения на React и писал Python-скрипты для автоматизации.",
         "claim_set": ["React client maintenance", "Python automation scripts"]},
    ],
}
ORIGINAL_COLLISIONS = (("data-03", 0, "data-07", 0), ("data-03", 2, "data-06", 2),
                       ("fullstack-03", 1, "fullstack-05", 1))
REPORT_VERSION = "train-description-derivation-drift-v1"
MAX_DOCUMENT_COSINE_DISTANCE = .20
MAX_SEMANTIC_COSINE_DELTA = .25


def apply_description_repairs(traits: list[dict], repairs: list[dict], split: str) -> list[dict]:
    """Deep-copy exact approved train paraphrases while preserving every typed fact.

    Only the three explicit old/new text pairs and reviewed claim sets are allowed.
    Replaying already-applied new text is idempotent. Holdouts, unsupported claims,
    new quantities, duplicate indices, and unexpected source text fail explicitly.
    """
    if repairs and split != "train":
        raise ValueError("description derivations are train-only")
    approved = [repair for entries in DESCRIPTION_REPAIRS.values() for repair in entries]
    result = deepcopy(traits)
    indices = set()
    for repair in repairs:
        if type(repair) is not dict or repair not in approved:
            raise ValueError("unapproved description claim or quantity")
        index = repair["index"]
        if type(index) is not int or not 0 <= index < len(result) or index in indices:
            raise ValueError("description repair index must be unique and in range")
        indices.add(index)
        if re.findall(r"\d+", repair["new_text"]) != re.findall(r"\d+", repair["old_text"]):
            raise ValueError("description repair introduces a quantity")
        if result[index]["description"] not in (repair["old_text"], repair["new_text"]):
            raise ValueError("description repair source text changed")
        result[index]["description"] = repair["new_text"]
        validate_candidate(result[index])
    if [_facts(trait) for trait in result] != [_facts(trait) for trait in traits]:
        raise ValueError("description repair changed typed facts")
    return result


def _facts(trait: dict) -> bytes:
    return json.dumps({key: value for key, value in trait.items() if key != "description"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _records(sources: list[dict] | dict[str, dict]) -> dict[str, dict]:
    rows = list(sources.values()) if isinstance(sources, dict) else sources
    expected = {row["family_id"]: row for row in expected_families()}
    records = {}
    if not isinstance(rows, list) or len(rows) != 60:
        raise ValueError("description audit requires all 60 source families")
    for record in rows:
        if record.get("record_hash") != digest({key: value for key, value in record.items() if key != "record_hash"}):
            raise ValueError("description source record hash mismatch")
        item = record["manifest"]
        family = item["family_id"]
        if family in records or family not in expected or any(
            item.get(key) != value for key, value in expected[family].items()
        ):
            raise ValueError("description source frozen allocation mismatch")
        if type(record.get("traits")) is not list or len(record["traits"]) != 7:
            raise ValueError("description sources must contain seven profiles per family")
        for trait in record["traits"]:
            validate_candidate(trait)
        records[family] = record
    if set(records) != set(expected) or isinstance(sources, dict) and set(sources) != set(records):
        raise ValueError("description source family IDs mismatch")
    return records


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _text_comparisons(records: dict[str, dict]) -> dict:
    profiles = [{"family_id": family, "index": index, "split": record["manifest"]["split"],
                 "text_hash": digest(trait["description"]), "text": trait["description"],
                 "tokens": set(re.findall(r"\w+", trait["description"].lower().replace("ё", "е")))}
                for family, record in sorted(records.items()) for index, trait in enumerate(record["traits"])]
    exact, near = [], []
    for a, b in itertools.combinations(profiles, 2):
        if a["split"] == b["split"]:
            continue
        union = a["tokens"] | b["tokens"]
        similarity = len(a["tokens"] & b["tokens"]) / max(1, len(union))
        pair = {"A": {key: a[key] for key in ("family_id", "index", "split", "text_hash", "text")},
                "B": {key: b[key] for key in ("family_id", "index", "split", "text_hash", "text")},
                "token_jaccard": similarity}
        if _normalized(a["text"]) == _normalized(b["text"]):
            exact.append(pair)
        if similarity >= .6:
            near.append(pair)
    near.sort(key=lambda pair: (-pair["token_jaccard"], pair["A"]["family_id"], pair["A"]["index"],
                                pair["B"]["family_id"], pair["B"]["index"]))
    return {"cross_split_exact_count": len(exact), "cross_split_exact_pairs": exact,
            "token_jaccard_threshold": .6, "cross_split_near_count": len(near), "top20": near[:20],
            "independence_established": False,
            "limitation": "Paraphrasing removes exact text identity only; related templates and cross-fold correlation remain."}


def _pool(records: dict[str, dict], direction: str) -> tuple[list, dict[str, str]]:
    profiles, lineage = [], {}
    for family, record in sorted(records.items()):
        if record["manifest"]["direction"] != direction:
            continue
        for index, trait in enumerate(record["traits"]):
            profile = candidate_profile(trait, direction)
            profiles.append(profile)
            lineage[f"{family}/{index}"] = digest(profile.model_dump(mode="json"))
    return profiles, lineage


def _value(vector: FeatureVector, name: str) -> float:
    return vector.values[vector.names.index(name)]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in a) * sum(value * value for value in b))
    if len(a) != 640 or len(b) != 640 or not denominator:
        raise ValueError("invalid description audit document vectors")
    return 1 - max(-1., min(1., sum(x * y for x, y in zip(a, b, strict=True)) / denominator))


def _publish(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(report, output, ensure_ascii=False, sort_keys=True, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (existing.get("report_hash") != digest({key: value for key, value in existing.items() if key != "report_hash"})
                    or existing != report):
                raise ValueError("immutable description repair report changed") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def audit_description_repairs(directory: Path, cohort_id: str,
                              new_sources: list[dict] | dict[str, dict],
                              old_sources: list[dict] | dict[str, dict]) -> dict:
    """Seal a pre-label drift audit of the three train-only prose variants over all 420 sources.

    Both corpora retain frozen split allocation, exact typed facts and original
    source seals. Old/new families use the same new cohort UUID namespace and cache,
    actual Harrier/runtime features, and their full global direction pools. No API
    request or teacher label is used; only local cache misses may run Harrier.
    """
    if type(cohort_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", cohort_id) is None:
        raise ValueError("invalid description repair cohort ID")
    directory = Path(directory)
    new, old = _records(new_sources), _records(old_sources)
    for family in sorted(old):
        a, b = old[family], new[family]
        if any(a["manifest"][key] != b["manifest"][key] for key in ("vacancy", "policy")):
            raise ValueError("description repair changed vacancy or policy")
        if [_facts(trait) for trait in a["traits"]] != [_facts(trait) for trait in b["traits"]]:
            raise ValueError("description repair changed frozen typed facts")
        expected = apply_description_repairs(a["traits"], DESCRIPTION_REPAIRS.get(family, []), a["manifest"]["split"])
        if expected != b["traits"]:
            raise ValueError("unapproved source description change")
    collisions = []
    for family, index, other, other_index in ORIGINAL_COLLISIONS:
        a, b = old[family], old[other]
        if (a["manifest"]["split"] != "train" or b["manifest"]["split"] == "train"
                or _normalized(a["traits"][index]["description"]) != _normalized(b["traits"][other_index]["description"])):
            raise ValueError("original description collision provenance differs")
        collisions.append({"train_family": family, "train_index": index, "other_family": other,
                           "other_index": other_index, "other_split": b["manifest"]["split"],
                           "original_text_hash": digest(a["traits"][index]["description"]),
                           "train_source_hash": a["record_hash"], "other_source_hash": b["record_hash"],
                           "correlation_eliminated": False})
    comparison_old, comparison_new = _text_comparisons(old), _text_comparisons(new)
    if comparison_new["cross_split_exact_count"]:
        raise ValueError("description repair leaves exact cross-split text collisions")
    owners = {}
    for family, record in sorted(new.items()):
        for trait in record["traits"]:
            fact_hash = digest(_facts(trait).hex())
            if owners.setdefault(fact_hash, family) != family:
                raise ValueError("description audit contains exact cross-family typed fact duplicates")
    seed = json.loads((directory / "semantic-cache.json").read_text(encoding="utf-8"))
    cache_path = directory / cohort_id / "semantic-cache.json"
    report_path = directory / cohort_id / "description-repair-report.json"
    source_hashes = {"old": {family: old[family]["record_hash"] for family in sorted(old)},
                     "new": {family: new[family]["record_hash"] for family in sorted(new)}}
    identity = {"source_hashes": source_hashes, "repairs_hash": digest(DESCRIPTION_REPAIRS),
                "semantic_seed_hash": digest(seed), "policy_hash": policy_hash(ScoringPolicy()),
                "feature_schema_hash": feature_schema(initial_schema()).schema_hash, "cohort_id": cohort_id}
    if report_path.exists():
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        if (saved.get("report_hash") != digest({key: value for key, value in saved.items() if key != "report_hash"})
                or saved.get("input_identity") != identity):
            raise ValueError("immutable description repair report identity changed")
    drift = []
    for family, repairs in DESCRIPTION_REPAIRS.items():
        direction = new[family]["manifest"]["direction"]
        old_pool, old_lineage = _pool(old, direction)
        new_pool, new_lineage = _pool(new, direction)
        item = new[family]["manifest"]
        old_vectors, old_scores, old_order, old_provenance = build_family_features(
            item, item["vacancy"], old[family]["traits"], cache_path, cohort_id,
            class_profiles=old_pool, class_source_lineage=old_lineage, semantic_seed=seed)
        new_vectors, new_scores, new_order, new_provenance = build_family_features(
            item, item["vacancy"], new[family]["traits"], cache_path, cohort_id,
            class_profiles=new_pool, class_source_lineage=new_lineage, semantic_seed=seed)
        if old_order != new_order:
            raise ValueError("description repair changed typed class ranking")
        for before, after in zip(old_vectors, new_vectors, strict=True):
            if before.names != after.names or before.schema_hash != after.schema_hash or before.available != after.available:
                raise ValueError("description repair changed feature availability or schema")
            if any(a != b for name, a, b in zip(before.names, before.values, after.values, strict=True)
                   if name not in ("main_score", "semantic_cosine")):
                raise ValueError("description repair changed nonsemantic features")
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        for repair in repairs:
            index = repair["index"]
            before_trait, after_trait = old[family]["traits"][index], new[family]["traits"][index]
            before_profile, after_profile = candidate_profile(before_trait, direction), candidate_profile(after_trait, direction)
            model = HarrierEncoder().model_version
            before_key = digest({"model": model, "text": build_semantic_text(before_profile)})
            after_key = digest({"model": model, "text": build_semantic_text(after_profile)})
            distance = _cosine_distance(cache[before_key], cache[after_key])
            semantic_delta = abs(_value(old_vectors[index], "semantic_cosine") - _value(new_vectors[index], "semantic_cosine"))
            score_delta = abs(old_scores[index] - new_scores[index])
            if (distance > MAX_DOCUMENT_COSINE_DISTANCE or semantic_delta > MAX_SEMANTIC_COSINE_DELTA
                    or score_delta > 2 * ScoringPolicy().semantic_correction):
                raise ValueError("description repair exceeds frozen semantic drift bounds")
            drift.append({"family_id": family, **repair, "typed_facts_hash": digest(_facts(before_trait).hex()),
                          "old_text_hash": digest(before_trait["description"]), "new_text_hash": digest(after_trait["description"]),
                          "old_document_key": before_key, "new_document_key": after_key,
                          "old_document_vector_hash": digest(cache[before_key]), "new_document_vector_hash": digest(cache[after_key]),
                          "old_feature_hash": digest(asdict(old_vectors[index])), "new_feature_hash": digest(asdict(new_vectors[index])),
                          "document_cosine_distance": distance, "semantic_cosine_delta": semantic_delta,
                          "old_main_score": old_scores[index], "new_main_score": new_scores[index], "main_score_delta": score_delta,
                          "availability_unchanged": True, "nonsemantic_features_unchanged": True,
                          "old_provenance_hash": digest(old_provenance), "new_provenance_hash": digest(new_provenance)})
    report = {"version": REPORT_VERSION, "cohort_id": cohort_id, "reviewer_kind": "assistant",
              "independent_human_review": False, "pre_label_audit": True, "source_families": 60,
              "source_profiles": 420, "derived_variants": 3, "typed_facts_byte_equal": True,
              "input_identity": identity, "original_collision_pairs": collisions,
              "near_templates": {"old": comparison_old, "new": comparison_new}, "repairs": drift,
              "bounds": {"document_cosine_distance": MAX_DOCUMENT_COSINE_DISTANCE,
                         "semantic_cosine_delta": MAX_SEMANTIC_COSINE_DELTA,
                         "main_score_delta": 2 * ScoringPolicy().semantic_correction},
              "independence_established": False, "status": "passed"}
    report["report_hash"] = digest(report)
    _publish(report_path, report)
    return report
