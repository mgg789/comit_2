"""One crash-safe, cache-only test fit of a fully sealed grounded cohort."""

import argparse
import fcntl
import itertools
import json
import math
import os
import tempfile
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import NAMESPACE_URL, uuid5

from matching_engine import rerank_cohort_pair_repair as pair_repair
from matching_engine import rerank_cohort_review as review
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
from matching_engine.models import GRADE_RANK, category_for, confirmed_strength
from matching_engine.rerank_cohort import _check_sources, _spent_profiles, pilot_cases
from matching_engine.rerank_cohort_features import (
    BUILD_VERSION,
    SEMANTIC_RECEIPT_VERSION,
    candidate_profile,
    legacy_traits,
)
from matching_engine.rerank_cohort_provider import (
    CohortProviders,
    decode_generation_response,
    teacher_identity,
)
from matching_engine.rerank_description_repair import apply_description_repairs
from matching_engine.rerank_features import (
    FeatureVector,
    build_features,
    feature_schema,
)
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import (
    RerankArtifact,
    promotion_eligible,
    scaled_features,
    validate_artifact,
)
from matching_engine.rerank_protocol import expected_families
from matching_engine.rerank_providers import GENERATION_MODEL, GENERATION_URL, LIMITS
from matching_engine.rerank_sample_features import FIXED_PUBLISHED_AT, SEED_PATH
from matching_engine.rerank_training import (
    C_GRID,
    SEED,
    TrainingFamily,
    evaluate,
    train,
    validate_families,
)
from matching_engine.strict import build_strict_vector, initial_schema
from matching_engine.strict_evidence import build_strict_evidence
from matching_engine.vacancy_features import (
    build_strict_features,
    semantic_text,
)
from matching_engine.vacancy_models import VacancyInput, VacancyReady, build_mode

ADAPTER_VERSION = "sealed-grounded-test-fit-v3"
DEFAULT_COHORT = "rerank-grounded-cohort-v9"
SPLITS = {"train": 32, "validation": 8, "calibration": 8, "golden": 12}
PAIR_REPAIR_MODULES = {pair_repair.VERSION: pair_repair}
HISTORICAL_PILOT_PINS = {
    "rerank-grounded-pair-repair-v1": {
        "source_commit": "7585ee7b382a406cd0e75a852883e36cd2f31790",
        "code_hash": "6c2e64302e7b11fdc3e373b9b8793f67cd45857e5c4b0566626db38302182f74",
        "observation_hash": "34c64ce384283b38689df6c7fb8adf4675f1d9035fda755f2c958e93c4df1597",
        "prompt_version": "rerank-uniform-cohort-pair-v3",
    },
    "rerank-grounded-pair-repair-v2": {
        "source_commit": "6d4179889e1f6d9dc42a4dbae7885cad038133e9",
        "code_hash": "3eff712ceda2179970eaf6598cd49f0e2c3b90d144a9ad1d127a7ebad9a5bc56",
        "observation_hash": "01660db08da76c30c06018d84de29ac402fa9d372f2ba612bb6ccdfe390b9c94",
        "prompt_version": "rerank-uniform-cohort-pair-v4",
    },
}
_REPAIR_SENTENCE = " Repair attempt: obey every required field, typed bound and exact citation; JSON only."


def _read(path: Path) -> dict:
    return review._read(path)


def _verified(path: Path, field: str) -> dict:
    return review._verified(path, field)


def _safe(directory: Path, relative: str) -> Path:
    path = directory / relative
    if path.resolve() == directory.resolve() or directory.resolve() not in path.resolve().parents:
        raise ValueError("training source path escapes artifact directory")
    return path


def _raw(directory: Path, key: str) -> dict:
    if type(key) is not str or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid pinned provider cache key")
    value = _read(directory / "provider-cache" / f"{key}.json")
    if (value.get("request_hash") != key or digest(value["request"]) != key
            or value.get("response_hash") != digest(value["response"])):
        raise ValueError("pinned provider receipt integrity changed")
    return value


class _CachedTeacher(CohortProviders):
    """Parse approved current teacher receipts with no credential, HTTP, or budget path."""

    def request(self, path: str, payload: dict, kind: str) -> dict:
        """Require the exact role/nonce/model payload in an existing sealed receipt."""
        expected = self._identity(path, payload, kind)
        key = digest(expected)
        receipt = _raw(self.directory.parent, key)
        if receipt["request"] != expected:
            raise ValueError("cached teacher request identity changed")
        self._last_request_hash = key
        return receipt["response"]


def _answer(directory: Path, target: Path, provider: _CachedTeacher,
            job_id: str, kind: str, expected: dict, execute) -> None:
    passed = []
    for attempt in (0, 1):
        path = target / "jobs" / f"{job_id}-{attempt}.json"
        if not path.exists():
            continue
        saved = _read(path)
        if saved.get("kind") != kind or saved.get("state") not in ("failed", "passed"):
            raise ValueError("ambiguous or invalid teacher job receipt")
        if saved["state"] == "passed":
            if saved.get("answer") != expected or saved.get("answer_hash") != digest(expected):
                raise ValueError("teacher job answer differs from dataset")
            raw = _raw(directory, saved["request_hash"])
            if raw["request"].get("attempt") != attempt:
                raise ValueError("teacher repair attempt identity changed")
            actual = execute(attempt)
            if actual != expected or provider._last_request_hash != saved["request_hash"]:
                raise ValueError("teacher cache replay differs from labeled answer")
            # execute uses an exact regenerated payload; also bind the job key to it.
            request = raw["request"]
            if request.get("kind") != kind or request.get("cohort_id") != provider.cohort_id:
                raise ValueError("teacher job belongs to another cohort or kind")
            passed.append(saved["request_hash"])
    if len(passed) != 1:
        raise ValueError("teacher job requires exactly one passed pinned receipt")


def _generation_source(directory: Path, target: Path, source: dict) -> None:
    item, traits = source["manifest"], source["traits"]
    repairs = item.get("description_repairs", [])
    if item.get("reuse"):
        link = item["reuse"]
        original = _verified(directory / "families" / f'{item["family_id"]}.json', "record_hash")
        if original["record_hash"] != link["record_hash"]:
            raise ValueError("historical source pin changed")
        raw_traits = legacy_traits(directory, original, link["indices"])
    else:
        bottom, folder, seen = source, target, set()
        link = item.get("prior_source")
        while link:
            path = _safe(directory, link["path"])
            if path in seen or path.parent.name != "sources":
                raise ValueError("invalid generation source ancestry")
            seen.add(path)
            prior = _verified(path, "record_hash")
            if prior["record_hash"] != link["record_hash"] or any(
                prior["manifest"][key] != item[key] for key in ("family_id", "source_id", "direction", "split", "vacancy", "policy")
            ):
                raise ValueError("generation source ancestry changed")
            if bottom is not source and prior["traits"] != bottom["traits"]:
                raise ValueError("generation ancestry raw traits differ")
            bottom, folder = prior, path.parent.parent
            parent = prior.get("source_receipts")
            link = parent if isinstance(parent, dict) and "path" in parent else None
        matching = []
        for path in (folder / "jobs").glob(f'generate-{item["family_id"]}-*.json'):
            job = _read(path)
            if job.get("state") == "passed" and job.get("answer") == bottom["traits"]:
                if job.get("answer_hash") != digest(bottom["traits"]):
                    raise ValueError("generation job answer hash changed")
                raw = _raw(directory, job["request_hash"])
                request = raw["request"]
                state = json.loads(request["payload"]["messages"][1]["content"])
                if (request.get("kind") != "generation" or request.get("path") != "/v1/chat/completions"
                        or request.get("endpoint") != GENERATION_URL
                        or state.get("family_id") != item["family_id"]
                        or request.get("cohort_id") != bottom["manifest"].get("cohort_id")):
                    raise ValueError("generation receipt family/model ancestry mismatch")
                if decode_generation_response(raw["response"], request["model"], request["prompt_version"]) != bottom["traits"]:
                    raise ValueError("canonical source differs from raw generation receipt")
                matching.append(job)
        if len(matching) != 1:
            raise ValueError("source requires exactly one accepted raw generation receipt")
        raw_traits = bottom["traits"]
    if apply_description_repairs(raw_traits, repairs, item["split"]) != traits:
        raise ValueError("training source differs from pinned raw source and approved derivations")


def _pilot_gate(directory: Path, target: Path, cohort_id: str, dataset: dict) -> dict:
    pilot = _verified(target / "pilot-report.json", "report_hash")
    packet = _verified(target / "pilot-packet.json", "packet_hash")
    cases = pilot_cases()
    if (packet.get("version") != cohort_id or packet.get("cases") != cases
            or pilot.get("packet_hash") != packet["packet_hash"] or pilot.get("passed") is not True
            or dataset.get("pilot_hash") != pilot["report_hash"]):
        raise ValueError("teacher pilot quality receipt mismatch")
    prior = pilot.get("cache_only_revalidated_pilot")
    origin = cohort_id if prior is None else prior["cohort_id"]
    if prior is not None:
        original = _verified(_safe(directory, f"{origin}/pilot-report.json"), "report_hash")
        if original["report_hash"] != prior["report_hash"] or original.get("passed") is not True:
            raise ValueError("pilot historical provenance mismatch")
    provider = _CachedTeacher(directory / "provider-cache", cohort_id=origin)
    pair_rows, grade_rows = [], []
    reverse = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}
    for case in cases:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            job = f'pilot-{case["case_id"]}-{int(swapped)}'
            accepted = [_read(path)["answer"] for path in (target / "jobs").glob(f"{job}-*.json")
                        if _read(path).get("state") == "passed"]
            if len(accepted) != 1:
                raise ValueError("pilot pair source receipt missing")
            answer = accepted[0]
            _answer(directory, target, provider, job, "pair", answer,
                    lambda attempt, case=case, a=a, b=b: provider.judge(case["vacancy"], a, b, case["policy"], attempt))
            expected = reverse[case["expected"]] if swapped else case["expected"]
            pair_rows.append({"case_id": case["case_id"], "reverse": swapped, "label": answer["label"],
                              "agrees": answer["label"] == expected})
        job = f'pilot-grade-{case["case_id"]}'
        accepted = [_read(path)["answer"] for path in (target / "jobs").glob(f"{job}-*.json")
                    if _read(path).get("state") == "passed"]
        if len(accepted) != 1:
            raise ValueError("pilot suitability source receipt missing")
        answer = accepted[0]
        _answer(directory, target, provider, job, "grade", answer,
                lambda attempt, case=case: provider.suitability(case["vacancy"], case["A"], case["policy"], attempt))
        grade_rows.append({"case_id": case["case_id"], "grade": answer["grade"], "agrees": answer["grade"] == case["expected_grade"]})
    if pilot.get("pairs") != pair_rows or pilot.get("grades") != grade_rows or not all(row["agrees"] for row in pair_rows + grade_rows):
        raise ValueError("pilot gate contradicts exact cached answers")
    return pilot


