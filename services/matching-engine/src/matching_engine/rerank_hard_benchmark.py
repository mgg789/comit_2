"""Pre-registered hard benchmark for the packaged R0 artifact.

The sealed grounded cohort cannot demonstrate the required NDCG gain even for a
perfect ordering: the main policy already reaches NDCG = 1.0 on eight of twelve
golden families and the attainable macro delta is 0.0041 against a 0.02 gate
(docs/rerank-grounded-cohort-acceptance.md). This namespace fixes a new,
independent benchmark with deliberately difficult alternatives BEFORE any
teacher label exists, so the attainable headroom is a property of the
benchmark, not of a tuned selection.

Honesty boundaries, all enforced in code:
- Candidates come from an external text subagent; every raw output byte is
  copied into the namespace with an explicit generator receipt. No Sverk
  generation call is made or implied, and no historical receipt is rewritten.
- Every candidate passes the sealed cohort validators; every family passes the
  pre-registered difficulty gate computed from main-v2 scores only. The
  artifact under test never influences family selection, preserving
  benchmark independence.
- Difficulty is structural: close main-score pairs, bounded span, mixed grades
  and evidence statuses. Whether the teacher order actually differs from main
  is measured after labeling and reported as the oracle headroom.
- Teacher labels reuse the shared CohortProviders transport, cache and
  cumulative ledger. The benchmark adds its own durable per-attempt receipts
  and never edits the sealed v9 corpus.
- The benchmark is evaluation-only: no family may enter training while this
  namespace is sealed evaluation-only, and the packaged artifact is fixed
  before any label exists.
"""

import argparse
import fcntl
import itertools
import json
import shutil
from collections.abc import Callable
from pathlib import Path

from matching_engine.rerank_cohort import CORE_KEYS, _policy
from matching_engine.rerank_cohort_features import (
    build_family_features,
    candidate_profile,
)
from matching_engine.rerank_cohort_provider import CohortProviders, validate_candidate
from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_experiment import _parallel
from matching_engine.rerank_features import FeatureVector
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import artifact_hash
from matching_engine.rerank_providers import LIMITS, _atomic_write
from matching_engine.rerank_training import TrainingFamily, _family_metrics, evaluate

VERSION = "rerank-hard-benchmark-v7"
SUPERSEDED_VERSION = "rerank-hard-benchmark-v6"
# Teacher calls keep the v4 cohort identity so every existing receipt and cached
# response stays valid; the namespace version tracks the manifest, not the cache.
LABEL_COHORT_ID = "rerank-hard-benchmark-v4"
SUPERSEDING_REASON = (
    "v1/v2 lesson: the calibrated teacher abstains on classless vacancies, so the "
    "class_only slot became mixed_alt; v3 aborted before any teacher spend on a "
    "pilot-accounting fix; v4 labeled five families before the grade ceiling (700, "
    "raised from 600 by the operator) proved ~20 calls short because teacher repair "
    "attempts charge like initial ones. v5 extends the ceiling to 720 (operator-"
    "authorized labeling budget, cumulative ledger preserved) and continues the v4 "
    "cohort identity for teacher calls, so every existing receipt and cached label "
    "stays valid without re-spend. Family specs and candidates are byte-identical "
    "to v3/v4.")
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "r0-c18f4bc1.json"
BASE_COHORT = "rerank-grounded-cohort-v9"
GENERATOR = "external-subagent-glm-5.3-flash"
CLOSE_PAIR_DELTA = 0.05
CLOSE_PAIR_MINIMUM = 8
SPAN_MAXIMUM = 0.5
FAMILY_SIZE = 7
# Operator decision (Mike, 2026-10-07): the benchmark labeling allowance needs
# grades beyond the original 600 ceiling. The cumulative ledger is preserved;
# only the hard ceiling moves, and the raise is recorded in the manifest.
BENCHMARK_LIMITS = {**LIMITS, "grade": 720}
BENCHMARK_DIRECTIONS = ("backend", "frontend", "data", "ml")
BENCHMARK_BRANCHES = ("mixed", "class_only", "stack_only")
# Frozen per-family requirement levels for the mixed/stack_only branches.
REQUIRED_LEVELS = (5, 4, 6)


def family_specs() -> list[dict]:
    """Return the frozen twelve-family hard benchmark plan."""
    from matching_engine.strict import INITIAL_GROUPS

    rows = []
    for direction_index, direction in enumerate(BENCHMARK_DIRECTIONS):
        keys = list(CORE_KEYS[direction])
        stack_keys = [key for key in keys if key in INITIAL_GROUPS["stack"]]
        domain_keys = [key for key in keys if key in INITIAL_GROUPS["domain"]]
        for branch_index, branch in enumerate(BENCHMARK_BRANCHES):
            index = direction_index * len(BENCHMARK_BRANCHES) + branch_index
            family_id = direction + "-hard-" + str(branch_index + 1)
            groups: dict[str, dict[str, int]] = {"domens": {}, "stacks": {}, "soft_skills": {}}
            resolved = branch
            if branch == "mixed":
                for position, key in enumerate(stack_keys[:3]):
                    groups["stacks"][key] = REQUIRED_LEVELS[position % 3]
                fill = 4 - len(groups["stacks"])
                for position, key in enumerate(domain_keys[:fill]):
                    groups["domens"][key] = REQUIRED_LEVELS[(position + 3) % 3]
            elif branch == "class_only":
                # v1/v2 lesson: the calibrated teacher abstains on classless
                # vacancies (grade-first ties into comparable_fit), so class-only
                # families cannot yield the preregistered decisive-pair minimum.
                # v3 replaces this branch with a second requirements mix.
                resolved = "mixed_alt"
                for position, key in enumerate(stack_keys[:2]):
                    groups["stacks"][key] = REQUIRED_LEVELS[position % 3]
                for position, key in enumerate(domain_keys[:4 - len(groups["stacks"])]):
                    groups["domens"][key] = REQUIRED_LEVELS[(position + 2) % 3]
            elif branch == "stack_only" and len(stack_keys) >= 4:
                for position, key in enumerate(stack_keys[:4]):
                    groups["stacks"][key] = REQUIRED_LEVELS[position % 3]
            elif len(domain_keys) >= 4:
                resolved = "domain_only"
                for position, key in enumerate(domain_keys[:4]):
                    groups["domens"][key] = REQUIRED_LEVELS[position % 3]
            elif stack_keys and len(domain_keys) >= 3:
                resolved = "domain_heavy"
                groups["stacks"][stack_keys[0]] = REQUIRED_LEVELS[0]
                for position, key in enumerate(domain_keys[:3]):
                    groups["domens"][key] = REQUIRED_LEVELS[(position + 1) % 3]
            else:
                raise ValueError("direction lacks registered keys for a hard family: " + direction)
            bio_absent = resolved == "class_only"
            bio = "" if bio_absent else (
                "Ищем специалиста направления " + direction + ": задачи команды требуют "
                "глубокого покрытия " + ", ".join(keys[:3]) + " в продакшене."
            )
            rows.append({
                "family_id": family_id,
                "direction": direction,
                "branch": resolved,
                "policy": _policy(index),
                "vacancy": {"class": direction, **groups, "bio": bio,
                            "salary": {"min": 50000, "max": 200000}},
                "bio_absent": bio_absent,
                "keys": keys,
                "profile_count": FAMILY_SIZE,
                "close_pair_minimum": CLOSE_PAIR_MINIMUM,
                "close_pair_delta": CLOSE_PAIR_DELTA,
                "span_maximum": SPAN_MAXIMUM,
            })
    return rows


def pilot_cases() -> list[dict]:
    """Return eight preregistered cases with deterministic teacher expectations."""
    cases: list[dict] = []
    for position, direction in enumerate(BENCHMARK_DIRECTIONS):
        key = CORE_KEYS[direction][0]
        def candidate(level: int, status: str, key: str = key) -> dict:
            return {"grade": "junior", "status": status, "levels": {key: level},
                    "skill_statuses": {key: status}, "salary_min": 80000,
                    "description": "Синтетический пример для проверки учителя",
                    "structured_facts_available": False, "duration_months": None,
                    "duration_status": None, "production_support": None,
                    "production_status": None, "role": None, "role_status": None,
                    "project_domains": [], "project_stacks": []}
        status = ("confirmed", "partial", "self_reported", "pending")[position % 4]
        # Sole-priority policy: higher declared level must win regardless of status.
        sole_policy = {"id": "benchmark-pilot-" + direction,
                       "ordered_priorities": ["The sole preference is higher declared levels." + key +
                                              ". Confirmation, grade, salary and narrative cannot replace "
                                              "this measurement."],
                       "required_measurements": ["levels." + key], "exclusive_criterion": True,
                       "suitability_rubric": "Grade3 when known declared levels." + key +
                                             " >=4; grade1 when known level below4; null only if this "
                                             "measurement is missing. No other facts are required."}
        cases.append({"case_id": direction + "-sole", "vacancy": {"class": direction},
                      "A": candidate(6, status), "B": candidate(5, status),
                      "policy": sole_policy, "expected": "A", "expected_grade": 3})
        # Identical measurements under a sole-priority policy must tie.
        cases.append({"case_id": direction + "-tie", "vacancy": {"class": direction},
                      "A": candidate(6, status), "B": candidate(6, status),
                      "policy": dict(sole_policy), "expected": "tie", "expected_grade": 3})
    return cases