def _current_features(target: Path, item: dict, traits: list[dict], global_profiles: list,
                      semantics: dict, semantic_seed: dict, provenance: dict) -> tuple[list[FeatureVector], list[float], list[int]]:
    """Reconstruct current runtime snapshots with read-only cache access and real core feature logic."""
    cohort_id, family = item["cohort_id"], item["family_id"]
    keys, roles, receipts = [], [], []

    def embedding(encoder: HarrierEncoder, text: str) -> list[float]:
        key = digest({"model": encoder.model_version, "text": text})
        vector = semantics.get(key)
        if (type(vector) is not list or len(vector) != 640
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
                or not .99 <= math.sqrt(sum(value * value for value in vector)) <= 1.01):
            raise ValueError("training requires a valid existing Harrier vector; inference is forbidden")
        if key in semantic_seed:
            if digest(vector) != digest(semantic_seed[key]):
                raise ValueError("training semantic seed vector changed")
            receipt = {"key": key, "source": "verified_seed", "vector_hash": digest(vector)}
        else:
            expected = {"version": SEMANTIC_RECEIPT_VERSION, "namespace": cohort_id, "key": key,
                        "model": encoder.model_version, "text_hash": digest(text), "vector_hash": digest(vector)}
            expected["receipt_hash"] = digest(expected)
            saved = _verified(target / "semantic-cache.entries" / digest(cohort_id) / f"{key}.json", "receipt_hash")
            if saved != expected:
                raise ValueError("training semantic entry receipt changed")
            receipt = {"key": key, "source": "local_harrier_receipt", "receipt_hash": saved["receipt_hash"],
                       "vector_hash": saved["vector_hash"]}
        keys.append(key)
        roles.append({"key": key, "model": encoder.model_version, "role": encoder.role, "text_hash": digest(text)})
        receipts.append(receipt)
        return list(vector)

    schema = initial_schema()
    identifier = uuid5(NAMESPACE_URL, f"{cohort_id}/{family}")
    raw = deepcopy(item["vacancy"])
    salary = raw.pop("salary", {})
    for field, value in (("source_revision", 1), ("daily", "full_time"), ("zp_min", salary.get("min", 50000)),
                         ("zp_max", salary.get("max", 200000)), ("type", "remote"), ("city", "Москва")):
        raw.setdefault(field, value)
    raw.update(owner_id=str(identifier), status="open")
    source = VacancyInput.model_validate(raw)
    vector, mask = build_strict_features(source, schema)
    document, query = HarrierEncoder(), HarrierEncoder("vacancy_query")
    text = semantic_text(source)
    semantic = embedding(query, text) if text else None
    vacancy = VacancyReady(**source.model_dump(by_alias=True), vacancy_id=identifier, revision=1,
        build_mode=build_mode(source), strict_vector=vector, requirement_mask=mask,
        strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash, semantic_vector=semantic,
        semantic_model_version=query.model_version if semantic is not None else None,
        semantic_space_id=EMBEDDING_SPACE_ID if semantic is not None else None,
        semantic_available=semantic is not None, published_at=FIXED_PUBLISHED_AT)
    profiles = [candidate_profile(trait, item["direction"]) for trait in traits]
    strengths = [confirmed_strength(profile) for profile in profiles]
    ordering = sorted(range(7), key=lambda index: (-GRADE_RANK[profiles[index].grade_result.grade], -strengths[index], index))
    pool_order = [(GRADE_RANK[profile.grade_result.grade], confirmed_strength(profile)) for profile in global_profiles]
    candidates = []
    for index, profile in enumerate(profiles):
        rank = 1 + sum(other > (GRADE_RANK[profile.grade_result.grade], strengths[index]) for other in pool_order)
        candidates.append(MatchCandidate(candidate_id=uuid5(NAMESPACE_URL, f"{cohort_id}/{family}/{index}"),
            profile_revision=1, display_name=f"Synthetic {index}", city=profile.city, salary=profile.salary,
            category=category_for(profile), rank=rank, class_size=len(global_profiles),
            rating_score=1 - (rank - 1) / max(len(global_profiles) - 1, 1), confirmed_strength=strengths[index],
            strict_vector=build_strict_vector(profile, schema), strict_evidence=build_strict_evidence(profile, schema),
            strict_schema_version=schema.version, strict_schema_hash=schema.schema_hash,
            semantic_vector=embedding(document, build_semantic_text(profile)), semantic_model_version=document.model_version,
            semantic_space_id=EMBEDDING_SPACE_ID, published_at=FIXED_PUBLISHED_AT, resume_url="/synthetic-public.pdf",
            work_types=profile.work_types, narrative_facts=project_narrative_facts({"projects": profile.projects,
                "experience": profile.experience, "achievements": profile.achievements})))
    selected = select_candidates(vacancy, candidates, ScoringPolicy(), 7)
    cards = {card.candidate_id: card for card in selected.items}
    if len(cards) != 7:
        raise ValueError("current strict policy excludes a sealed training source")
    expected = {"build_version": BUILD_VERSION, "strict_schema_hash": schema.schema_hash,
                "profile_snapshot_hashes": [digest(profile.model_dump(mode="json")) for profile in profiles],
                "candidate_snapshot_hashes": [digest(candidate.model_dump(mode="json")) for candidate in candidates],
                "vacancy_snapshot_hash": digest(vacancy.model_dump(mode="json")),
                "semantic_keys": keys, "semantic_roles": roles, "semantic_entry_receipts": receipts,
                "semantic_seed_hash": digest(semantic_seed),
                "semantic_vectors_hash": digest({key: semantics[key] for key in sorted(set(keys))})}
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError("training runtime snapshot/semantic provenance changed")
    return ([build_features(vacancy, candidate, cards[candidate.candidate_id], schema) for candidate in candidates],
            [cards[candidate.candidate_id].explanation.score for candidate in candidates], ordering)


def _review_metadata_rebind(directory: Path, target: Path, packet: dict, manual: dict) -> dict | None:
    path = target / "review-metadata-rebind-receipt.json"
    if not path.exists():
        return None
    receipt = _verified(path, "receipt_hash")
    previous_packets = []
    for namespace in HISTORICAL_PILOT_PINS:
        previous = directory / namespace / "reviews"
        if (previous / "assistant-packet.json").exists():
            candidate = _verified(previous / "assistant-packet.json", "packet_hash")
            if candidate["packet_hash"] == receipt.get("old_packet_hash"):
                previous_packets.append((candidate, _read(previous / "assistant-review.json")))
    if len(previous_packets) != 1:
        raise ValueError("blind review metadata rebind lacks a unique pinned historical packet")
    old_packet, old_manual = previous_packets[0]
    review._manual(old_manual, old_packet)
    ignored = {"packet_hash", "pair_repair_pilot_manifest_hash"}
    if ({key: value for key, value in old_packet.items() if key not in ignored}
            != {key: value for key, value in packet.items() if key not in ignored}
            or {key: value for key, value in old_manual.items() if key != "packet_hash"}
            != {key: value for key, value in manual.items() if key != "packet_hash"}
            or receipt.get("version") != "blind-review-metadata-rebind-v1"
            or receipt.get("old_packet_hash") != old_packet["packet_hash"]
            or receipt.get("new_packet_hash") != packet["packet_hash"]
            or receipt.get("old_pilot_manifest_hash") != old_packet["pair_repair_pilot_manifest_hash"]
            or receipt.get("new_pilot_manifest_hash") != packet["pair_repair_pilot_manifest_hash"]
            or receipt.get("old_review_hash") != digest(old_manual) or receipt.get("new_review_hash") != digest(manual)
            or receipt.get("cases_hash") != digest(packet["cases"])
            or receipt.get("source_hashes") != packet["source_hashes"]
            or receipt.get("policy_revision") != packet["policy_revision"]
            or receipt.get("labels_hash") != digest(manual["labels"]) or receipt.get("label_changes") != 0
            or receipt.get("cases_equal") is not True or receipt.get("new_blind_review") is not False
            or receipt.get("claimed_human_review") is not False):
        raise ValueError("blind review metadata rebind changed decisions, facts, policies or lineage")
    return receipt


def _review_gate(directory: Path, cohort_id: str, manifest: dict, sources: dict, dataset: dict,
                 assistant_review_file: Path, adjudication_file: Path | None, *,
                 review_directory: Path | None = None, expected_packet: dict | None = None) -> dict:
    target = review_directory if review_directory is not None else directory / cohort_id / "reviews"
    packet = _verified(target / "assistant-packet.json", "packet_hash")
    expected = expected_packet if expected_packet is not None else review._packets(manifest, sources, cohort_id)["assistant"]
    if packet != expected:
        raise ValueError("assistant packet differs from frozen score-blind allocation")
    manual = _read(assistant_review_file)
    labels = review._manual(manual, packet)
    if manual["reviewer_kind"] != "assistant":
        raise ValueError("test fit requires the assistant20 review")
    metadata_rebind = _review_metadata_rebind(directory, target, packet, manual)
    comparison = _verified(target / f"assistant-comparison-{digest(manual)}.json", "report_hash")
    if (comparison.get("dataset_hash") != dataset["dataset_hash"] or comparison.get("packet_hash") != packet["packet_hash"]
            or comparison.get("manifest_hash") != manifest["manifest_hash"]
            or comparison.get("source_hashes") != packet["source_hashes"]
            or comparison.get("review_hash") != digest(manual) or comparison.get("reviewer_kind") != "assistant"
            or comparison.get("reviewed_pairs") != 20 or comparison.get("activation_allowed") is not False
            or comparison.get("claimed_human_review") is not False):
        raise ValueError("assistant comparison provenance mismatch")
    families = {family["manifest"]["family_id"]: family for family in dataset["families"]}
    expected_rows, case_map = [], {case["pair_id"]: case for case in packet["cases"]}
    for case in packet["cases"]:
        label = next(pair[2] for pair in families[case["family_id"]]["pairs"]
                     if pair[:2] == [case["a_index"], case["b_index"]])
        answer = labels[case["pair_id"]]
        expected_rows.append({"pair_id": case["pair_id"], "family_id": case["family_id"],
                              "review_label": answer["label"], "teacher_label": label,
                              "category": review._category(answer["label"], label), "reason": answer["reason"],
                              "unresolved_factual_issue": answer.get("factual_issue", False)})
    counts = dict(Counter(row["category"] for row in expected_rows))
    unresolved = sum(row["unresolved_factual_issue"] for row in expected_rows)
    if (comparison.get("comparisons") != expected_rows or comparison.get("category_counts") != counts
            or comparison.get("agreements") != counts.get("agreement", 0)
            or comparison.get("disagreements") != 20 - counts.get("agreement", 0)
            or comparison.get("unresolved_factual_issues") != unresolved):
        raise ValueError("assistant comparison contradicts source review or teacher labels")
    blocked = {row["pair_id"]: row for row in expected_rows
               if row["category"] != "agreement" or row["unresolved_factual_issue"]}
    adjudication = None
    if adjudication_file is not None:
        adjudication = _verified(adjudication_file, "adjudication_hash")
        if (adjudication.get("dataset_hash") != dataset["dataset_hash"] or adjudication.get("packet_hash") != packet["packet_hash"]
                or adjudication.get("review_hash") != digest(manual) or adjudication.get("labels_changed") is not False):
            raise ValueError("adjudication is not pinned to current unchanged labels")
        resolutions = adjudication.get("resolutions")
        if (type(resolutions) is not list or len(resolutions) != len(blocked)
                or any(type(row) is not dict or row.get("pair_id") not in blocked for row in resolutions)
                or {row["pair_id"] for row in resolutions} != set(blocked)):
            raise ValueError("adjudication must cover every blocked pair exactly once")
        for row in resolutions:
            problem = blocked[row["pair_id"]]
            if (set(row) != {"pair_id", "outcome", "teacher_label", "assistant_label", "reason", "factual_issue_resolved"}
                    or row.get("teacher_label") != problem["teacher_label"]
                    or row.get("assistant_label") != problem["review_label"]
                    or row.get("outcome") not in ("teacher_label_confirmed", "assistant_label_confirmed")
                    or row.get("factual_issue_resolved") is not True or type(row.get("reason")) is not str
                    or not 20 <= len(row["reason"].strip()) <= 2000):
                raise ValueError("adjudication requires exact two-sided labels, explicit outcome and per-pair reason")
        if len(resolutions) > 1 and len({row["reason"].strip() for row in resolutions}) != len(resolutions):
            raise ValueError("blanket adjudication reasons cannot resolve distinct factual comparisons")
    elif blocked:
        raise ValueError("assistant disagreements or factual issues require explicit adjudication before fitting")
    resolutions, corrections = [], []
    if adjudication is not None:
        for row in sorted(adjudication["resolutions"], key=lambda item: item["pair_id"]):
            case = case_map[row["pair_id"]]
            derived = row["teacher_label"] if row["outcome"] == "teacher_label_confirmed" else row["assistant_label"]
            resolution = {**row, "family_id": case["family_id"], "a_index": case["a_index"],
                          "b_index": case["b_index"], "split": case["split"], "derived_label": derived}
            resolutions.append(resolution)
            if derived != row["teacher_label"]:
                corrections.append(resolution)
    return {"packet_hash": packet["packet_hash"], "review_hash": digest(manual),
            "comparison_hash": comparison["report_hash"], "reviewed_pairs": 20,
            "original_disagreements": len([row for row in expected_rows if row["category"] != "agreement"]),
            "original_factual_issues": unresolved, "unresolved_pairs": 0,
            "adjudication_hash": None if adjudication is None else adjudication["adjudication_hash"],
            "resolutions": resolutions, "corrections": corrections,
            "correction_pairs_limit": 20, "labels_changed_in_raw_dataset": False,
            "original_reverse_audits_validate_derived_corrected_pairs": False,
            "review_metadata_rebind": metadata_rebind,
            "new_blind_review_claimed": None if metadata_rebind is None else False,
            "user_review_required_for_test_fit": False, "independent_human_evaluation": False}


def _registered_repair(directory: Path, cohort_id: str, derived_dataset_file: Path) -> ModuleType:
    for namespace, adapter in PAIR_REPAIR_MODULES.items():
        if derived_dataset_file.resolve() == (directory / namespace / "dataset.json").resolve():
            if cohort_id != adapter.BASE_COHORT or namespace != adapter.VERSION:
                raise ValueError("derived repair registration or base cohort mismatch")
            return adapter
    raise ValueError("derived dataset file must identify an explicitly registered pinned pair-repair namespace")


def _base_charges(directory: Path, target: Path, cohort_id: str, attempts: dict, pilot: dict) -> dict:
    observed, cached = dict.fromkeys(LIMITS, 0), dict.fromkeys(LIMITS, 0)
    rows = {}
    origin = pilot.get("cache_only_revalidated_pilot")
    cached_origin = None if origin is None else origin["cohort_id"]
    for path in sorted((target / "jobs").glob("*.json")):
        job = _read(path)
        kind = job.get("kind")
        if kind not in observed or job.get("state") not in ("failed", "passed"):
            raise ValueError("base reservation has no terminal approved kind")
        raw = _raw(directory, job["request_hash"])
        request = raw["request"]
        if request.get("kind") != kind or request.get("endpoint") != GENERATION_URL or request.get("path") != "/v1/chat/completions":
            raise ValueError("base reservation endpoint/kind changed")
        attempt = request.get("attempt")
        state = json.loads(request["payload"]["messages"][1]["content"])
        owner = request.get("cohort_id")
        if (type(attempt) is not int or attempt not in (0, 1) or not path.stem.endswith(f"-{attempt}")
                or state.get("request_nonce") != {"cohort_id": owner, "kind": kind, "attempt": attempt}):
            raise ValueError("base reservation attempt/nonce changed")
        if kind in ("pair", "grade"):
            identity = teacher_identity(owner, kind, attempt)
            if any(request.get(key) != value for key, value in identity.items()) or request["payload"].get("model") != GENERATION_MODEL:
                raise ValueError("base reservation teacher identity changed")
        observed[kind] += 1
        if owner != cohort_id:
            if (owner != cached_origin or owner != "rerank-grounded-cohort-v5" or not path.name.startswith("pilot-")
                    or attempt != 0 or kind not in ("pair", "grade") or job["state"] != "passed"):
                raise ValueError("unexpected cache-only historical reservation")
            cached[kind] += 1
        rows[path.name] = {"job_hash": digest(job), "request_hash": job["request_hash"],
                           "response_hash": raw["response_hash"]}
    if observed != attempts or (cached_origin is not None and cached != {"generation": 0, "pair": 20, "grade": 10}):
        raise ValueError("base attempt budget does not reconcile exact reservations/cache-only pilot")
    return {"reserved": observed, "cached_pilot_reservations": cached,
            "charged": {kind: observed[kind] - cached[kind] for kind in observed}, "jobs_hash": digest(rows)}