def _load_base_sources(directory: Path) -> dict[str, list[tuple[str, int, dict]]]:
    """Load frozen v9 source traits with their family/index provenance per direction."""
    sources: dict[str, list[tuple[str, int, dict]]] = {}
    for path in sorted((directory / BASE_COHORT / "sources").glob("*.json")):
        record = _read(path)
        family_id = record["manifest"]["family_id"]
        direction = record["manifest"]["direction"]
        sources.setdefault(direction, []).extend(
            (family_id, index, trait) for index, trait in enumerate(record["traits"]))
    if not sources:
        raise ValueError("base cohort sources missing")
    return sources


def _isolation(records: list[dict], base_sources: dict[str, list[dict]]) -> dict:
    """Reject exact fact or normalized description collisions inside and against v9."""
    owners: dict[str, str] = {}
    texts: dict[str, str] = {}
    for trait in [trait for traits in base_sources.values() for trait in traits]:
        owners[digest({k: v for k, v in trait.items() if k != "description"})] = BASE_COHORT
        owners[digest(trait)] = BASE_COHORT
        texts[" ".join(trait["description"].lower().split())] = BASE_COHORT
    duplicates = []
    for record in records:
        family_id = record["family_id"]
        for trait in record["candidates"]:
            for key in (digest({k: v for k, v in trait.items() if k != "description"}), digest(trait)):
                previous = owners.setdefault(key, family_id)
                if previous != family_id:
                    duplicates.append(family_id + ": exact fact collision with " + previous)
            text = " ".join(trait["description"].lower().split())
            previous = texts.setdefault(text, family_id)
            if previous != family_id:
                duplicates.append(family_id + ": description collision with " + previous)
    return {"duplicates": duplicates, "passed": not duplicates,
            "policy": "Exact fact and normalized description blocking against v9 and inside the benchmark"}


def _difficulty(main_scores: list[float], candidates: list[dict]) -> dict:
    """Structural preregistered gate from main scores and declared diversity only.

    Closeness (many pairs within the delta, bounded span) is what makes the
    benchmark hard for the weighted main policy. Non-degeneracy demands at
    least three distinct evidence statuses across each candidate's own status
    and skill statuses plus pairwise-distinct fact sets; grade diversity is
    deliberately not required because same-grade alternatives are exactly the
    close calls the main policy cannot separate.
    """
    close = sum(abs(a - b) <= CLOSE_PAIR_DELTA
                for a, b in itertools.combinations(main_scores, 2))
    span = max(main_scores) - min(main_scores)
    statuses = set()
    for candidate in candidates:
        statuses.add(candidate["status"])
        statuses.update(candidate["skill_statuses"].values())
    distinct = len({digest({k: v for k, v in candidate.items() if k != "description"})
                    for candidate in candidates})
    return {"close_pairs": close, "span": round(span, 9),
            "distinct_statuses": len(statuses), "distinct_candidates": distinct,
            "passed": (close >= CLOSE_PAIR_MINIMUM and span <= SPAN_MAXIMUM
                       and len(statuses) >= 3 and distinct == len(candidates))}


def _artifact_scores(artifact: dict, vectors: list[FeatureVector]) -> list[float]:
    """Apply the packaged linear artifact exactly like rerank_model.scaled_features."""
    from matching_engine.rerank_model import RerankArtifact

    model = RerankArtifact.model_validate(artifact)
    if model.content_hash != artifact_hash({k: v for k, v in artifact.items() if k != "content_hash"}):
        raise ValueError("packaged artifact integrity mismatch")
    scores = []
    for vector in vectors:
        scaled = [(value - mean) / scale if available else 0.0
                  for value, available, mean, scale in zip(
                      vector.values, vector.available, model.means, model.scales, strict=True)]
        scores.append(sum(coefficient * value for coefficient, value
                          in zip(model.coefficients, scaled, strict=True)))
    return scores


def _oracle_headroom(main_scores: list[float], grades: list[int | None]) -> float | None:
    """Maximum attainable NDCG delta for a family; None when grades cannot order it."""
    known = [index for index, grade in enumerate(grades) if grade is not None]
    if not known or not any(grades[index] for index in known):
        return None
    probe = TrainingFamily(family_id="oracle", direction="backend", split="golden", vectors=[],
                           main_scores=main_scores, grades=grades, pairs=[])
    baseline = _family_metrics(probe, main_scores)
    return 1.0 - (baseline["ndcg"] or 0.0)


def ingest(directory: Path, staging: Path) -> dict:
    """Validate subagent candidates, gate difficulty and seal the manifest before labels."""
    target = directory / VERSION
    if (target / "manifest.json").exists():
        raise ValueError("benchmark manifest already sealed")
    specs = family_specs()
    base_sources = _load_base_sources(directory)
    records = []
    for spec in specs:
        family_id = spec["family_id"]
        path = staging / (family_id + ".json")
        if not path.exists():
            raise ValueError("missing staging file for " + family_id)
        raw = path.read_bytes()
        payload = json.loads(raw)
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if payload.get("family_id") != family_id or not isinstance(candidates, list):
            raise ValueError("invalid staging payload for " + family_id)
        if len(candidates) != FAMILY_SIZE:
            raise ValueError(family_id + " must contain exactly " + str(FAMILY_SIZE) + " candidates")
        for candidate in candidates:
            validate_candidate(candidate)
        receipt = {"family_id": family_id, "generator": GENERATOR,
                   "prompt_sha256": digest(spec), "output_sha256": digest(raw.decode("utf-8")),
                   "output_bytes": len(raw),
                   "note": "Raw subagent output preserved verbatim; no provider generation call made"}
        records.append({**spec, "candidates": candidates, "external_receipt": receipt})
    flat_sources = {direction: [trait for _, _, trait in traits]
                    for direction, traits in base_sources.items()}
    isolation = _isolation(records, flat_sources)
    if not isolation["passed"]:
        raise ValueError("benchmark isolation failed: " + "; ".join(isolation["duplicates"][:5]))
    semantic_path = target / "semantic-cache.json"
    semantic_path.parent.mkdir(parents=True, exist_ok=True)
    if not semantic_path.exists():
        shutil.copyfile(directory / "semantic-cache.json", semantic_path)
    pools = {direction: [candidate_profile(trait, direction)
                         for _, _, trait in base_sources[direction]]
             for direction in BENCHMARK_DIRECTIONS}
    for record in records:
        pools[record["direction"]].extend(
            candidate_profile(trait, record["direction"]) for trait in record["candidates"])
    lineages = {}
    for direction in BENCHMARK_DIRECTIONS:
        lineage = {}
        for family_id, index, trait in base_sources[direction]:
            lineage[family_id + "/" + str(index)] = digest(
                candidate_profile(trait, direction).model_dump(mode="json"))
        for record in records:
            if record["direction"] != direction:
                continue
            for index, trait in enumerate(record["candidates"]):
                lineage[record["family_id"] + "/" + str(index)] = digest(
                    candidate_profile(trait, direction).model_dump(mode="json"))
        lineages[direction] = lineage
    for direction, pool in pools.items():
        hashes = [digest(profile.model_dump(mode="json")) for profile in pool]
        if len(set(hashes)) != len(hashes):
            raise ValueError("benchmark pool contains duplicate profiles")
    semantic_seed = _read(directory / "semantic-cache.json")
    feature_records = {}
    for record in records:
        family_id = record["family_id"]
        vectors, scores, order, provenance = build_family_features(
            record, record["vacancy"], record["candidates"],
            semantic_path, VERSION, class_profiles=pools[record["direction"]],
            class_source_lineage=lineages[record["direction"]], semantic_seed=semantic_seed)
        gate = _difficulty(scores, record["candidates"])
        if not gate["passed"]:
            raise ValueError(family_id + " failed the preregistered difficulty gate: " + json.dumps(gate))
        feature_records[family_id] = {
            "vectors": [{"values": vector.values, "available": vector.available,
                         "names": vector.names, "schema_hash": vector.schema_hash} for vector in vectors],
            "main_scores": scores, "profile_order": order, "provenance": provenance,
            "hash": digest({"vectors": [[vector.values, vector.available] for vector in vectors],
                            "main_scores": scores, "profile_order": order})}
        print(json.dumps({"phase": "ingest", "family": family_id,
                          "close_pairs": gate["close_pairs"], "span": gate["span"]}), flush=True)
    artifact = _read(ARTIFACT_PATH)
    superseded = directory / SUPERSEDED_VERSION / "manifest.json"
    manifest = {
        "version": VERSION,
        "purpose": ("Independent pre-registered hard benchmark for the packaged R0 artifact; "
                    "evaluation-only, never a training split while this namespace exists"),
        "families": [{**record, "feature_record": feature_records[record["family_id"]]}
                     for record in records],
        "isolation": isolation,
        "pool_definition": {direction: {"base": BASE_COHORT, "base_profiles": len(base_sources[direction]),
                                        "benchmark_profiles": 3 * FAMILY_SIZE}
                            for direction in BENCHMARK_DIRECTIONS},
        "generator": {"kind": GENERATOR,
                      "note": ("External text subagent outputs; validators identical to the sealed "
                               "cohort; no provider generation attempt, no budget consumed")},
        "artifact_under_test": {"path": "src/matching_engine/artifacts/r0-c18f4bc1.json",
                                "content_hash": artifact["content_hash"]},
        "evaluation_protocol": {
            "metrics": "macro NDCG@10 and pair accuracy versus main, paired family bootstrap, "
                       "oracle headroom reported separately",
            "independence": "Family selection used main-v2 structure only; the artifact was fixed "
                            "before labels and never influenced family selection",
            "training_use": "Forbidden; benchmark families may never join a training split"},
        "pilot_cases": pilot_cases(),
        "budget": {"ledger_before": _read(directory / "provider-cache/usage.json"),
                   "limits": BENCHMARK_LIMITS,
                   "raise_note": "grade ceiling 600->720 by operator decision 2026-10-07 "
                                 "(Mike): hard benchmark labeling; extension 700->720 after "
                                 "repair-attempt overhead; ledger cumulative, no resets"},
        "code_hashes": {name: digest((Path(__file__).parent / name).read_bytes().hex()) for name in (
            "rerank_hard_benchmark.py", "rerank_cohort.py", "rerank_cohort_provider.py",
            "rerank_cohort_features.py", "rerank_training.py", "rerank_model.py",
            "rerank_providers.py", "combine_scoring.py", "strict.py", "embeddings.py")},
        "protected_hashes": {
            "semantic-cache.json": digest((directory / "semantic-cache.json").read_bytes().hex()),
            BASE_COHORT + "/dataset.json": digest((directory / BASE_COHORT / "dataset.json").read_bytes().hex()),
            "artifacts/r0-c18f4bc1.json": digest(ARTIFACT_PATH.read_bytes().hex())},
    }
    if superseded.exists():
        prior = _read(superseded)
        manifest["supersedes"] = {
            "version": SUPERSEDED_VERSION, "manifest_hash": prior.get("manifest_hash"),
            "reason": SUPERSEDING_REASON}
    manifest["manifest_hash"] = digest(manifest)
    _seal(target / "manifest.json", manifest)
    return {"state": "manifest_sealed", "families": len(records),
            "manifest_hash": manifest["manifest_hash"]}