def _concurrent_pilots(directory: Path, manifest: dict, sources: dict, before: dict, after: dict,
                       attempts: dict, repair: tuple | None) -> dict:
    known = set(HISTORICAL_PILOT_PINS)
    pins, count = {}, 0
    for namespace in sorted(known):
        target = directory / namespace
        if not (target / "pilot-manifest.json").exists():
            continue
        pilot_manifest = _verified(target / "pilot-manifest.json", "manifest_hash")
        start = pilot_manifest["ledger_before_pilot"]
        if start["pair"] >= after["pair"]:
            continue  # Its charges belong to a post-closure repair, checked separately.
        observation = _verified(target / "closure-observation.json", "observation_hash")
        snapshot = observation["global_ledger"]
        if snapshot["pair"] <= before["pair"]:
            continue  # Already contained in the base's frozen starting global ledger.
        historical = HISTORICAL_PILOT_PINS.get(namespace)
        adapter = PAIR_REPAIR_MODULES.get(namespace)
        expected_code = historical["code_hash"] if historical else (
            digest(Path(adapter.__file__).read_bytes().hex()) if adapter is not None else None)
        prompt = historical["prompt_version"] if historical else (adapter.PAIR_PROMPT_VERSION if adapter is not None else None)
        report = _verified(target / "pilot-report.json", "report_hash")
        cases, requests = pilot_manifest["cases"], pilot_manifest["requests"]
        if (expected_code is None or pilot_manifest.get("code_hash") != expected_code
                or historical and observation["observation_hash"] != historical["observation_hash"]
                or pilot_manifest.get("version") != namespace or pilot_manifest.get("base_cohort") != manifest["version"]
                or pilot_manifest.get("base_manifest_hash") != manifest["manifest_hash"]
                or pilot_manifest.get("source_hashes") != {key: value["record_hash"] for key, value in sources.items()}
                or len(cases) != 10 or len({case["case_id"] for case in cases}) != 10 or len(requests) != 20
                or len({digest(request) for request in requests}) != 20
                or report.get("pilot_manifest_hash") != pilot_manifest["manifest_hash"]
                or any(report.get(key) != 0 for key in ("generation_calls", "grade_calls"))
                or report.get("activation_allowed") is not False):
            raise ValueError("concurrent pilot frozen manifest/code/request provenance changed")
        for identifier, expected_hash in pilot_manifest["pinned_base_family_audits"].items():
            if (identifier not in sources or _verified(directory / manifest["version"] / "families" / f"{identifier}.json", "record_hash")["record_hash"]
                    != expected_hash):
                raise ValueError("concurrent pilot changed a preregistered base family receipt")
        expected_jobs, raw_hashes, rows = {}, {}, []
        initial, retry = 0, 0
        for case_index, case in enumerate(cases):
            for swapped in (False, True):
                initial_request = requests[case_index * 2 + int(swapped)]
                state = json.loads(initial_request["payload"]["messages"][1]["content"])
                a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
                identity = {"kind": "pair", "endpoint": GENERATION_URL, "path": "/v1/chat/completions",
                            "model": GENERATION_MODEL, "cohort_id": namespace, "attempt": 0, "prompt_version": prompt}
                if (any(initial_request.get(key) != value for key, value in identity.items())
                        or initial_request["payload"].get("model") != GENERATION_MODEL
                        or any(state.get(key) != value for key, value in {"vacancy": case["vacancy"], "A": a, "B": b,
                            "policy": case["policy"], "request_nonce": {"cohort_id": namespace, "kind": "pair", "attempt": 0}}.items())):
                    raise ValueError("concurrent pilot initial request differs from exact frozen case")
                accepted, previous = None, None
                for attempt in (0, 1):
                    path = target / "jobs" / f'pilot-{case["case_id"]}-{int(swapped)}-{attempt}.json'
                    if not path.exists():
                        if attempt == 0:
                            raise ValueError("concurrent pilot omitted an initial reservation")
                        continue
                    job = _read(path)
                    if (job.get("kind") != "pair" or job.get("category") != "pilot" or job.get("attempt") != attempt
                            or job.get("state") not in ("passed", "failed") or previous == "passed"):
                        raise ValueError("concurrent pilot reservation is ambiguous or exceeds one repair")
                    raw = _raw(directory, job["request_hash"])
                    request = raw["request"]
                    if attempt == 0:
                        if request != initial_request:
                            raise ValueError("concurrent pilot raw request differs from frozen initial identity")
                        initial += 1
                    else:
                        normalized = deepcopy(request)
                        normalized["attempt"], normalized["prompt_version"] = 0, prompt
                        retry_state = json.loads(normalized["payload"]["messages"][1]["content"])
                        if retry_state.get("request_nonce") != {"cohort_id": namespace, "kind": "pair", "attempt": 1}:
                            raise ValueError("concurrent pilot retry nonce changed")
                        retry_state["request_nonce"]["attempt"] = 0
                        normalized["payload"]["messages"][1]["content"] = initial_request["payload"]["messages"][1]["content"]
                        system = normalized["payload"]["messages"][0]["content"]
                        if system.count(_REPAIR_SENTENCE) != 1:
                            raise ValueError("concurrent pilot retry instruction changed")
                        normalized["payload"]["messages"][0]["content"] = system.replace(_REPAIR_SENTENCE, "", 1)
                        if (request.get("prompt_version") != prompt + "-repair1" or retry_state != state
                                or normalized != initial_request):
                            raise ValueError("concurrent pilot retry changed original facts/policy/identity")
                        retry += 1
                    if job["state"] == "passed":
                        if job.get("answer_hash") != digest(job.get("answer")):
                            raise ValueError("concurrent pilot accepted answer hash changed")
                        accepted = job["answer"]
                    previous = job["state"]
                    expected_jobs[path.name] = job
                    raw_hashes[job["request_hash"]] = raw["response_hash"]
                expected = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[case["expected"]] if swapped else case["expected"]
                row = report["rows"][case_index * 2 + int(swapped)]
                if (row.get("case_id") != case["case_id"] or row.get("swapped") is not swapped
                        or row.get("expected") != expected or row.get("answer") != accepted):
                    raise ValueError("concurrent pilot report differs from terminal reservation evidence")
                rows.append(row)
        all_jobs = {path.name: _read(path) for path in (target / "jobs").glob("*.json")}
        pilot_jobs = {name: job for name, job in all_jobs.items() if job.get("category") == "pilot"}
        if pilot_jobs != expected_jobs or initial != 20 or not 0 <= retry <= 20:
            raise ValueError("concurrent pilot reservation count differs from frozen 20 plus bounded retries")
        if len(all_jobs) != len(pilot_jobs):
            if repair is None or repair[2].VERSION != namespace:
                raise ValueError("unapproved family reservations precede base closure")
            selection = _verified(target / "manifest.json", "manifest_hash")
            if selection["base_dataset_hash"] != repair[1]["base_dataset_hash"] or selection["ledger_before"] != after:
                raise ValueError("repair family reservations lack exact post-base-closure selection")
        budget = _read(target / "budget.json")
        observed_budget = dict.fromkeys(("pilot_initial", "pilot_retry", "family_initial", "family_retry"), 0)
        for job in all_jobs.values():
            if job.get("category") not in ("pilot", "family") or job.get("attempt") not in (0, 1):
                raise ValueError("concurrent repair has an unknown reservation category")
            observed_budget[f'{job["category"]}_{"retry" if job["attempt"] else "initial"}'] += 1
        valid = sum(row["answer"] is not None for row in rows)
        correct = sum(row["answer"] is not None and row["answer"]["label"] == row["expected"] for row in rows)
        swaps = sum(rows[i]["answer"] is not None and rows[i + 1]["answer"] is not None
                    and rows[i + 1]["answer"]["label"] == {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[rows[i]["answer"]["label"]]
                    for i in range(0, 20, 2))
        if (budget != observed_budget or budget.get("pilot_initial") != initial or budget.get("pilot_retry") != retry
                or report.get("valid") != valid or report.get("correct") != correct or report.get("swap_correct") != swaps
                or report.get("passed") is not (valid == correct == 20 and swaps == 10)
                or observation.get("pilot_manifest_hash") != pilot_manifest["manifest_hash"]
                or observation.get("pilot_report_hash") != report["report_hash"]
                or observation.get("pilot_passed") is not report["passed"]
                or observation.get("job_receipts_hash") != digest(expected_jobs)
                or observation.get("raw_response_hashes") != raw_hashes
                or observation.get("actual_pair_reservations") != initial + retry
                or observation.get("base_manifest_hash") != manifest["manifest_hash"]
                or observation.get("base_cohort") != manifest["version"] or observation.get("cohort_id") != namespace
                or observation.get("base_dataset_existed") is not False
                or type(observation.get("base_completed_families")) is not int or not 0 <= observation["base_completed_families"] < 60
                or observation.get("observer_kind") != "assistant"
                or any(not before[kind] <= start[kind] <= snapshot[kind] <= after[kind] for kind in LIMITS)
                or start["pair"] + initial + retry > snapshot["pair"]
                or any(not 0 <= observation["base_attempt_budget"][kind] <= attempts[kind] for kind in LIMITS)):
            raise ValueError("concurrent pilot closure/budget/ledger observation changed")
        stamp = datetime.fromisoformat(observation["observed_at"])
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("concurrent pilot closure observation must use UTC")
        if namespace == "rerank-grounded-pair-repair-v2" and report.get("ledger_after_pilot") != snapshot:
            raise ValueError("current pilot report/closure ledger snapshot differs")
        pins[namespace] = {"manifest_hash": pilot_manifest["manifest_hash"], "report_hash": report["report_hash"],
                           "code_hash": expected_code, "observation_hash": observation["observation_hash"],
                           "source_commit": None if historical is None else historical["source_commit"],
                           "job_receipts_hash": digest(expected_jobs), "raw_response_hashes": raw_hashes,
                           "charged_pair_attempts": initial + retry, "pilot_passed": report["passed"],
                           "accounting_only_no_label_admission": True, "local_operator_observation": True,
                           "remote_timestamp_attestation": False}
        count += initial + retry
    return {"charged_pair_attempts": count, "pilots": pins}


def _repair_context(directory: Path, cohort_id: str, dataset: dict, cohort_report: dict,
                    derived_dataset_file: Path | None) -> tuple[dict, dict, ModuleType] | None:
    if derived_dataset_file is None:
        return None
    adapter = _registered_repair(directory, cohort_id, derived_dataset_file)
    effective, report = adapter.validate_pair_repair(directory, cohort_id=cohort_id,
                                                    derived_dataset_path=derived_dataset_file)
    if (effective.get("version") != adapter.VERSION
            or effective.get("derived_from") != {"cohort_id": cohort_id, "dataset_hash": dataset["dataset_hash"],
                                                   "manifest_hash": dataset["manifest_hash"]}
            or report.get("base_report_hash") != cohort_report["report_hash"]
            or report.get("base_dataset_hash") != dataset["dataset_hash"]
            or report.get("dataset_hash") != effective["dataset_hash"]
            or report.get("state") != "repaired_quality_passed"
            or effective.get("quality", {}).get("passed") is not True
            or report.get("quality") != effective["quality"]
            or report.get("failed_families") != []
            or any(report.get(field) != 0 for field in ("generation_calls", "grade_calls", "golden_model_predictions"))
            or any(value.get("activation_allowed") is not False or value.get("training_started") is not False
                   for value in (effective, report))
            or report.get("ledger_before") != cohort_report["ledger_after"]):
        raise ValueError("derived pair repair has not passed its exact isolated pre-fit gate")
    original = {family["manifest"]["family_id"]: family for family in dataset["families"]}
    preserved = ("manifest", "vacancy", "traits", "source_hash", "hash", "vectors", "main_scores",
                 "profile_order", "provenance", "class_pool_hash", "grades", "suitability_answers", "slice_tags")
    if (len(effective["families"]) != 60
            or Counter(family["manifest"]["family_id"] for family in effective["families"]) != Counter(original.keys())
            or any(any(family.get(key) != original[family["manifest"]["family_id"]].get(key) for key in preserved)
                   for family in effective["families"])):
        raise ValueError("derived repair changed frozen source/features/independent suitability")
    quality = effective["quality"]
    if (quality.get("swap_total") != 132 or quality.get("swap_correct", 0) < 119
            or quality.get("teacher_passed") is not True or quality.get("oracle_passed") is not True
            or quality.get("oracle_swap_correct") != 48 or quality.get("oracle_swap_total") != 48
            or quality.get("golden_families") != 12 or quality.get("minimum_golden_decisive_pairs", 0) < 6
            or quality.get("quarantined_families") != []):
        raise ValueError("hybrid repair must pass separate 132 teacher and 48 rule reverse gates")
    return effective, report, adapter


def _label_provenance(directory: Path, dataset: dict, repair: tuple | None) -> dict:
    """Describe verified label sources and allocation without rerunning a label rule."""
    sources, splits, local_ids = {}, {}, []
    for family in dataset["families"]:
        item, revision = family["manifest"], family.get("pair_repair")
        identifier, split = item["family_id"], item["split"]
        source = "llm_pair_teacher" if revision is None else revision.get("label_source")
        if source not in ("llm_pair_teacher", "rule_derived_class_only"):
            raise ValueError("unknown effective pair-label source")
        class_only = not any(family["vacancy"][key] for key in ("domens", "stacks", "soft_skills"))
        if source == "rule_derived_class_only":
            if repair is None or not class_only or split not in ("train", "validation"):
                raise ValueError("rule-derived labels must stay in class-only train/validation families")
            local_ids.append(identifier)
        elif repair is not None and class_only:
            raise ValueError("hybrid class-only source lacks explicit rule-derived lineage")
        sources[identifier] = source
        splits.setdefault(source, dict.fromkeys(SPLITS, 0))[split] += 1
    receipts, oracle_hash, metrics = {}, None, None
    if repair is not None:
        adapter = repair[2]
        metrics = dataset["source_metrics"]
        expected_metrics = {"rule_derived_class_only": {"families": 16, "reverse_proofs": 48,
                            "oracle_version": adapter.oracle.VERSION},
                            "llm_pair_teacher": {"families": 44, "reverse_comparisons": 132},
                            "human_review_verified": False}
        if (metrics != expected_metrics or repair[1]["source_metrics"] != metrics
                or splits.get("rule_derived_class_only") != {"train": 8, "validation": 8, "calibration": 0, "golden": 0}
                or splits.get("llm_pair_teacher") != {"train": 24, "validation": 0, "calibration": 8, "golden": 12}):
            raise ValueError("hybrid source metrics or split allocation changed")
        oracle_hash = dataset["oracle_code_hash"]
        if oracle_hash != repair[1]["oracle_code_hash"]:
            raise ValueError("hybrid oracle code provenance differs between sealed dataset/report")
        # Public validate_pair_repair already verifies every rule input, answer and code hash.
        pairs = list(itertools.combinations(range(7), 2))
        orientations = [*pairs, *((pairs[i][1], pairs[i][0]) for i in (0, 7, 14))]
        expected_names = {f"{identifier}-pair-{a}-{b}.json" for identifier in local_ids for a, b in orientations}
        folder = directory / adapter.VERSION / "oracle-receipts"
        if {path.name for path in folder.glob("*.json")} != expected_names:
            raise ValueError("hybrid local receipt set must contain exactly 384 admitted rule decisions")
        receipts = {name: _verified(folder / name, "record_hash")["record_hash"] for name in sorted(expected_names)}
    return {"by_family": dict(sorted(sources.items())), "split_counts": splits,
            "source_metrics": metrics, "oracle_code_hash": oracle_hash,
            "oracle_local_receipts": len(receipts), "oracle_local_receipts_hash": digest(receipts),
            "oracle_remote_calls": 0, "human_review_verified": False,
            "validation_entirely_rule_derived": repair is not None,
            "selection_limitation": (None if repair is None else
                "All eight validation families use the synthetic class-only rule; C selection does not independently validate LLM nonclass preferences."),
            "golden_oracle_families": 0, "source_metrics_used_for_tuning": False}


def _repair_packet(cohort_id: str, manifest: dict, sources: dict, repair_report: dict, adapter: ModuleType) -> dict:
    original = review._packets(manifest, sources, cohort_id)["assistant"]
    packet = deepcopy(original)
    packet.pop("packet_hash")
    packet.update(base_packet_hash=original["packet_hash"],
                  pair_repair_pilot_manifest_hash=repair_report["pilot_manifest_hash"],
                  policy_revision={"version": adapter.CLASS_ONLY_POLICY["version"],
                                   "hash": adapter.CLASS_ONLY_POLICY_HASH})
    for case in packet["cases"]:
        case["policy"] = adapter.revised_policy(case["vacancy"], case["policy"])
    packet["packet_hash"] = digest(packet)
    return packet


def compare_test_only_review(directory: Path, assistant_review_file: Path, cohort_id: str = DEFAULT_COHORT, *,
                             derived_dataset_file: Path | None = None) -> dict:
    """Seal an assistant20 comparison against current labels, without fitting or relabeling.

    The optional derived path selects the verified pair-repair labels and the new
    policy-corrected packet with the original blind selection. Raw facts and labels
    stay immutable; this comparison does not resolve disagreements or factual issues.
    """
    directory = Path(directory)
    if derived_dataset_file is None:
        return review.compare_review(directory, Path(assistant_review_file), cohort_id)
    adapter = _registered_repair(directory, cohort_id, Path(derived_dataset_file))
    manifest, sources = review._sources(directory, cohort_id)
    dataset, repair_report = adapter.validate_pair_repair(
        directory, cohort_id=cohort_id, derived_dataset_path=Path(derived_dataset_file))
    target = directory / adapter.VERSION / "reviews"
    packet = _verified(target / "assistant-packet.json", "packet_hash")
    if packet != _repair_packet(cohort_id, manifest, sources, repair_report, adapter):
        raise ValueError("derived review packet differs from fixed blind selection and revised policy")
    manual = _read(Path(assistant_review_file))
    if manual.get("reviewer_kind") != "assistant":
        raise ValueError("test fit comparison requires the assistant20 review")
    labels = review._manual(manual, packet)
    metadata_rebind = _review_metadata_rebind(directory, target, packet, manual)
    families = {family["manifest"]["family_id"]: family for family in dataset["families"]}
    rows = []
    for case in packet["cases"]:
        teacher = next(pair[2] for pair in families[case["family_id"]]["pairs"]
                       if pair[:2] == [case["a_index"], case["b_index"]])
        answer = labels[case["pair_id"]]
        rows.append({"pair_id": case["pair_id"], "family_id": case["family_id"], "review_label": answer["label"],
                     "teacher_label": teacher, "category": review._category(answer["label"], teacher),
                     "reason": answer["reason"], "unresolved_factual_issue": answer.get("factual_issue", False)})
    counts = dict(Counter(row["category"] for row in rows))
    unresolved = sum(row["unresolved_factual_issue"] for row in rows)
    report = {"version": review.REVIEW_VERSION, "cohort_id": cohort_id, "reviewer_kind": "assistant",
              "packet_hash": packet["packet_hash"], "manifest_hash": manifest["manifest_hash"],
              "dataset_hash": dataset["dataset_hash"], "source_hashes": packet["source_hashes"],
              "review_hash": digest(manual), "reviewed_pairs": 20, "category_counts": counts,
              "agreements": counts.get("agreement", 0), "disagreements": 20 - counts.get("agreement", 0),
              "unresolved_factual_issues": unresolved, "factual_issues_block_activation": unresolved > 0,
              "claimed_human_review": False, "human_authorship_verified": False, "independent_human_evaluation": False,
              "activation_allowed": False, "labels_changed": False, "comparisons": rows,
              "pair_repair_report_hash": repair_report["report_hash"], "base_packet_hash": packet["base_packet_hash"],
              "policy_revision": packet["policy_revision"]}
    report["review_metadata_rebind_hash"] = None if metadata_rebind is None else metadata_rebind["receipt_hash"]
    report["report_hash"] = digest(report)
    with (target / ".lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _seal(target / f'assistant-comparison-{report["review_hash"]}.json', report)
    return report


def _validated(directory: Path, cohort_id: str, assistant_review_file: Path,
               adjudication_file: Path | None, derived_dataset_file: Path | None = None) -> tuple[list[TrainingFamily], dict]:
    target = review._target(directory, cohort_id)
    manifest, sources = review._sources(directory, cohort_id)
    if (manifest.get("profiles") != 420 or manifest.get("split_counts") != SPLITS
            or manifest.get("activation_allowed") is not False or manifest.get("global_limits") != LIMITS):
        raise ValueError("training manifest profile/split/global-cap contract mismatch")
    for name, expected in manifest["code_hashes"].items():
        if Path(name).name != name or digest((Path(__file__).parent / name).read_bytes().hex()) != expected:
            raise ValueError("frozen cohort code changed before fitting")
    required_code = {"rerank_cohort.py", "rerank_cohort_provider.py", "rerank_cohort_features.py",
                     "rerank_description_repair.py", "rerank_features.py", "combine_scoring.py", "models.py",
                     "embeddings.py", "strict.py", "strict_evidence.py", "vacancy_features.py"}
    if not required_code <= set(manifest["code_hashes"]) or digest(SEED_PATH.read_bytes().hex()) != manifest["seed_hash"]:
        raise ValueError("frozen code or candidate seed identity missing")
    for name, expected in manifest["protected_source_hashes"].items():
        if digest(_safe(directory, name).read_bytes().hex()) != expected:
            raise ValueError("protected historical source bytes changed")
    dataset = review._dataset(directory, cohort_id, manifest, sources)
    cohort_report = _verified(target / "report.json", "report_hash")
    repair = _repair_context(directory, cohort_id, dataset, cohort_report, derived_dataset_file)
    if dataset.get("quality", {}).get("passed") is not True and repair is None:
        raise ValueError("cohort quality has not passed the pre-fit gate")
    if _read(target / "source-isolation.json") != _check_sources(list(sources.values())):
        raise ValueError("source isolation receipt differs from sealed facts")
    if (dataset.get("teacher") != teacher_identity(cohort_id) or dataset.get("activation_allowed") is not False
            or dataset.get("training_started") is not False or cohort_report.get("dataset_hash") != dataset["dataset_hash"]
            or cohort_report.get("profiles") != 420 or cohort_report.get("families") != 60
            or cohort_report.get("split_counts") != SPLITS or cohort_report.get("activation_allowed") is not False
            or cohort_report.get("training_started") is not False or cohort_report.get("golden_model_predictions") != 0
            or cohort_report.get("historical_source_bytes_preserved") is not True):
        raise ValueError("sealed dataset/cohort report provenance mismatch")
    before, after = manifest["ledger_before"], cohort_report["ledger_after"]
    ledger, attempts = _read(directory / "provider-cache/usage.json"), _read(target / "attempt-budget.json")
    pilot = _pilot_gate(directory, target, cohort_id, dataset)
    base_charges = _base_charges(directory, target, cohort_id, attempts, pilot)
    concurrent = _concurrent_pilots(directory, manifest, sources, before, after, attempts, repair)
    current_namespace = pair_repair.VERSION if repair is None else repair[2].VERSION
    current_pilot = concurrent["pilots"].get(current_namespace)
    if current_pilot is not None and current_pilot["pilot_passed"] is not True:
        raise ValueError("current pair-repair pilot has not passed; historical failed pilots are accounting-only")
    if repair is None:
        current_target = directory / current_namespace
        if (current_target / "pilot-manifest.json").exists():
            current_report = current_target / "pilot-report.json"
            if not current_report.exists() or _verified(current_report, "report_hash").get("passed") is not True:
                raise ValueError("current pair-repair pilot has not passed before fitting")
            raise ValueError("current hybrid repair requires its verified derived dataset before fitting")
        previous_v2 = concurrent["pilots"].get("rerank-grounded-pair-repair-v2")
        if previous_v2 is not None and previous_v2["pilot_passed"] is not True:
            raise ValueError("failed v2 pilot requires a verified passed current hybrid repair before fitting")
    effective_after = after if repair is None else repair[1]["ledger_after"]
    if cohort_report.get("ledger_before") != before or cohort_report.get("attempts") != attempts or ledger != effective_after:
        raise ValueError("completed provider ledger/attempt receipt changed")
    for kind, limit in LIMITS.items():
        if any(type(row.get(kind)) is not int or not 0 <= row[kind] <= limit for row in (before, after, effective_after)):
            raise ValueError("provider cumulative global cap exceeded")
        if (after[kind] < before[kind] or type(attempts.get(kind)) is not int
                or not 0 <= attempts[kind] <= manifest["attempt_limits"][kind]):
            raise ValueError("cohort attempt accounting exceeds frozen allowance")
        expected_charges = base_charges["charged"][kind] + (concurrent["charged_pair_attempts"] if kind == "pair" else 0)
        if after[kind] - before[kind] != expected_charges:
            raise ValueError("base global ledger does not reconcile exact charged reservations and verified concurrent pilots")
        if effective_after[kind] < after[kind] or (kind != "pair" and effective_after[kind] != after[kind]):
            raise ValueError("derived repair changed unrelated provider accounting")
    repair_accounting = None
    if repair is not None:
        repair_report, adapter = repair[1], repair[2]
        closure = _verified(directory / adapter.VERSION / "closure-observation.json", "observation_hash")
        budget = repair_report["budget"]
        expected_additional = sum(budget.values())
        if (repair_report.get("pilot_closure_observation_hash") != closure["observation_hash"]
                or repair_report.get("ledger_before_pilot") != after
                or closure.get("base_dataset_existed") is not True or closure.get("base_completed_families") != 60
                or not 8 <= budget["pilot_initial"] + budget["pilot_retry"] <= 16
                or budget["pilot_initial"] != 8
                or effective_after["pair"] - after["pair"] != expected_additional):
            raise ValueError("derived global ledger does not reconcile exact post-base repair charges")
        repair_accounting = {"total_repair_reservations": sum(budget.values()), "pilot_charges_already_in_base": 0,
                             "additional_charged_pair_attempts": expected_additional,
                             "closure_observation_hash": closure["observation_hash"]}
    first = directory / "rerank-grounded-cohort-v1/manifest.json"
    historical = _read(first)["ledger_before"]["generation"] if first.exists() else before["generation"]
    bound, receipts = _spent_profiles(directory, after["generation"], historical)
    if (bound + manifest["manual_description_variants"] > 600
            or manifest["generation_spent_profile_bound"] > 600
            or manifest["generation_profile_receipts"] != receipts):
        raise ValueError("generation profile accounting exceeds sealed global budget")
    schema = feature_schema(initial_schema())
    provider = _CachedTeacher(directory / "provider-cache", cohort_id=cohort_id, generator_model=manifest["generator"])
    pools, runtime_pools = {}, {}
    lineages = {}
    for row in manifest["families"]:
        direction = row["direction"]
        for index, trait in enumerate(sources[row["family_id"]]["traits"]):
            profile = candidate_profile(trait, direction)
            value = digest(profile.model_dump(mode="json"))
            pools.setdefault(direction, []).append(value)
            runtime_pools.setdefault(direction, []).append(profile)
            lineages.setdefault(direction, {})[f'{row["family_id"]}/{index}'] = value
    if any(len(values) != len(set(values)) for values in pools.values()):
        raise ValueError("duplicate global class profile snapshots before fitting")
    families = []
    semantics, semantic_seed = _read(target / "semantic-cache.json"), _read(directory / "semantic-cache.json")
    swap_correct, quarantined = 0, []
    for family in dataset["families"]:
        item, traits = family["manifest"], family["traits"]
        identifier, direction = item["family_id"], item["direction"]
        if family != _verified(target / "families" / f"{identifier}.json", "record_hash"):
            raise ValueError("dataset family differs from sealed family file")
        _generation_source(directory, target, sources[identifier])
        feature = _verified(target / "features" / f"{identifier}.json", "hash")
        if (any(family.get(key) != value for key, value in feature.items())
                or feature.get("source_hash") != sources[identifier]["record_hash"]
                or feature.get("class_pool_hash") != digest(sorted(pools[direction]))):
            raise ValueError("training feature receipt differs from family/global class sources")
        provenance = feature["provenance"]
        if (provenance.get("namespace") != cohort_id or provenance.get("class_rank_scope") != "global_direction_pool"
                or provenance.get("class_source_lineage") != dict(sorted(lineages[direction].items()))
                or provenance.get("class_profile_snapshot_hashes") != sorted(pools[direction])
                or provenance.get("class_size") != len(pools[direction])
                or provenance.get("current_feature_schema_hash") != schema.schema_hash
                or provenance.get("current_policy_hash") != policy_hash(ScoringPolicy())):
            raise ValueError("current feature/schema/policy provenance mismatch")
        if family.get("teacher") != teacher_identity(cohort_id):
            raise ValueError("family teacher identity mismatch")
        vectors = [FeatureVector(tuple(row["names"]), tuple(row["values"]), tuple(row["available"]), row["schema_hash"])
                   for row in family["vectors"]]
        if len(vectors) != 7 or len(family["grades"]) != 7 or any(
            vector.values[vector.names.index("main_score")] != score for vector, score in zip(vectors, family["main_scores"], strict=True)
        ):
            raise ValueError("training candidate dimensions/main score mismatch")
        if sorted(family["profile_order"]) != list(range(7)):
            raise ValueError("training profile order is not a permutation")
        actual_vectors, actual_scores, actual_order = _current_features(target, item, traits, runtime_pools[direction],
                                                                      semantics, semantic_seed, provenance)
        if vectors != actual_vectors or family["main_scores"] != actual_scores or family["profile_order"] != actual_order:
            raise ValueError("sealed training features differ from current cache-only runtime calculation")
        pairs = family["pairs"]
        indices = list(itertools.combinations(range(7), 2))
        if [(a, b) for a, b, _ in pairs] != indices or len(family["pair_answers"]) != 21:
            raise ValueError("independent training pairs/order mismatch")
        for (a, b, label), answer in zip(pairs, family["pair_answers"], strict=True):
            if answer["label"] != label:
                raise ValueError("pair labels differ from citation-grounded answers")
            _answer(directory, target, provider, f"{identifier}-pair-{a}-{b}", "pair", answer,
                    lambda attempt, a=a, b=b, item=item, traits=traits: provider.judge(item["vacancy"], traits[a], traits[b], item["policy"], attempt))
        audit_pairs = [indices[index] for index in (0, 7, 14)]
        if len(family["swap_answers"]) != 3 or len(family["swap_audits"]) != 3 or len(family["suitability_answers"]) != 7:
            raise ValueError("swap/suitability dimensions mismatch")
        audits = []
        reverse = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}
        for (a, b), answer in zip(audit_pairs, family["swap_answers"], strict=True):
            _answer(directory, target, provider, f"{identifier}-pair-{b}-{a}", "pair", answer,
                    lambda attempt, a=a, b=b, item=item, traits=traits: provider.judge(item["vacancy"], traits[b], traits[a], item["policy"], attempt))
            audits.append(answer["label"] == reverse[pairs[indices.index((a, b))][2]])
        if family["swap_audits"] != audits or family.get("quarantined") != (sum(audits) < 2):
            raise ValueError("swap quality receipt differs from cached evidence")
        swap_correct += sum(audits)
        if family["quarantined"]:
            quarantined.append(identifier)
        for index, answer in enumerate(family["suitability_answers"]):
            if answer["grade"] != family["grades"][index]:
                raise ValueError("independent suitability differs from cached answer")
            _answer(directory, target, provider, f"{identifier}-grade-{index}", "grade", answer,
                    lambda attempt, index=index, item=item, traits=traits: provider.suitability(item["vacancy"], traits[index], item["policy"], attempt))
        families.append(TrainingFamily(identifier, direction, item["split"], vectors, family["main_scores"],
                                       family["grades"], [tuple(pair) for pair in pairs], item["source_id"], tuple(family["slice_tags"])))
    validate_families(families, schema)
    if (Counter(family.split for family in families) != Counter(SPLITS)
            or sorted([{key: getattr(family, key) for key in ("family_id", "source_id", "direction", "split")}
                       for family in families], key=lambda row: row["family_id"]) != sorted(expected_families(), key=lambda row: row["family_id"])):
        raise ValueError("test fit source allocation differs from frozen 32/8/8/12 protocol")
    quality = {"minimum_golden_decisive_pairs": min(sum(label in ("A", "B") for _, _, label in family.pairs)
                for family in families if family.split == "golden"), "swap_correct": swap_correct,
               "swap_total": 180, "quarantined_families": quarantined}
    quality["passed"] = swap_correct >= 162 and not quarantined and quality["minimum_golden_decisive_pairs"] >= 6
    if (dataset.get("quality") != quality or cohort_report.get("quality") != quality
            or (not quality["passed"] and repair is None)):
        raise ValueError("cohort quality has not passed the verified pre-fit gate")
    if manifest["manual_description_variants"]:
        audit = _verified(target / "description-repair-report.json", "report_hash")
        if dataset.get("description_audit") != audit or audit.get("status") != "passed" or audit.get("source_profiles") != 420:
            raise ValueError("pre-label description drift audit missing or changed")
    base_families, base_quality = families, quality
    review_dataset, repair_pins = dataset, None
    review_directory, expected_packet = None, None
    if repair is not None:
        review_dataset, repair_report, adapter = repair
        repaired = {family["manifest"]["family_id"]: family for family in review_dataset["families"]}
        families = [replace(family, pairs=[tuple(pair) for pair in repaired[family.family_id]["pairs"]])
                    for family in families]
        validate_families(families, schema)
        quality = review_dataset["quality"]
        review_directory = directory / adapter.VERSION / "reviews"
        expected_packet = _repair_packet(cohort_id, manifest, sources, repair_report, adapter)
        repair_pins = {"namespace": adapter.VERSION, "base_dataset_hash": dataset["dataset_hash"],
                       "dataset_hash": review_dataset["dataset_hash"], "report_hash": repair_report["report_hash"],
                       "manifest_hash": repair_report["manifest_hash"], "pilot_hash": repair_report["pilot_hash"],
                       "pilot_manifest_hash": repair_report["pilot_manifest_hash"],
                       "pilot_closure_observation_hash": repair_report.get("pilot_closure_observation_hash"),
                       "policy_revision": expected_packet["policy_revision"],
                       "selected_families": repair_report["selected_families"], "budget": repair_report["budget"],
                       "ledger_before": repair_report["ledger_before"], "ledger_after": repair_report["ledger_after"],
                       "code_hash": digest(Path(adapter.__file__).read_bytes().hex()),
                       "base_quality": base_quality, "effective_quality": quality,
                       "charge_reconciliation": repair_accounting,
                       "source_metrics": review_dataset["source_metrics"],
                       "oracle_code_hash": review_dataset["oracle_code_hash"],
                       "new_policy_review_required": True, "original_blind_nomination_is_evaluation": False}
    assistant = _review_gate(directory, cohort_id, manifest, sources, review_dataset, assistant_review_file,
                             adjudication_file, review_directory=review_directory, expected_packet=expected_packet)
    if repair is not None:
        comparison = _verified(review_directory / f'assistant-comparison-{assistant["review_hash"]}.json', "report_hash")
        if (comparison.get("pair_repair_report_hash") != repair_pins["report_hash"]
                or comparison.get("base_packet_hash") != expected_packet["base_packet_hash"]
                or comparison.get("policy_revision") != expected_packet["policy_revision"]
                or comparison.get("review_metadata_rebind_hash") != (None if assistant["review_metadata_rebind"] is None
                    else assistant["review_metadata_rebind"]["receipt_hash"])):
            raise ValueError("derived assistant comparison lacks exact repair and policy provenance")
    replacements = {(row["family_id"], row["a_index"], row["b_index"]): row["derived_label"]
                    for row in assistant["corrections"]}
    derived = [replace(family, pairs=[(a, b, replacements.get((family.family_id, a, b), label))
                                      for a, b, label in family.pairs]) for family in families]
    validate_families(derived, schema)
    adapter_code = {name: digest((Path(__file__).parent / name).read_bytes().hex()) for name in (
        "rerank_cohort_training.py", "rerank_cohort_review.py", "rerank_training.py", "rerank_model.py")}
    pinned = {"cohort_id": cohort_id, "manifest_hash": manifest["manifest_hash"], "dataset_hash": review_dataset["dataset_hash"],
              "base_dataset_hash": dataset["dataset_hash"], "base_teacher_quality": base_quality,
              "cohort_report_hash": cohort_report["report_hash"], "pilot_hash": pilot["report_hash"],
              "quality": quality, "assistant_gate": assistant, "ledger_hash": digest(ledger), "attempts_hash": digest(attempts),
              "generation_profile_upper_bound": bound + manifest["manual_description_variants"],
              "source_hashes": {key: source["record_hash"] for key, source in sorted(sources.items())},
              "feature_hashes": {family["manifest"]["family_id"]: family["hash"] for family in dataset["families"]},
              "frozen_code_hashes": manifest["code_hashes"], "adapter_code_hashes": adapter_code,
              "feature_schema_hash": schema.schema_hash, "current_policy_hash": policy_hash(ScoringPolicy())}
    pinned["pair_repair"] = repair_pins
    pinned["label_provenance"] = _label_provenance(directory, review_dataset, repair)
    pinned["label_provenance"]["family_source_interpretation"] = (
        "Raw effective family label origin before separately pinned blind-review adjudication corrections")
    pinned["label_provenance"]["manual_correction_pair_ids"] = [row["pair_id"] for row in assistant["corrections"]]
    pinned["base_reservation_accounting"] = base_charges
    pinned["concurrent_pair_attempts"] = concurrent
    effective_rows = {family["manifest"]["family_id"]: family for family in review_dataset["families"]}
    scopes, pair_policies, suitability_policies = {}, {}, {}
    for identifier, family in effective_rows.items():
        revision = family.get("pair_repair")
        scopes[identifier] = revision["policy_scope"] if revision else (
            "original_class_only_preserved" if not any(family["vacancy"][key] for key in ("domens", "stacks", "soft_skills"))
            else "original_nonclass")
        pair_policies[identifier] = digest(revision["pair_policy"] if revision else family["manifest"]["policy"])
        suitability_policies[identifier] = digest(family["manifest"]["policy"])
    pinned["policy_evaluation"] = {
        "pair_policy_hashes": pair_policies, "suitability_policy_hashes": suitability_policies,
        "pair_policy_scope_by_family": scopes, "suitability_teacher": dataset["teacher"],
        "suitability_policy_original": True, "independent_suitability_regraded": False,
        "families_with_different_pair_and_suitability_policy": sorted(
            identifier for identifier in pair_policies if pair_policies[identifier] != suitability_policies[identifier]),
        "ndcg_interpretation": "Original independently labeled suitability under original per-family policy",
        "pair_accuracy_interpretation": "Effective pair policy with separately recorded blind-review adjudication corrections",
        "policy_scope_metrics_used_for_tuning": False,
    }
    pinned["base_teacher_label_hash"] = digest([{ "id": family.family_id, "pairs": family.pairs, "grades": family.grades}
                                                for family in base_families])
    pinned["current_features_recomputed_cache_only"] = True
    pinned["derived_training_dataset_hash"] = digest([asdict(family) for family in derived])
    pinned["raw_teacher_label_hash"] = digest([{ "id": family.family_id, "pairs": family.pairs, "grades": family.grades}
                                               for family in families])
    pinned["derived_label_hash"] = digest([{ "id": family.family_id, "pairs": family.pairs, "grades": family.grades}
                                           for family in derived])
    if _read(directory / "provider-cache/usage.json") != ledger:
        raise ValueError("provider ledger changed during offline validation")
    return derived, pinned


def _seal(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(value, output, sort_keys=True, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read(path) != value:
                raise ValueError("immutable test-only training output already differs") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def run_test_only_training(directory: Path, cohort_id: str = DEFAULT_COHORT, *,
                           assistant_review_file: Path, adjudication_file: Path | None = None,
                           derived_dataset_file: Path | None = None) -> dict:
    """Execute one validated fixed-grid test fit, or replay its immutable completed report.

    A started request without a completed receipt always blocks automatic refitting,
    including after failure/crash. User20 is not required for this isolated test;
    activation stays false and the existing trainer's actual protocol/metrics remain
    unchanged. The optional derived_dataset_file must identify the fixed completed
    pair-repair namespace, with its passed quality and new policy-corrected assistant
    review; original sources/features/grades and raw receipts are still verified.
    No provider inference, credentials, API or runtime deployment occurs.
    """
    directory = Path(directory)
    if tuple(C_GRID) != (.01, .1, 1., 10.) or SEED != 42:
        raise ValueError("test fit requires the frozen C grid and seed")
    if derived_dataset_file is not None:
        derived_dataset_file = Path(derived_dataset_file)
        adapter = _registered_repair(directory, cohort_id, derived_dataset_file)
    namespace = cohort_id if derived_dataset_file is None else adapter.VERSION
    target = review._target(directory, namespace) / "test-only-training"
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        families, pinned = _validated(directory, cohort_id, Path(assistant_review_file), adjudication_file, derived_dataset_file)
        identity = {"version": ADAPTER_VERSION, "pinned": pinned, "C_grid": list(C_GRID), "seed": SEED,
                    "scaling": "train-observed-mean-plus-availability:v1", "fit_runs": 1,
                    "require_protocol": False, "test_only": True, "activation_allowed": False}
        corrections = {"version": ADAPTER_VERSION, "raw_dataset_hash": pinned["dataset_hash"],
                       "derived_training_dataset_hash": pinned["derived_training_dataset_hash"],
                       "raw_label_hash": pinned["raw_teacher_label_hash"], "derived_label_hash": pinned["derived_label_hash"],
                       "assistant_gate": pinned["assistant_gate"], "raw_teacher_quality": pinned["base_teacher_quality"],
                       "effective_teacher_quality": pinned["quality"], "pair_repair": pinned["pair_repair"],
                       "policy_evaluation": pinned["policy_evaluation"],
                       "independent_suitability_unchanged": True, "raw_dataset_unchanged": True,
                       "derived_pair_corrections": len(pinned["assistant_gate"]["corrections"]),
                       "original_reverse_audits_validate_derived_corrected_pairs": False,
                       "golden_corrections_preselected_blind_before_predictions": True, "activation_allowed": False}
        corrections["correction_hash"] = digest(corrections)
        request_path, completed_path = target / "train-request.json", target / "completed.json"
        if request_path.exists():
            request = _verified(request_path, "request_hash")
            if request.get("identity") != identity or request.get("state") != "started":
                raise ValueError("immutable training request input identity changed")
            if not completed_path.exists():
                raise RuntimeError("test fit started but did not complete; manual crash review required, automatic refit forbidden")
            completed = _verified(completed_path, "completed_hash")
            artifact = validate_artifact(_read(target / "artifact.json"))
            report = _verified(target / "report.json", "report_hash")
            if _verified(target / "corrections.json", "correction_hash") != corrections:
                raise ValueError("derived correction receipt changed during replay")
            if (completed.get("request_hash") != request["request_hash"]
                    or completed.get("artifact_hash") != artifact["content_hash"]
                    or completed.get("report_hash") != report["report_hash"]
                    or report.get("pinned") != pinned or report.get("activation_allowed") is not False
                    or artifact["training"].get("test_only") is not True or promotion_eligible(artifact)):
                raise ValueError("completed test-only artifact/report replay integrity mismatch")
            return report
        if any((target / name).exists() for name in ("artifact.json", "report.json", "completed.json", "corrections.json")):
            raise ValueError("orphaned training output exists without a started receipt")
        request = {"identity": identity, "state": "started", "started_at": datetime.now(UTC).isoformat()}
        request["request_hash"] = digest(request)
        _seal(request_path, request)
        _seal(target / "corrections.json", corrections)
        validation = {"passed": True, "quality": pinned["quality"], "assistant_gate": pinned["assistant_gate"]}
        schema = feature_schema(initial_schema())
        artifact = train(families, schema, {"require_protocol": False, "model_version": f"{namespace}-test-linear-r0-v1",
            "dataset_hash": pinned["derived_training_dataset_hash"], "label_cache_hash": digest([{ "id": family.family_id,
                "pairs": family.pairs, "grades": family.grades} for family in families]),
            "label_validation": validation, "label_validation_hash": digest(validation), "tuning_cycles": 0})
        if artifact["training"].get("test_only") is not True or artifact["gate"]["passed"] is not False or promotion_eligible(artifact):
            raise ValueError("test-only trainer returned an activation-eligible artifact")
        portable = RerankArtifact.model_validate(artifact)
        calibration = [family for family in families if family.split == "calibration"]
        fixed_scores = {family.family_id: [sum(value * coefficient for value, coefficient in zip(
            scaled_features(vector, portable), portable.coefficients, strict=True)) for vector in family.vectors]
            for family in families}
        scope_metrics = {}
        scope_map = pinned["policy_evaluation"]["pair_policy_scope_by_family"]
        for scope in sorted(set(scope_map.values())):
            scope_metrics[scope] = {split: evaluate([family for family in families
                if family.split == split and scope_map[family.family_id] == scope], fixed_scores)
                for split in ("validation", "calibration", "golden")}
        source_map = pinned["label_provenance"]["by_family"]
        source_metrics = {source: {split: evaluate([family for family in families
            if family.split == split and source_map[family.family_id] == source], fixed_scores)
            for split in SPLITS} for source in sorted(set(source_map.values()))}
        report = {"version": ADAPTER_VERSION, "cohort_id": cohort_id, "namespace": namespace, "state": "completed_test_only",
                  "request_hash": request["request_hash"], "pinned": pinned, "artifact_hash": artifact["content_hash"],
                  "test_only": True, "activation_allowed": False, "activation_eligible": False,
                  "independent_human_evaluation": False, "source_allocation_validated": True,
                  "protocol_valid": artifact["gate"]["protocol_valid"], "fit_runs": 1,
                  "raw_teacher_quality": pinned["base_teacher_quality"], "effective_teacher_quality": pinned["quality"],
                  "effective_label_quality": pinned["quality"],
                  "effective_quality_has_mixed_label_sources": pinned["pair_repair"] is not None,
                  "label_provenance": pinned["label_provenance"], "label_source_metrics": source_metrics,
                  "pair_repair": pinned["pair_repair"], "correction_receipt_hash": corrections["correction_hash"],
                  "concurrent_pair_attempts": pinned["concurrent_pair_attempts"],
                  "policy_evaluation": pinned["policy_evaluation"], "policy_scope_metrics": scope_metrics,
                  "derived_pair_corrections": corrections["derived_pair_corrections"],
                  "metrics": {"validation": artifact["training"]["report"]["validation"],
                              "calibration": evaluate(calibration, fixed_scores),
                              "golden": artifact["training"]["report"]["golden"],
                              "tail": artifact["training"]["report"]["tail"]},
                  "C_grid": list(C_GRID), "selected_C": artifact["training"]["C"],
                  "scaling": artifact["training"]["scaling"], "families_hash": digest([asdict(family) for family in families])}
        report["report_hash"] = digest(report)
        _seal(target / "artifact.json", artifact)
        _seal(target / "report.json", report)
        completed = {"state": "completed", "request_hash": request["request_hash"],
                     "artifact_hash": artifact["content_hash"], "report_hash": report["report_hash"]}
        completed["completed_hash"] = digest(completed)
        _seal(completed_path, completed)
        return report


def main() -> None:
    """Run the explicitly requested isolated fit after all offline gates pass."""
    parser = argparse.ArgumentParser(description="One sealed, non-activatable grounded cohort test fit")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--cohort-id", default=DEFAULT_COHORT)
    parser.add_argument("--assistant-review-file", type=Path, required=True)
    parser.add_argument("--adjudication-file", type=Path)
    parser.add_argument("--derived-dataset-file", type=Path,
                        help="Fixed completed pair-repair dataset; requires its new policy-corrected assistant20 review")
    parser.add_argument("--compare-only", action="store_true", help="Seal the current assistant comparison without fitting")
    args = parser.parse_args()
    if args.compare_only:
        result = compare_test_only_review(args.directory, args.assistant_review_file, args.cohort_id,
                                         derived_dataset_file=args.derived_dataset_file)
        fields = ("reviewed_pairs", "agreements", "disagreements", "activation_allowed", "report_hash")
    else:
        result = run_test_only_training(args.directory, args.cohort_id,
                                       assistant_review_file=args.assistant_review_file, adjudication_file=args.adjudication_file,
                                       derived_dataset_file=args.derived_dataset_file)
        fields = ("state", "test_only", "activation_allowed", "report_hash")
    print(json.dumps({key: result[key] for key in fields}, ensure_ascii=False))


if __name__ == "__main__":
    main()