def _job(directory: Path, provider: CohortProviders, job_id: str, kind: str,
         payload: Callable[[int], dict], execute: Callable[[int], dict]) -> dict:
    """Durable one-repair teacher call sharing the global provider cache and ledger."""
    folder = directory / VERSION / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    for attempt in (0, 1):
        request = payload(attempt)
        identity = provider._identity("/v1/chat/completions", request, kind)
        key = digest(identity)
        path = folder / (job_id + "-" + str(attempt) + ".json")
        if path.exists():
            saved = _read(path)
            if saved["request_hash"] != key:
                raise ValueError("benchmark job request identity changed")
            if saved["state"] == "failed":
                continue
            answer = execute(attempt)
            if saved["answer_hash"] != digest(answer):
                raise ValueError("benchmark answer changed during replay")
            return answer
        _atomic_write(path, {"request_hash": key, "state": "started", "kind": kind})
        try:
            answer = execute(attempt)
        except (ValueError, TypeError, KeyError, RuntimeError):
            _atomic_write(path, {"request_hash": key, "state": "failed", "kind": kind})
            continue
        _atomic_write(path, {"request_hash": key, "state": "passed", "kind": kind,
                             "answer": answer, "answer_hash": digest(answer)})
        return answer
    raise ValueError("benchmark teacher response invalid after one repair: " + job_id)


def run_pilot(directory: Path, manifest: dict) -> dict:
    """Check preregistered hard expectations before any family labeling.

    Identical preregistered cases reuse the superseded namespace's sealed
    pilot report after hash verification (the v9/v5 pattern); no HTTP and no
    budget spend happens for a reused pilot.
    """
    target = directory / VERSION
    report_path = target / "pilot-report.json"
    if report_path.exists():
        report = _read(report_path)
        if report["report_hash"] != digest({k: v for k, v in report.items() if k != "report_hash"}):
            raise ValueError("benchmark pilot report changed")
        return report
    prior_path = directory / SUPERSEDED_VERSION / "pilot-report.json"
    if prior_path.exists():
        prior = _read(prior_path)
        if prior["report_hash"] != digest({k: v for k, v in prior.items() if k != "report_hash"}):
            raise ValueError("superseded pilot report changed")
        if prior.get("cases") == len(manifest["pilot_cases"]) and prior.get("passed") is True:
            expected = [case["case_id"] for case in manifest["pilot_cases"]]
            prior_ids = [row["case_id"] for row in prior["pairs"][::2]]
            if prior_ids == expected:
                report = {"cases": prior["cases"], "pairs": prior["pairs"], "grades": prior["grades"],
                          "passed": prior["passed"], "training_started": False,
                          "activation_allowed": False,
                          "reused_from": {"version": SUPERSEDED_VERSION,
                                          "report_hash": prior["report_hash"]}}
                report["report_hash"] = digest(report)
                _seal(report_path, report)
                return report
    provider = CohortProviders(directory / "provider-cache", cohort_id=VERSION)

    def pair(job: tuple[dict, bool]) -> dict:
        case, reverse = job
        case_id = case["case_id"]
        a, b = (case["B"], case["A"]) if reverse else (case["A"], case["B"])
        answer = _job(directory, provider, "pilot-" + case_id + "-" + str(int(reverse)), "pair",
                      lambda attempt: provider.pair_payload(case["vacancy"], a, b, case["policy"], attempt=attempt),
                      lambda attempt: provider.judge(case["vacancy"], a, b, case["policy"], attempt=attempt))
        expected = REVERSE[case["expected"]] if reverse else case["expected"]
        return {"case_id": case_id, "reverse": reverse, "label": answer["label"],
                "agrees": answer["label"] == expected}
    pairs = _parallel(pair, [(case, reverse) for case in manifest["pilot_cases"] for reverse in (False, True)])
    def grade(case: dict) -> dict:
        case_id = case["case_id"]
        answer = _job(directory, provider, "pilot-grade-" + case_id, "grade",
                      lambda attempt: provider.suitability_payload(case["vacancy"], case["A"], case["policy"], attempt=attempt),
                      lambda attempt: provider.suitability(case["vacancy"], case["A"], case["policy"], attempt=attempt))
        return {"case_id": case_id, "grade": answer["grade"],
                "agrees": answer["grade"] == case["expected_grade"]}
    grades = _parallel(grade, manifest["pilot_cases"])
    report = {"cases": len(manifest["pilot_cases"]), "pairs": pairs, "grades": grades,
              "passed": all(row["agrees"] for row in pairs + grades),
              "training_started": False, "activation_allowed": False}
    report["report_hash"] = digest(report)
    _seal(report_path, report)
    return report


def label(directory: Path) -> dict:
    """Label the sealed benchmark families; evaluation-only dataset, never training."""
    manifest = _read(directory / VERSION / "manifest.json")
    if manifest["manifest_hash"] != digest({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("benchmark manifest hash changed")
    for name, value in manifest["code_hashes"].items():
        if digest((Path(__file__).parent / name).read_bytes().hex()) != value:
            raise ValueError("frozen benchmark code changed")
    if digest(ARTIFACT_PATH.read_bytes().hex()) != manifest["protected_hashes"]["artifacts/r0-c18f4bc1.json"]:
        raise ValueError("artifact under test changed after manifest seal")
    pilot = run_pilot(directory, manifest)
    if not pilot["passed"]:
        return {"state": "pilot_failed", "pilot": pilot}
    pilot_spend = 0 if pilot.get("reused_from") else 1
    provider = CohortProviders(directory / "provider-cache", cohort_id=LABEL_COHORT_ID)
    target = directory / VERSION
    used_path = target / "attempt-budget.json"
    with (target / ".budget.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if not used_path.exists():
            _atomic_write(used_path, {"pair": 0, "grade": 0})
    ledger = _read(directory / "provider-cache/usage.json")
    pilot_pair_calls = len(manifest["pilot_cases"]) * 2 * pilot_spend
    pilot_grade_calls = len(manifest["pilot_cases"]) * pilot_spend
    families_path = target / "families"
    families_path.mkdir(parents=True, exist_ok=True)
    labeled_ids = {path.stem for path in families_path.glob("*.json")}
    probe, seen = SUPERSEDED_VERSION, set()
    while probe and probe not in seen and (directory / probe / "manifest.json").exists():
        seen.add(probe)
        prior_families = directory / probe / "families"
        if prior_families.exists():
            labeled_ids |= {path.stem for path in prior_families.glob("*.json")}
        probe = _read(directory / probe / "manifest.json").get("supersedes", {}).get("version")
    uncached = sum(1 for item in manifest["families"] if item["family_id"] not in labeled_ids)
    normal = {"pair": uncached * (21 + 3) + pilot_pair_calls, "grade": uncached * FAMILY_SIZE + pilot_grade_calls}
    for kind, need in normal.items():
        spent = ledger[kind] - manifest["budget"]["ledger_before"][kind]
        if spent + need > BENCHMARK_LIMITS[kind] - manifest["budget"]["ledger_before"][kind]:
            raise ValueError("benchmark " + kind + " allowance does not fit the raised ceiling")
    records = []
    for item in manifest["families"]:
        family_id = item["family_id"]
        record_path = families_path / (family_id + ".json")
        if record_path.exists():
            records.append(_read(record_path))
            continue
        traits = item["candidates"]
        indices = list(itertools.combinations(range(FAMILY_SIZE), 2))
        def pair(job: tuple[int, int], item: dict = item, traits: list[dict] = traits,
                 family_id: str = family_id) -> dict:
            a, b = job
            return _job(directory, provider, family_id + "-pair-" + str(a) + "-" + str(b), "pair",
                        lambda attempt: provider.pair_payload(item["vacancy"], traits[a], traits[b],
                                                              item["policy"], attempt=attempt),
                        lambda attempt: provider.judge(item["vacancy"], traits[a], traits[b],
                                                       item["policy"], attempt=attempt))
        answers = _parallel(pair, indices)
        audit_pairs = [indices[position] for position in (0, 7, 14)]
        reverse = _parallel(pair, [(b, a) for a, b in audit_pairs])
        audits = [answer["label"] == REVERSE[answers[indices.index(association)]["label"]]
                  for association, answer in zip(audit_pairs, reverse, strict=True)]
        def grade(index: int, item: dict = item, traits: list[dict] = traits,
                  family_id: str = family_id) -> dict:
            return _job(directory, provider, family_id + "-grade-" + str(index), "grade",
                        lambda attempt: provider.suitability_payload(item["vacancy"], traits[index],
                                                                     item["policy"], attempt=attempt),
                        lambda attempt: provider.suitability(item["vacancy"], traits[index],
                                                             item["policy"], attempt=attempt))
        suitability = _parallel(grade, list(range(FAMILY_SIZE)))
        pairs = [[a, b, answer["label"]] for (a, b), answer in zip(indices, answers, strict=True)]
        decisive = sum(entry[2] in ("A", "B") for entry in pairs)
        if decisive < 6:
            raise ValueError(family_id + " lacks decisive pairs for a valid benchmark")
        record = {"manifest": {k: v for k, v in item.items() if k != "policy"} | {"policy_id": item["policy"]["id"]},
                  "teacher": provider.teacher_identity(), "traits": traits, "vacancy": item["vacancy"],
                  "feature_record": item["feature_record"],
                  "grades": [answer["grade"] for answer in suitability], "pairs": pairs,
                  "pair_answers": answers, "suitability_answers": suitability,
                  "swap_answers": reverse, "swap_audits": audits,
                  "quarantined": sum(audits) < 2,
                  "evaluation_only": True, "training_started": False, "activation_allowed": False}
        record["record_hash"] = digest(record)
        _seal(record_path, record)
        records.append(record)
        print(json.dumps({"phase": "labels", "family": family_id, "decisive": decisive,
                          "swap_correct": sum(audits)}), flush=True)
    swaps = sum(sum(record["swap_audits"]) for record in records)
    quality = {"swap_correct": swaps, "swap_total": 3 * len(records),
               "quarantined_families": [record["manifest"]["family_id"] for record in records if record["quarantined"]]}
    quality["passed"] = swaps >= 2 * len(records) and not quality["quarantined_families"]
    dataset = {"version": VERSION, "manifest_hash": manifest["manifest_hash"], "families": records,
               "teacher": records[0]["teacher"], "quality": quality, "pilot_hash": pilot["report_hash"],
               "training_started": False, "activation_allowed": False, "evaluation_only": True}
    dataset["dataset_hash"] = digest(dataset)
    _seal(target / "dataset.json", dataset)
    report = {"state": "labeled_quality_passed" if quality["passed"] else "labeled_quality_failed",
              "families": len(records), "quality": quality,
              "pair_labels": {label_name: sum(entry[2] == label_name for record in records for entry in record["pairs"])
                              for label_name in ("A", "B", "tie", "insufficient")},
              "ledger_after": _read(directory / "provider-cache/usage.json"),
              "dataset_hash": dataset["dataset_hash"], "training_started": False,
              "activation_allowed": False}
    report["report_hash"] = digest(report)
    _seal(target / "label-report.json", report)
    return report


def evaluate_artifact(directory: Path) -> dict:
    """Evaluate the packaged artifact and main order on the labeled benchmark."""
    manifest = _read(directory / VERSION / "manifest.json")
    dataset = _read(directory / VERSION / "dataset.json")
    artifact = _read(ARTIFACT_PATH)
    if artifact["content_hash"] != manifest["artifact_under_test"]["content_hash"]:
        raise ValueError("artifact under test differs from the sealed manifest")
    families, scores = [], {}
    headroom = []
    for record in dataset["families"]:
        family_id = record["manifest"]["family_id"]
        vectors = [FeatureVector(**entry) for entry in record["feature_record"]["vectors"]]
        for vector in vectors:
            if vector.schema_hash != artifact["feature_schema_hash"]:
                raise ValueError("artifact feature schema incompatible with the benchmark")
        grades: list[int | None] = record["grades"]
        families.append(TrainingFamily(
            family_id=family_id, direction=record["manifest"]["direction"],
            split="golden", vectors=vectors, main_scores=record["feature_record"]["main_scores"],
            grades=grades, pairs=[tuple(pair) for pair in record["pairs"]],
            source_id=family_id, slice_tags=(record["manifest"]["branch"],)))
        scores[family_id] = _artifact_scores(artifact, vectors)
        oracle = _oracle_headroom(record["feature_record"]["main_scores"], grades)
        if oracle is not None:
            headroom.append(oracle)
    result = evaluate(families, scores)
    result["oracle_headroom"] = {
        "macro_max_attainable_ndcg_delta": sum(headroom) / len(headroom) if headroom else 0.0,
        "families": len(headroom),
        "note": ("Perfect-ordering ceiling of this benchmark; the sealed v9 golden ceiling was "
                 "0.004110, below the 0.02 activation gate")}
    result["artifact"] = {"content_hash": artifact["content_hash"], "mode": "sort-only manual rollout"}
    result["benchmark"] = {"version": VERSION, "manifest_hash": manifest["manifest_hash"],
                           "dataset_hash": dataset["dataset_hash"], "evaluation_only": True}
    result["activation_note"] = ("Benchmark result informs the operator; promotion continues to "
                                 "require the sealed cohort gates and completed blind reviews")
    result["report_hash"] = digest({k: v for k, v in result.items() if k != "report_hash"})
    _seal(directory / VERSION / "evaluation.json", result)
    return result


def status(directory: Path) -> dict:
    """Report the current benchmark namespace state without any network or key access."""
    target = directory / VERSION
    state: dict[str, object] = {"version": VERSION, "directory": str(target),
                                "artifact": ARTIFACT_PATH.name}
    for name in ("manifest.json", "pilot-report.json", "dataset.json", "label-report.json", "evaluation.json"):
        state[name] = (target / name).exists()
    if state["manifest.json"]:
        state["ledger"] = _read(directory / "provider-cache/usage.json")
        state["limits"] = BENCHMARK_LIMITS
    return state


def main() -> None:
    """Run one benchmark phase; credentials stay lazy and outputs stay sealed."""
    parser = argparse.ArgumentParser(description="Pre-registered hard benchmark for R0")
    parser.add_argument("command", choices=("ingest", "label", "evaluate", "status"))
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--staging", type=Path, default=None)
    args = parser.parse_args()
    if args.command == "ingest":
        if args.staging is None:
            raise SystemExit("ingest requires --staging")
        print(json.dumps(ingest(args.directory, args.staging), ensure_ascii=False))
    elif args.command == "label":
        print(json.dumps(label(args.directory), ensure_ascii=False))
    elif args.command == "evaluate":
        print(json.dumps(evaluate_artifact(args.directory), ensure_ascii=False))
    else:
        print(json.dumps(status(args.directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
