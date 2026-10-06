"""Bounded, opt-in pair-only policy revision; preserves all source and grade receipts."""

import argparse
import fcntl
import itertools
import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from matching_engine.rerank_cohort_provider import (
    CohortProviders,
    _parse,
    source_citations,
    validate_candidate,
)
from matching_engine.rerank_cohort_review import _manual, _markdown, _packets
from matching_engine.rerank_experiment import _parallel
from matching_engine.rerank_hash import digest
from matching_engine.rerank_protocol import expected_families
from matching_engine.rerank_providers import LIMITS, _atomic_write
from matching_engine.rerank_sample_probe import _cycles, _ordering_ceiling

VERSION = "rerank-grounded-pair-repair-v2"
PREVIOUS_VERSION = "rerank-grounded-pair-repair-v1"
PREVIOUS_COMMIT = "7585ee7b382a406cd0e75a852883e36cd2f31790"
PREVIOUS_CODE_HASH = "6c2e64302e7b11fdc3e373b9b8793f67cd45857e5c4b0566626db38302182f74"
PREVIOUS_ATTEMPTS = 24
BASE_COHORT = "rerank-grounded-cohort-v9"
PAIR_PROMPT_VERSION = "rerank-uniform-cohort-pair-v4"
MAX_ATTEMPTS = 672
PROJECTED_BASE_FINAL_PAIR = 2268
CLASS_ONLY_POLICY = {
    "id": "class-only-policy-v1", "version": "class-only-policy-v1",
    "ordered_priorities": [
        "Compare accepted formal test grade: senior > middle > junior > intern, independently of profile status.",
        "For equal grade, compare production only when both facts are known and their own statuses are not rejected: true > false.",
        "For equal production, compare its own confirmation: confirmed > partial > self_reported > pending.",
        "Then compare duration only when both durations are known and their own statuses are not rejected; greater duration wins.",
        "Skip inapplicable missing comparisons; if no applicable difference exists, use tie.",
    ],
    "instructions": "Do not invent technical requirements for a class-only vacancy. Ignore salary, biography, "
        "skill breadth and unrelated facts. A rejected parent profile status does not invalidate an accepted formal grade. "
        "Never infer an individual fact's confirmation from parent status. Missing facts are not zero or false. "
        "Insufficient is reserved for an explicitly essential exclusive measurement that is unavailable.",
    "exclusive_criterion": False, "required_measurements": [],
}
CLASS_ONLY_POLICY_HASH = digest(CLASS_ONLY_POLICY)
_PAIRS = list(itertools.combinations(range(7), 2))
_AUDIT_INDICES = (0, 7, 14)
_REVERSE = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}
_GRADE = {"intern": 0, "junior": 1, "middle": 2, "senior": 3}
_STATUS = {"pending": 0, "self_reported": 1, "partial": 2, "confirmed": 3}
_DECISION_CONTRACT = (
    "PAIR DECISION CONTRACT v4. Execute these checks in order; the essential gate precedes ANY other criterion. "
    "STEP 0: if exclusive_criterion=true and ANY required_measurements path is absent or null on EITHER side, "
    "STOP with insufficient and insufficient_source_facts. Grade, confirmation, production, duration, salary "
    "and prose CANNOT substitute for the missing measurement, even when grades differ. "
    "For a class-only vacancy ONLY, continue lexicographically: "
    "STEP 1: compare accepted formal grade senior>middle>junior>intern, regardless of parent status. "
    "If grades differ, select the higher grade and STOP. Otherwise continue. "
    "STEP 2 is applicable ONLY when BOTH production values are non-null and BOTH OWN production_status values "
    "are not rejected. If either side fails this condition, skip BOTH production value and production "
    "confirmation comparisons completely; do not reward the other side for being known or confirmed. "
    "If applicable, compare true>false; if values differ STOP. If values are equal, compare OWN production "
    "confirmed>partial>self_reported>pending; if statuses differ STOP. Otherwise continue. "
    "STEP 3 is applicable ONLY when BOTH duration values are non-null and BOTH OWN duration_status values "
    "are not rejected. If either side fails, skip duration completely, without rewarding the known side. "
    "If applicable, greater duration wins; if values differ STOP. No duration confirmation tiebreak exists. "
    "STEP 4: if no applicable step established a difference, STOP with tie and comparable_fit. In particular, "
    "equal formal grades plus inapplicable production and duration MUST be tie regardless of one side's "
    "known duration or confirmed production. Rejected is an applicability exclusion, not a bottom confirmation rank. "
    "These same semantic checks apply on the initial request and the single repair request. "
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("pair repair receipt must be an object")
    return value


def _verified(path: Path, field: str) -> dict:
    value = _read(path)
    if value.get(field) != digest({key: item for key, item in value.items() if key != field}):
        raise ValueError("pair repair receipt hash mismatch")
    return value


def _seal(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != value:
            raise ValueError("immutable pair repair receipt changed")
    else:
        _atomic_write(path, value)


def _hashed(value: dict, field: str) -> dict:
    return {**value, field: digest(value)}


def _class_only(vacancy: dict) -> bool:
    return not any(vacancy.get(field) for field in ("domens", "stacks", "soft_skills"))


def revised_policy(vacancy: dict, original: dict) -> dict:
    """Replace class-only semantics explicitly; retain essential exclusive measurements."""
    if not _class_only(vacancy):
        return deepcopy(original)
    policy = deepcopy(CLASS_ONLY_POLICY)
    for field in ("exclusive_criterion", "required_measurements"):
        if field in original:
            policy[field] = deepcopy(original[field])
    return policy


def _measurement(candidate: dict, path: str) -> object:
    value = candidate
    for field in path.split("."):
        if type(value) is not dict or field not in value or field == "description":
            return None
        value = value[field]
    return value


def _exclusive_missing(policy: dict, a: dict, b: dict) -> bool:
    return policy.get("exclusive_criterion") is True and any(
        _measurement(a, field) is None or _measurement(b, field) is None
        for field in policy.get("required_measurements", []))


def class_only_label(a: dict, b: dict, policy: dict) -> str:
    """Apply the frozen class-only comparator without scores or inferred confirmation."""
    validate_candidate(a)
    validate_candidate(b)
    if _exclusive_missing(policy, a, b):
        return "insufficient"
    pairs = [(_GRADE[a["grade"]], _GRADE[b["grade"]])]
    if all(candidate["production_support"] is not None and candidate["production_status"] != "rejected"
           for candidate in (a, b)):
        pairs.extend([(int(a["production_support"]), int(b["production_support"])),
                      (_STATUS[a["production_status"]], _STATUS[b["production_status"]])])
    if all(candidate["duration_months"] is not None and candidate["duration_status"] != "rejected"
           for candidate in (a, b)):
        pairs.append((a["duration_months"], b["duration_months"]))
    for left, right in pairs:
        if left != right:
            return "A" if left > right else "B"
    return "tie"


def _allowed_citations(vacancy: dict, a: dict, b: dict) -> dict[str, list[str]]:
    citations = source_citations(a, b)
    requested = {key for field in ("domens", "stacks", "soft_skills") for key in vacancy.get(field, {})}
    class_fields = {"grade", "status", "structured_facts_available", "production_support", "production_status",
                    "duration_months", "duration_status"}
    allowed = {"A": [], "B": []}
    for token, fact in citations.items():
        field = fact["field"]
        if _class_only(vacancy):
            if field not in class_fields:
                continue
        elif field.startswith(("levels.", "skill_statuses.")) and field.split(".", 1)[1] not in requested:
            continue
        elif field.startswith(("project_domains.", "project_stacks.")):
            group, index, _primitive = field.split(".", 2)
            candidate = a if fact["candidate"] == "A" else b
            if candidate[group][int(index)]["key"] not in requested:
                continue
        allowed[fact["candidate"]].append(token)
    return allowed


class PairRepairProviders(CohortProviders):
    """Pair-only v4 teacher with inherited cache, five shared slots and lazy credentials."""

    def __init__(self, directory: Path, *, key_file: Path | None = None) -> None:
        """Bind the repair namespace; do not read a credential or reset any shared budget."""
        super().__init__(directory, cohort_id=VERSION, key_file=key_file)

    def _endpoint(self, kind: str) -> str:
        if kind != "pair":
            raise ValueError("pair repair forbids generation and grade calls")
        return super()._endpoint(kind)

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        identity = super()._identity(path, payload, kind)
        identity["prompt_version"] = PAIR_PROMPT_VERSION + ("-repair1" if identity["attempt"] else "")
        return identity

    def teacher_identity(self, kind: str = "pair", attempt: int = 0) -> dict:
        """Identify the sole authorized pair teacher; grade/generation identities are forbidden."""
        self._endpoint(kind)
        identity = super().teacher_identity(kind, attempt)
        identity["prompt_version"] = PAIR_PROMPT_VERSION + ("-repair1" if attempt else "")
        return identity

    def generation_payload(self, direction: str, family_id: str, keys: Sequence[str], count: int = 7,
                           attempt: int = 0) -> dict:
        """Reject generation preparation in this pair-only operator."""
        raise ValueError("pair repair forbids generation")

    def suitability_payload(self, vacancy: dict, candidate: dict, policy: dict, attempt: int = 0) -> dict:
        """Reject grade preparation; existing v2 suitability labels are preserved."""
        raise ValueError("pair repair forbids grade")

    def pair_payload(self, vacancy: dict, a: dict, b: dict, policy: dict, attempt: int = 0) -> dict:
        """Clarify class-only and declared technical priorities without model/main metrics."""
        payload = super().pair_payload(vacancy, a, b, policy, attempt)
        allowed = _allowed_citations(vacancy, a, b)
        for side in ("A", "B"):
            payload["response_format"]["json_schema"]["schema"]["properties"][f"evidence_{side}"]["items"]["enum"] = allowed[side]
        state = json.loads(payload["messages"][1]["content"])
        state["available_citations"] = allowed
        state["source_citations"] = {token: fact for token, fact in state["source_citations"].items()
                                     if token in allowed[fact["candidate"]]}
        state["criterion_applicability"] = {
            "exclusive_criterion": policy.get("exclusive_criterion", False),
            "required_measurements": [{"path": field, "available_A": _measurement(a, field) is not None,
                                       "available_B": _measurement(b, field) is not None}
                                      for field in policy.get("required_measurements", [])],
            "class_only": _class_only(vacancy),
            "production_comparison_applicable": all(candidate["production_support"] is not None
                and candidate["production_status"] != "rejected" for candidate in (a, b)),
            "duration_comparison_applicable": all(candidate["duration_months"] is not None
                and candidate["duration_status"] != "rejected" for candidate in (a, b)),
        }
        payload["messages"][1]["content"] = json.dumps(state, ensure_ascii=False, sort_keys=True)
        payload["messages"][0]["content"] = _DECISION_CONTRACT + payload["messages"][0]["content"] + (
            "For technical vacancies preserve the supplied original policy and its ordered priorities verbatim; "
            "the class-only rubric never substitutes for it. When coverage/declared-fit/depth is a primary policy priority, compare common explicitly requested "
            "keys, declared level coverage of the stated thresholds, then declared depth BEFORE confirmation. "
            "Unconfirmed declared levels still count as declarations; an individual rejected skill does not establish "
            "that ability. Missing keys are unknown, not level zero. Confirmation is a secondary criterion only "
            "when explicitly named by the policy. Never infer skill/fact status from parent profile status, "
            "and never invent requirements for class-only or unspecified keys. Respect essential exclusive criteria."
        )
        return payload

    def judge(self, vacancy: dict, a: dict, b: dict, policy: dict, attempt: int = 0) -> dict:
        """Validate exact references and class-only semantics; never silently rewrite an answer."""
        answer = super().judge(vacancy, a, b, policy, attempt)
        allowed = _allowed_citations(vacancy, a, b)
        if any(token not in allowed[side] for side in ("A", "B") for token in answer[f"evidence_{side}"]):
            raise ValueError("teacher cited an inapplicable or unspecified technical requirement")
        if _class_only(vacancy) and answer["label"] != class_only_label(a, b, policy):
            raise ValueError("teacher contradicts frozen class-only policy")
        return answer


def _candidate(grade="middle", status="partial", level=4, skill_status="self_reported", **facts) -> dict:
    value = {"grade": grade, "status": status, "levels": {"python": level}, "skill_statuses": {"python": skill_status},
             "salary_min": 80000, "description": "Синтетический профиль для проверки правил.",
             "structured_facts_available": False, "duration_months": None, "duration_status": None,
             "production_support": None, "production_status": None, "role": None, "role_status": None,
             "project_domains": [], "project_stacks": []}
    value.update(facts)
    value["structured_facts_available"] = any(value[field] is not None for field in (
        "duration_months", "production_support", "role")) or bool(value["project_domains"] or value["project_stacks"])
    validate_candidate(value)
    return value


def pilot_cases() -> list[dict]:
    """Preregister five class-only, four declared-level and one exclusive-unknown fixture."""
    empty = {"domens": {}, "stacks": {}, "soft_skills": {}, "bio": None, "salary": {"min": 50000, "max": 200000}}
    facts = [
        ("backend", _candidate("junior", production_support=True, production_status="confirmed"), _candidate("middle"), "B"),
        ("frontend", _candidate(status="pending"), _candidate(status="confirmed"), "tie"),
        ("fullstack", _candidate(production_support=True, production_status="rejected"),
         _candidate(production_support=False, production_status="confirmed", duration_months=36, duration_status="confirmed"), "tie"),
        ("mobile", _candidate("senior", "rejected"), _candidate("middle", "confirmed"), "A"),
        ("qa", _candidate(status="pending", production_support=False, production_status="confirmed"),
         _candidate(status="confirmed", production_support=False, production_status="pending"), "A"),
    ]
    cases = [{"case_id": f"class-{index}", "vacancy": {"class": direction, **deepcopy(empty)},
              "A": a, "B": b, "policy": deepcopy(CLASS_ONLY_POLICY), "expected": label}
             for index, (direction, a, b, label) in enumerate(facts)]
    for index, (direction, status) in enumerate(zip(("devops", "data", "ml", "backend"),
                                                  ("pending", "self_reported", "partial", "confirmed"), strict=True)):
        cases.append({"case_id": f"declared-{index}", "vacancy": {"class": direction, **deepcopy(empty), "stacks": {"python": 4}},
                      "A": _candidate(status="rejected" if index == 3 else "partial", level=5, skill_status=status),
                      "B": _candidate(status="confirmed", level=4, skill_status="confirmed"),
                      "policy": {"id": "explicit-technical-v1", "ordered_priorities": [
                          "Prefer declared coverage of explicit requested levels.", "Then prefer declared depth on those keys.",
                          "Only then prefer stronger confirmation."], "exclusive_criterion": False, "required_measurements": []},
                      "expected": "A"})
    cases.append({"case_id": "essential-unknown", "vacancy": {"class": "qa", **deepcopy(empty)},
                  "A": _candidate("senior"), "B": _candidate("junior"),
                  "policy": {**deepcopy(CLASS_ONLY_POLICY), "exclusive_criterion": True,
                             "required_measurements": ["uptime_percentage"]}, "expected": "insufficient"})
    return cases


def _sources(directory: Path, base_cohort: str) -> tuple[dict, dict[str, dict]]:
    if base_cohort != BASE_COHORT:
        raise ValueError("this semantic repair is pinned to the v9 cohort")
    base = directory / base_cohort
    manifest = _verified(base / "manifest.json", "manifest_hash")
    expected = {row["family_id"]: row for row in expected_families()}
    rows = manifest.get("families", [])
    if len(rows) != 60 or {row["family_id"] for row in rows} != set(expected):
        raise ValueError("pair repair requires all 60 frozen families")
    sources = {}
    for row in rows:
        identifier = row["family_id"]
        if any(row[field] != expected[identifier][field] for field in ("direction", "split", "source_id")):
            raise ValueError("pair repair source ancestry changed")
        source = _verified(base / "sources" / f"{identifier}.json", "record_hash")
        if source["manifest"] != row or len(source["traits"]) != 7:
            raise ValueError("pair repair accepted source differs")
        for trait in source["traits"]:
            validate_candidate(trait)
        sources[identifier] = source
    if sum(_class_only(source["manifest"]["vacancy"]) for source in sources.values()) != 16:
        raise ValueError("pair repair requires all 16 class-only families")
    return manifest, sources


def _base_remaining(directory: Path) -> int:
    remaining = 0
    for family in expected_families():
        identifier = family["family_id"]
        completed_family = directory / BASE_COHORT / "families" / f"{identifier}.json"
        if completed_family.exists():
            _verified(completed_family, "record_hash")
            continue
        for a, b in [*_PAIRS, *[tuple(reversed(_PAIRS[index])) for index in _AUDIT_INDICES]]:
            completed = False
            for attempt in (0, 1):
                path = directory / BASE_COHORT / "jobs" / f"{identifier}-pair-{a}-{b}-{attempt}.json"
                if path.exists() and _read(path).get("state") == "passed":
                    completed = True
            remaining += not completed
    return remaining


def _previous_pilot(directory: Path) -> dict:
    """Audit the frozen v1's 24 raw attempts; unknown prior repair usage blocks v2."""
    unknown = [path.name for path in directory.glob("rerank-grounded-pair-repair-*")
               if path.is_dir() and path.name not in (PREVIOUS_VERSION, VERSION)]
    target = directory / PREVIOUS_VERSION
    budget = _read(target / "budget.json")
    manifest = _verified(target / "pilot-manifest.json", "manifest_hash")
    report = _verified(target / "pilot-report.json", "report_hash")
    if (unknown or budget != {"pilot_initial": 20, "pilot_retry": 4, "family_initial": 0, "family_retry": 0}
            or manifest["code_hash"] != PREVIOUS_CODE_HASH or manifest["cases"] != pilot_cases() or len(manifest["requests"]) != 20
            or report["pilot_manifest_hash"] != manifest["manifest_hash"]
            or (report["valid"], report["correct"], report["swap_correct"], report["passed"]) != (16, 16, 8, False)
            or (target / "manifest.json").exists() or (target / "dataset.json").exists()):
        raise ValueError("unknown or changed prior pair repair attempts; v1 must remain exactly 24")
    rows, hashes, raw_hashes, jobs, expected_jobs = [], {}, {}, {}, set()
    request_index = 0
    for case in manifest["cases"]:
        for swapped in (False, True):
            expected = _REVERSE[case["expected"]] if swapped else case["expected"]
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            admitted = None
            for attempt in (0, 1):
                job_id = f"pilot-{case['case_id']}-{int(swapped)}-{attempt}"
                path = target / "jobs" / f"{job_id}.json"
                if not path.exists():
                    if attempt == 0 or admitted is None:
                        raise ValueError("prior pair repair raw job missing")
                    continue
                expected_jobs.add(path.name)
                job = _read(path)
                receipt = _read(directory / "provider-cache" / f"{job['request_hash']}.json")
                identity = receipt["request"]
                state = json.loads(identity["payload"]["messages"][1]["content"])
                if (job.get("kind") != "pair" or job.get("category") != "pilot" or job.get("attempt") != attempt
                        or job.get("state") not in ("passed", "failed") or job.get("fatal_http_statuses")
                        or digest(identity) != job["request_hash"] or receipt["request_hash"] != job["request_hash"]
                        or receipt["response_hash"] != digest(receipt["response"])
                        or identity["cohort_id"] != PREVIOUS_VERSION or identity["kind"] != "pair"
                        or identity["prompt_version"] != "rerank-uniform-cohort-pair-v3" + ("-repair1" if attempt else "")
                        or identity["attempt"] != attempt or identity["model"] != "qwen3.8-27b"
                        or state["A"] != a or state["B"] != b or state["policy"] != case["policy"]
                        or state["vacancy"] != case["vacancy"] or state["request_nonce"] != {
                            "attempt": attempt, "cohort_id": PREVIOUS_VERSION, "kind": "pair"}
                        or (attempt == 0 and identity != manifest["requests"][request_index])):
                    raise ValueError("prior pair repair raw receipt provenance changed")
                answer = _parse(receipt["response"], "qwen3.8-27b")
                if job["state"] == "passed":
                    if (admitted is not None or answer["label"] != expected or job["answer"] != answer
                            or job["answer_hash"] != digest(answer)):
                        raise ValueError("prior pair repair admitted answer changed")
                    admitted = answer
                elif answer["label"] == expected:
                    raise ValueError("prior semantic failure no longer matches its frozen raw answer")
                hashes[job_id] = {"job_hash": digest(job), "cache_hash": digest(receipt)}
                raw_hashes[job["request_hash"]] = receipt["response_hash"]
                jobs[path.name] = job
            rows.append({"case_id": case["case_id"], "swapped": swapped, "answer": admitted, "expected": expected,
                         "error": None if admitted is not None else "pair repair response invalid after one bounded repair"})
            request_index += 1
    if ({path.name for path in (target / "jobs").glob("*.json")} != expected_jobs
            or len(expected_jobs) != PREVIOUS_ATTEMPTS or report["rows"] != rows):
        raise ValueError("unknown or changed prior pair repair raw attempt count")
    closure = _verified(target / "closure-observation.json", "observation_hash")
    if (closure["cohort_id"] != PREVIOUS_VERSION or closure["pilot_manifest_hash"] != manifest["manifest_hash"]
            or closure["pilot_report_hash"] != report["report_hash"] or closure["pilot_passed"] is not False
            or closure["job_receipts_hash"] != digest(jobs) or closure["raw_response_hashes"] != raw_hashes
            or closure["actual_pair_reservations"] != PREVIOUS_ATTEMPTS
            or closure["base_dataset_existed"] is not False
            or closure["base_manifest_hash"] != manifest["base_manifest_hash"]):
        raise ValueError("prior repair closure observation differs from terminal raw receipts")
    return {"version": PREVIOUS_VERSION, "source_commit": PREVIOUS_COMMIT, "code_hash": PREVIOUS_CODE_HASH,
            "attempts": PREVIOUS_ATTEMPTS, "pilot_manifest_hash": manifest["manifest_hash"],
            "pilot_report_hash": report["report_hash"], "budget_hash": digest(budget), "raw_receipt_hashes": hashes,
            "source_hashes": manifest["source_hashes"], "closure_observation_hash": closure["observation_hash"],
            "closure_global_ledger": closure["global_ledger"]}


def _budget_check(directory: Path, used: int) -> dict:
    previous = _previous_pilot(directory)
    saved = directory / VERSION / "pilot-manifest.json"
    if saved.exists() and _verified(saved, "manifest_hash")["previous_repair"] != previous:
        raise ValueError("frozen previous pair repair provenance changed")
    ledger = _read(directory / "provider-cache/usage.json")
    if (PROJECTED_BASE_FINAL_PAIR + previous["attempts"] + MAX_ATTEMPTS > LIMITS["pair"]
            or ledger["pair"] < previous["closure_global_ledger"]["pair"]
            or ledger["pair"] + _base_remaining(directory) + MAX_ATTEMPTS - used > LIMITS["pair"]):
        raise ValueError("pair repair cannot preserve remaining base calls inside global budget")
    return ledger


def _job(directory: Path, provider: PairRepairProviders, job_id: str, vacancy: dict,
         a: dict, b: dict, policy: dict, category: str) -> dict:
    folder = directory / VERSION / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f".{job_id}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _job_unlocked(directory, provider, job_id, vacancy, a, b, policy, category)


def _job_unlocked(directory: Path, provider: PairRepairProviders, job_id: str, vacancy: dict,
                  a: dict, b: dict, policy: dict, category: str) -> dict:
    target = directory / VERSION
    for attempt in (0, 1):
        payload = provider.pair_payload(vacancy, a, b, policy, attempt)
        identity = provider._identity("/v1/chat/completions", payload, "pair")
        key = digest(identity)
        path = target / "jobs" / f"{job_id}-{attempt}.json"
        cache = directory / "provider-cache" / f"{key}.json"
        if path.exists():
            receipt = _read(path)
            if receipt["request_hash"] != key:
                raise ValueError("pair repair job identity changed")
            if receipt["state"] == "failed":
                if receipt.get("fatal_http_statuses"):
                    raise ValueError("fatal pair repair provider HTTP failure")
                continue
            if not cache.exists():
                raise ValueError("ambiguous started pair repair call requires operator review")
            if receipt["state"] == "passed":
                answer = provider.judge(vacancy, a, b, policy, attempt)
                if digest(answer) != receipt["answer_hash"]:
                    raise ValueError("pair repair replay answer changed")
                return answer
        else:
            with (target / ".budget.lock").open("a+") as budget_lock:
                fcntl.flock(budget_lock, fcntl.LOCK_EX)
                budget_path = target / "budget.json"
                budget = _read(budget_path) if budget_path.exists() else {"pilot_initial": 0, "pilot_retry": 0,
                                                                       "family_initial": 0, "family_retry": 0}
                field = f"{category}_{'retry' if attempt else 'initial'}"
                maximum = {"pilot_initial": 20, "pilot_retry": 20, "family_initial": 600, "family_retry": 32}[field]
                if budget[field] >= maximum:
                    raise ValueError("pair repair bounded response allowance exhausted")
                _budget_check(directory, sum(budget.values()))
                budget[field] += 1
                _atomic_write(budget_path, budget)
                _seal(path, {"state": "started", "request_hash": key, "kind": "pair", "category": category,
                             "attempt": attempt})
        try:
            answer = provider.judge(vacancy, a, b, policy, attempt)
        except (ValueError, TypeError, KeyError, RuntimeError) as error:
            statuses = [int(_read(failure).get("http_status", 0))
                        for failure in provider.directory.glob(f"{key}.failure-*.json")]
            fatal = [status for status in statuses if status in (400, 401, 403, 404, 422)]
            if isinstance(error, RuntimeError) and not cache.exists() and not any(statuses):
                _atomic_write(path, {"state": "started", "request_hash": key, "kind": "pair", "category": category,
                                     "attempt": attempt, "error_type": type(error).__name__})
                raise ValueError("ambiguous started pair repair call requires operator review") from None
            _atomic_write(path, {"state": "failed", "request_hash": key, "kind": "pair", "category": category,
                                 "attempt": attempt, "error_type": type(error).__name__, "fatal_http_statuses": fatal})
            if fatal:
                raise ValueError("fatal pair repair provider HTTP failure") from None
            continue
        _atomic_write(path, {"state": "passed", "request_hash": key, "kind": "pair", "category": category,
                             "attempt": attempt, "answer": answer, "answer_hash": digest(answer)})
        return answer
    raise ValueError("pair repair response invalid after one bounded repair")


def prepare_pilot(directory: Path) -> dict:
    """Freeze ten fixtures, source/audit hashes and selection predicate before any HTTP."""
    base_manifest, sources = _sources(directory, BASE_COHORT)
    previous = _previous_pilot(directory)
    if previous["source_hashes"] != {identifier: source["record_hash"] for identifier, source in sources.items()}:
        raise ValueError("v2 pair repair sources differ from the frozen v1 pilot")
    provider = PairRepairProviders(directory / "provider-cache")
    cases = pilot_cases()
    requests = []
    for case in cases:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            requests.append(provider._identity("/v1/chat/completions",
                            provider.pair_payload(case["vacancy"], a, b, case["policy"]), "pair"))
    target = directory / VERSION
    existing = target / "pilot-manifest.json"
    if existing.exists():
        saved = _verified(existing, "manifest_hash")
        if (saved["source_hashes"] != {identifier: source["record_hash"] for identifier, source in sources.items()}
                or saved["base_manifest_hash"] != base_manifest["manifest_hash"]
                or saved["previous_repair"] != previous
                or saved["cases"] != cases or saved["requests"] != requests
                or saved["code_hash"] != digest(Path(__file__).read_bytes().hex())):
            raise ValueError("frozen pair repair pilot inputs changed")
        return saved
    ledger = _budget_check(directory, 0)
    audits = {path.stem: _verified(path, "record_hash")["record_hash"]
              for path in (directory / BASE_COHORT / "families").glob("*.json")}
    manifest = _hashed({"version": VERSION, "base_cohort": BASE_COHORT,
                       "base_manifest_hash": base_manifest["manifest_hash"],
                       "source_hashes": {identifier: source["record_hash"] for identifier, source in sources.items()},
                       "pinned_base_family_audits": audits, "cases": cases, "requests": requests,
                       "class_only_policy": CLASS_ONLY_POLICY, "class_only_policy_hash": CLASS_ONLY_POLICY_HASH,
                       "previous_repair": previous,
                       "selection_predicate": "All 16 class-only families plus the union of quarantined non-class families and all non-class families nominated by disagreement in the exact original assistant blind 20; maximum 9 non-class, no truncation or model metrics; manual labels never enter teacher requests",
                       "maximum_selected_families": 25, "maximum_attempts": MAX_ATTEMPTS,
                       "projected_base_final_pair": PROJECTED_BASE_FINAL_PAIR, "projected_prior_pair_attempts": PREVIOUS_ATTEMPTS,
                       "ledger_before_pilot": ledger,
                       "code_hash": digest(Path(__file__).read_bytes().hex()), "teacher": provider.teacher_identity()}, "manifest_hash")
    _seal(existing, manifest)
    return manifest


def revised_review_packets(directory: Path, pilot_manifest: dict) -> dict[str, dict]:
    """Copy fixed blind selections, revising only class-only policy; keep originals immutable."""
    base_manifest, sources = _sources(directory, BASE_COHORT)
    expected = _packets(base_manifest, sources, BASE_COHORT)
    result = {}
    for reviewer in ("assistant", "user"):
        packet = _verified(directory / BASE_COHORT / "reviews" / f"{reviewer}-packet.json", "packet_hash")
        if packet != expected[reviewer] or packet["source_hashes"] != pilot_manifest["source_hashes"]:
            raise ValueError("base review source hashes differ from repair sources")
        revised = deepcopy(packet)
        revised.pop("packet_hash")
        revised.update(base_packet_hash=packet["packet_hash"], pair_repair_pilot_manifest_hash=pilot_manifest["manifest_hash"],
                       policy_revision={"version": CLASS_ONLY_POLICY["version"], "hash": CLASS_ONLY_POLICY_HASH})
        for case in revised["cases"]:
            source = sources[case["family_id"]]
            if (case["A"] != source["traits"][case["a_index"]] or case["B"] != source["traits"][case["b_index"]]
                    or case["policy"] != source["manifest"]["policy"]):
                raise ValueError("base review case differs from exact accepted source")
            if _class_only(case["vacancy"]):
                case["policy"] = revised_policy(case["vacancy"], case["policy"])
        revised["packet_hash"] = digest(revised)
        _seal(directory / VERSION / "reviews" / f"{reviewer}-packet.json", revised)
        markdown_path = directory / VERSION / "reviews" / f"{reviewer}-packet.md"
        markdown = _markdown(revised)
        if markdown_path.exists() and markdown_path.read_text(encoding="utf-8") != markdown:
            raise ValueError("immutable revised review Markdown changed")
        if not markdown_path.exists():
            markdown_path.write_text(markdown, encoding="utf-8")
        result[reviewer] = revised
    return result


def _pilot(directory: Path, provider: PairRepairProviders, manifest: dict) -> dict:
    path = directory / VERSION / "pilot-report.json"
    if path.exists():
        return _verified_pilot(directory, manifest)
    def compare(item):
        case, swapped = item
        a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
        answer, error = None, None
        try:
            answer = _job(directory, provider, f"pilot-{case['case_id']}-{int(swapped)}",
                          case["vacancy"], a, b, case["policy"], "pilot")
        except ValueError as exception:
            error = str(exception) if str(exception).startswith("pair repair response invalid") else None
            if error is None:
                raise
        return {"case_id": case["case_id"], "swapped": swapped, "answer": answer,
                "expected": _REVERSE[case["expected"]] if swapped else case["expected"], "error": error}

    rows = _parallel(compare, [(case, swapped) for case in manifest["cases"] for swapped in (False, True)])
    valid = sum(row["answer"] is not None for row in rows)
    correct = sum(row["answer"] is not None and row["answer"]["label"] == row["expected"] for row in rows)
    swaps = sum(rows[index]["answer"] is not None and rows[index + 1]["answer"] is not None
                and rows[index + 1]["answer"]["label"] == _REVERSE[rows[index]["answer"]["label"]]
                for index in range(0, 20, 2))
    report = _hashed({"pilot_manifest_hash": manifest["manifest_hash"], "rows": rows, "valid": valid,
                      "correct": correct, "swap_correct": swaps, "passed": valid == correct == 20 and swaps == 10,
                      "ledger_after_pilot": _read(directory / "provider-cache/usage.json"),
                      "grade_calls": 0, "generation_calls": 0, "activation_allowed": False}, "report_hash")
    _seal(path, report)
    _observe_pilot_closure(directory, manifest, report)
    return report


def _pilot_raw_snapshot(directory: Path) -> tuple[dict, dict]:
    jobs = {path.name: _read(path) for path in sorted((directory / VERSION / "jobs").glob("pilot-*.json"))}
    raw = {}
    for job in jobs.values():
        if job.get("state") not in ("passed", "failed"):
            raise ValueError("pilot closure requires every raw job to be terminal")
        receipt = _read(directory / "provider-cache" / f"{job['request_hash']}.json")
        if receipt["request_hash"] != digest(receipt["request"]) or receipt["response_hash"] != digest(receipt["response"]):
            raise ValueError("pilot closure raw receipt hash differs")
        raw[job["request_hash"]] = receipt["response_hash"]
    budget = _read(directory / VERSION / "budget.json")
    if len(jobs) != budget["pilot_initial"] + budget["pilot_retry"] or budget["pilot_initial"] != 20 or budget["pilot_retry"] > 20:
        raise ValueError("pilot closure bounded terminal attempts differ")
    return jobs, raw


def _observe_pilot_closure(directory: Path, manifest: dict, report: dict) -> None:
    jobs, raw = _pilot_raw_snapshot(directory)
    base = directory / BASE_COHORT
    observation = _hashed({"version": "pilot-closure-observation-v1", "observer_kind": "assistant",
        "observed_at": datetime.now(UTC).isoformat(), "cohort_id": VERSION,
        "pilot_manifest_hash": manifest["manifest_hash"], "pilot_report_hash": report["report_hash"],
        "pilot_passed": report["passed"], "job_receipts_hash": digest(jobs), "raw_response_hashes": raw,
        "actual_pair_reservations": len(raw), "global_ledger": report["ledger_after_pilot"], "base_cohort": BASE_COHORT,
        "base_manifest_hash": manifest["base_manifest_hash"], "base_attempt_budget": _read(base / "attempt-budget.json"),
        "base_completed_families": len(list((base / "families").glob("*.json"))),
        "base_dataset_existed": (base / "dataset.json").exists(),
        "claim": "Local operator observed terminal pilot receipts at this global counter; this is not a remote timestamp attestation."}, "observation_hash")
    _seal(directory / VERSION / "closure-observation.json", observation)


def _verified_pilot_closure(directory: Path, manifest: dict, report: dict) -> dict:
    observation = _verified(directory / VERSION / "closure-observation.json", "observation_hash")
    jobs, raw = _pilot_raw_snapshot(directory)
    if (observation["cohort_id"] != VERSION or observation["pilot_manifest_hash"] != manifest["manifest_hash"]
            or observation["pilot_report_hash"] != report["report_hash"] or observation["pilot_passed"] != report["passed"]
            or observation["job_receipts_hash"] != digest(jobs) or observation["raw_response_hashes"] != raw
            or observation["actual_pair_reservations"] != len(raw)
            or observation["global_ledger"] != report["ledger_after_pilot"]
            or observation["base_manifest_hash"] != manifest["base_manifest_hash"]
            or type(observation["base_dataset_existed"]) is not bool
            or not manifest["ledger_before_pilot"]["pair"] + len(raw) <= observation["global_ledger"]["pair"]
            <= _read(directory / "provider-cache/usage.json")["pair"]):
        raise ValueError("pilot closure observation differs from raw jobs or cumulative counters")
    return observation


def _progress(family: dict, *, admitted: bool) -> None:
    print(json.dumps({"event": "pair_repair_family", "family_id": family["manifest"]["family_id"],
                      "swap_correct": sum(family["swap_audits"]), "swap_total": 3,
                      "decisive_pairs": sum(pair[2] in ("A", "B") for pair in family["pairs"]),
                      "quarantined": family["quarantined"], "admitted": admitted}), flush=True)


def _base_dataset(directory: Path, pilot_manifest: dict) -> dict:
    dataset = _verified(directory / BASE_COHORT / "dataset.json", "dataset_hash")
    if dataset["manifest_hash"] != pilot_manifest["base_manifest_hash"] or len(dataset["families"]) != 60:
        raise ValueError("repair requires the completed sealed v9 dataset")
    identifiers = {family["manifest"]["family_id"] for family in dataset["families"]}
    if identifiers != set(pilot_manifest["source_hashes"]):
        raise ValueError("base dataset family allocation changed")
    for family in dataset["families"]:
        if family["record_hash"] != digest({key: value for key, value in family.items() if key != "record_hash"}):
            raise ValueError("base dataset family hash mismatch")
        identifier = family["manifest"]["family_id"]
        source = _verified(directory / BASE_COHORT / "sources" / f"{identifier}.json", "record_hash")
        if (family["source_hash"] != source["record_hash"] or family["traits"] != source["traits"]
                or source["record_hash"] != pilot_manifest["source_hashes"][identifier]
                or family["manifest"] != source["manifest"]
                or family["vacancy"] != source["manifest"]["vacancy"]
                or len(family["pairs"]) != 21 or len(family["pair_answers"]) != 21
                or len(family["swap_answers"]) != 3 or len(family["swap_audits"]) != 3
                or len(family["grades"]) != 7):
            raise ValueError("base dataset differs from pinned source")
        original = _verified(directory / BASE_COHORT / "families" / f"{identifier}.json", "record_hash")
        if original != family:
            raise ValueError("base dataset differs from immutable family receipt")
        if identifier in pilot_manifest["pinned_base_family_audits"] and (
                family["record_hash"] != pilot_manifest["pinned_base_family_audits"][identifier]):
            raise ValueError("preregistered base reverse audit changed")
    return dataset


def _blind_nomination(directory: Path, dataset: dict, review_file: Path | None = None) -> dict:
    """Pin original blind disagreements as nomination only, never as teacher targets."""
    base_manifest, sources = _sources(directory, BASE_COHORT)
    path = review_file or directory / BASE_COHORT / "reviews" / "assistant-review.json"
    packet = _verified(directory / BASE_COHORT / "reviews" / "assistant-packet.json", "packet_hash")
    if packet != _packets(base_manifest, sources, BASE_COHORT)["assistant"]:
        raise ValueError("original blind nomination packet differs from fixed source selection")
    manual = _read(path)
    labels = _manual(manual, packet)
    families = {family["manifest"]["family_id"]: family for family in dataset["families"]}
    comparisons, nominated = [], set()
    for case in packet["cases"]:
        family = families[case["family_id"]]
        teacher = next(pair[2] for pair in family["pairs"] if pair[:2] == [case["a_index"], case["b_index"]])
        review = labels[case["pair_id"]]
        disagreement = review["label"] != teacher
        if disagreement and not _class_only(family["vacancy"]):
            nominated.add(case["family_id"])
        comparisons.append({"pair_id": case["pair_id"], "family_id": case["family_id"],
                            "disagreement": disagreement, "unresolved_factual_issue": review.get("factual_issue", False)})
    return {"review_path": str(path.resolve()), "review_hash": digest(manual), "packet_hash": packet["packet_hash"],
            "source_hashes": packet["source_hashes"], "reviewer_kind": "assistant", "comparisons": comparisons,
            "nonclass_disagreement_family_ids": sorted(nominated), "nomination_only": True,
            "human_authorship_verified": False, "teacher_receives_review_labels": False}


def _selection(directory: Path, dataset: dict, pilot: dict, review_file: Path | None = None) -> dict:
    nomination = _blind_nomination(directory, dataset, review_file)
    class_ids = sorted(family["manifest"]["family_id"] for family in dataset["families"] if _class_only(family["vacancy"]))
    quarantined = {family["manifest"]["family_id"] for family in dataset["families"]
                   if not _class_only(family["vacancy"]) and family["quarantined"]}
    disagreed = set(nomination["nonclass_disagreement_family_ids"])
    nonclass = sorted(quarantined | disagreed)
    if len(class_ids) != 16 or len(nonclass) > 9:
        blocked = _hashed({"state": "selection_over_capacity", "class_only": class_ids,
                           "quarantined_nonclass": sorted(quarantined), "selected_nonclass": nonclass,
                           "original_blind_nomination": nomination, "base_dataset_hash": dataset["dataset_hash"],
                           "activation_allowed": False}, "report_hash")
        _seal(directory / VERSION / "selection-blocked-report.json", blocked)
        raise ValueError("pair repair fixed selection exceeds 16 class-only plus 9 nominated non-class families")
    rows = []
    for family in dataset["families"]:
        identifier = family["manifest"]["family_id"]
        if identifier not in class_ids + nonclass:
            continue
        old = family["manifest"]["policy"]
        new = revised_policy(family["vacancy"], old)
        reasons = (["class_only_policy_applicability"] if identifier in class_ids else
                   (["quarantined_reverse_audits"] if identifier in quarantined else []) +
                   (["original_blind_disagreement"] if identifier in disagreed else []))
        rows.append({"family_id": identifier, "reason": "+".join(reasons), "reasons": reasons,
                     "policy_scope": "class_only_revision" if identifier in class_ids else "original_nonclass",
                     "policy_id": new["id"],
                     "original_record_hash": family["record_hash"], "old_policy_hash": digest(old),
                     "new_policy_hash": digest(new), "pair_policy": new})
    manifest = _hashed({"version": VERSION, "pilot_manifest_hash": pilot["manifest_hash"],
                       "base_dataset_hash": dataset["dataset_hash"], "selected": rows,
                       "original_blind_nomination": nomination,
                       "ledger_before": _budget_check(directory, sum(_read(directory / VERSION / "budget.json").values())),
                       "selection_uses_model_metrics": False}, "manifest_hash")
    path = directory / VERSION / "manifest.json"
    if path.exists():
        saved = _verified(path, "manifest_hash")
        if any(saved[key] != manifest[key] for key in ("pilot_manifest_hash", "base_dataset_hash", "selected", "original_blind_nomination")):
            raise ValueError("frozen family repair selection changed")
        return saved
    _seal(path, manifest)
    return manifest


def _quality(families: list[dict]) -> dict:
    correct = sum(sum(family["swap_audits"]) for family in families)
    quarantined = [family["manifest"]["family_id"] for family in families
                   if family["quarantined"] or sum(family["swap_audits"]) < 2]
    golden = [family for family in families if family["manifest"]["split"] == "golden"]
    counts = {family["manifest"]["family_id"]: sum(pair[2] in ("A", "B") for pair in family["pairs"]) for family in families}
    minimum = min(counts[family["manifest"]["family_id"]] for family in golden)
    by_split = {}
    for split in ("train", "validation", "calibration", "golden"):
        subset = [family for family in families if family["manifest"]["split"] == split]
        by_split[split] = {"families": len(subset), "swap_correct": sum(sum(family["swap_audits"]) for family in subset),
                           "swap_total": 3 * len(subset), "decisive_pairs": sum(counts[family["manifest"]["family_id"]] for family in subset)}
    by_policy_scope = {}
    for family in families:
        revision = family.get("pair_repair")
        scope = revision["policy_scope"] if revision else (
            "original_class_only_preserved" if _class_only(family["vacancy"]) else "original_nonclass")
        policy = revision["pair_policy"] if revision else family["manifest"]["policy"]
        group = by_policy_scope.setdefault(scope, {"families": 0, "swap_correct": 0, "swap_total": 0,
                                                  "policy_ids": [], "family_ids": []})
        group["families"] += 1
        group["swap_correct"] += sum(family["swap_audits"])
        group["swap_total"] += 3
        group["family_ids"].append(family["manifest"]["family_id"])
        group["policy_ids"] = sorted(set(group["policy_ids"]) | {policy["id"]})
    return {"swap_correct": correct, "swap_total": 180, "quarantined_families": quarantined,
            "golden_families": len(golden), "minimum_golden_decisive_pairs": minimum,
            "decisive_pairs_by_family": counts, "by_split": by_split, "by_policy_scope": by_policy_scope,
            "macro_reverse_accuracy": correct / 180,
            "golden_reverse_accuracy": by_split["golden"]["swap_correct"] / by_split["golden"]["swap_total"],
            "passed": correct >= 162 and not quarantined and len(golden) == 12 and minimum >= 6,
            "metric": "180 teacher reverse-orientation comparisons, not human agreement"}


def run_pair_repair(directory: Path, *, pilot_only: bool = False, original_review_file: Path | None = None) -> dict:
    """Run a preregistered pilot, then whole selected families; never train or activate."""
    target = directory / VERSION
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "repair-report.json").exists():
            _dataset, completed = validate_pair_repair(directory)
            return completed
        pilot_manifest = prepare_pilot(directory)
        revised_review_packets(directory, pilot_manifest)
        provider = PairRepairProviders(directory / "provider-cache")
        pilot = _pilot(directory, provider, pilot_manifest)
        if not pilot["passed"] or pilot_only:
            return {"state": "pilot_passed" if pilot["passed"] else "pilot_failed", "pilot": pilot,
                    "activation_allowed": False}
        base = _base_dataset(directory, pilot_manifest)
        try:
            selection = _selection(directory, base, pilot_manifest, original_review_file)
        except ValueError:
            if (target / "selection-blocked-report.json").exists():
                return _verified(target / "selection-blocked-report.json", "report_hash")
            raise
        selected = {row["family_id"]: row for row in selection["selected"]}
        families, failed = [], []
        for original in base["families"]:
            identifier = original["manifest"]["family_id"]
            if identifier not in selected:
                families.append(deepcopy(original))
                continue
            row = selected[identifier]
            path = target / "families" / f"{identifier}.json"
            if path.exists():
                saved_family = _verified(path, "record_hash")
                families.append(saved_family)
                _progress(saved_family, admitted=True)
                continue
            traits = original["traits"]

            def compare(pair, identifier=identifier, vacancy=original["vacancy"], traits=traits, policy=row["pair_policy"]):
                a, b = pair
                return _job(directory, provider, f"{identifier}-pair-{a}-{b}", vacancy,
                            traits[a], traits[b], policy, "family")

            try:
                answers = _parallel(compare, _PAIRS)
                reverse = _parallel(compare, [tuple(reversed(_PAIRS[index])) for index in _AUDIT_INDICES])
            except ValueError as error:
                if str(error).startswith(("fatal pair repair", "ambiguous started")):
                    raise
                failed.append(identifier)
                retained = deepcopy(original)
                retained["quarantined"] = True
                retained["pair_repair_failure"] = "Whole-family repair not admitted"
                retained.pop("record_hash")
                families.append(_hashed(retained, "record_hash"))
                _progress(retained, admitted=False)
                continue
            pairs = [[a, b, answer["label"]] for (a, b), answer in zip(_PAIRS, answers, strict=True)]
            audits = [reverse[position]["label"] == _REVERSE[answers[index]["label"]]
                      for position, index in enumerate(_AUDIT_INDICES)]
            repaired = {**deepcopy(original), "teacher": provider.teacher_identity(), "pairs": pairs,
                        "pair_answers": answers, "swap_answers": reverse, "swap_audits": audits,
                        "quarantined": sum(audits) < 2, "cyclic_components": _cycles(7, pairs),
                        "scalar_order_ceiling": _ordering_ceiling(7, pairs), "pair_repair": row,
                        "original_teacher": original["teacher"], "suitability_receipts_preserved": True}
            repaired.pop("record_hash")
            repaired = _hashed(repaired, "record_hash")
            _seal(path, repaired)
            families.append(repaired)
            _progress(repaired, admitted=True)
        quality = _quality(families)
        quality["passed"] = quality["passed"] and not failed
        derived = {**deepcopy(base), "version": VERSION, "families": families, "quality": quality,
                           "derived_from": {"cohort_id": BASE_COHORT, "dataset_hash": base["dataset_hash"],
                                            "manifest_hash": base["manifest_hash"]},
                           "pair_repair_manifest_hash": selection["manifest_hash"], "pair_repair_teacher": provider.teacher_identity(),
                           "pair_provenance": "Selected v4 pair repairs plus preserved unselected v2 pairs; all suitability v2 unchanged",
                           "activation_allowed": False, "training_started": False}
        derived.pop("dataset_hash")
        derived["dataset_hash"] = digest(derived)
        _seal(target / "dataset.json", derived)
        base_report = _verified(directory / BASE_COHORT / "report.json", "report_hash")
        ledger = _read(directory / "provider-cache/usage.json")
        report = _hashed({"version": VERSION, "state": "repaired_quality_passed" if quality["passed"] else "repair_incomplete_or_quality_failed",
                          "manifest_hash": selection["manifest_hash"], "pilot_manifest_hash": pilot_manifest["manifest_hash"],
                          "pilot_hash": pilot["report_hash"], "base_dataset_hash": base["dataset_hash"],
                          "base_report_hash": base_report["report_hash"], "dataset_hash": derived["dataset_hash"],
                          "selected_families": list(selected), "failed_families": failed, "quality": quality,
                          "original_blind_nomination": selection["original_blind_nomination"],
                          "post_repair_blind_review": {"state": "requires_new_policy_corrected_review",
                                                       "included_in_reverse_quality": False},
                          "previous_repair": pilot_manifest["previous_repair"], "prior_pair_attempts": PREVIOUS_ATTEMPTS,
                          "pilot_closure_observation_hash": _verified_pilot_closure(directory, pilot_manifest, pilot)["observation_hash"],
                          "ledger_before": base_report["ledger_after"], "ledger_before_pilot": pilot_manifest["ledger_before_pilot"],
                          "ledger_after": ledger, "budget": _read(target / "budget.json"), "generation_calls": 0, "grade_calls": 0,
                          "golden_model_predictions": 0, "training_started": False, "activation_allowed": False}, "report_hash")
        _seal(target / "repair-report.json", report)
    return report


class _CacheOnlyRepair(PairRepairProviders):
    """Validate exact raw receipts without HTTP, locks, credentials or cache writes."""

    def request(self, path: str, payload: dict, kind: str) -> dict:
        """Read only a hash-verified existing response for the exact approved request."""
        identity = self._identity(path, payload, kind)
        key = digest(identity)
        receipt = _read(self.directory / f"{key}.json")
        if (receipt.get("request") != identity or receipt.get("request_hash") != key
                or receipt.get("response_hash") != digest(receipt["response"])):
            raise ValueError("pair repair raw cache receipt differs")
        return receipt["response"]


def _replay_job(directory: Path, provider: _CacheOnlyRepair, job_id: str, vacancy: dict,
                a: dict, b: dict, policy: dict, category: str) -> dict:
    for attempt in (0, 1):
        path = directory / VERSION / "jobs" / f"{job_id}-{attempt}.json"
        if not path.is_file():
            raise ValueError("pair repair job missing")
        receipt = _read(path)
        identity = provider._identity("/v1/chat/completions", provider.pair_payload(vacancy, a, b, policy, attempt), "pair")
        if (receipt.get("request_hash") != digest(identity) or receipt.get("category") != category
                or receipt.get("attempt") != attempt or receipt.get("kind") != "pair"):
            raise ValueError("pair repair replay job differs")
        if receipt.get("state") == "failed":
            continue
        if receipt.get("state") != "passed":
            raise ValueError("pair repair replay job is unfinished")
        answer = provider.judge(vacancy, a, b, policy, attempt)
        if receipt.get("answer") != answer or receipt.get("answer_hash") != digest(answer):
            raise ValueError("pair repair replay answer differs")
        return answer
    raise ValueError("pair repair job has no admitted response")


def _verified_pilot(directory: Path, manifest: dict) -> dict:
    provider = _CacheOnlyRepair(directory / "provider-cache")
    report = _verified(directory / VERSION / "pilot-report.json", "report_hash")
    rows = []
    for case in manifest["cases"]:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            answer = _replay_job(directory, provider, f"pilot-{case['case_id']}-{int(swapped)}",
                                 case["vacancy"], a, b, case["policy"], "pilot")
            expected = _REVERSE[case["expected"]] if swapped else case["expected"]
            if answer["label"] != expected:
                raise ValueError("repair pilot machine gate failed")
            rows.append({"case_id": case["case_id"], "swapped": swapped, "answer": answer, "expected": expected, "error": None})
    if (report["rows"] != rows or report["pilot_manifest_hash"] != manifest["manifest_hash"]
            or not report["passed"] or report["valid"] != 20 or report["correct"] != 20 or report["swap_correct"] != 10):
        raise ValueError("repair pilot report differs from exact 20 orientations")
    _verified_pilot_closure(directory, manifest, report)
    return report


def validate_pair_repair(directory: Path, cohort_id: str = BASE_COHORT,
                         derived_dataset_path: Path | None = None,
                         repair_report_path: Path | None = None) -> tuple[dict, dict]:
    """Verify completed repair, pilot and raw pairs cache-only, returning dataset/report.

    directory is the shared artifact root. Paths, when provided, must identify
    this namespace's dataset.json and repair-report.json. Sources, features,
    independent grades, unchanged families and original blind selections are
    checked against the sealed base. This validator never grants activation.
    """
    if cohort_id != BASE_COHORT:
        raise ValueError("repair validator requires the pinned v9 cohort")
    target = directory / VERSION
    dataset_path = target / "dataset.json"
    report_path = target / "repair-report.json"
    if ((derived_dataset_path is not None and derived_dataset_path.resolve() != dataset_path.resolve())
            or (repair_report_path is not None and repair_report_path.resolve() != report_path.resolve())):
        raise ValueError("repair artifact paths must remain in the fixed repair namespace")
    pilot_manifest = _verified(target / "pilot-manifest.json", "manifest_hash")
    base_manifest, sources = _sources(directory, cohort_id)
    if (pilot_manifest["code_hash"] != digest(Path(__file__).read_bytes().hex())
            or pilot_manifest["base_manifest_hash"] != base_manifest["manifest_hash"]
            or pilot_manifest["source_hashes"] != {identifier: source["record_hash"] for identifier, source in sources.items()}
            or pilot_manifest["class_only_policy"] != CLASS_ONLY_POLICY):
        raise ValueError("repair pilot provenance changed")
    previous = _previous_pilot(directory)
    if (pilot_manifest["previous_repair"] != previous
            or previous["source_hashes"] != pilot_manifest["source_hashes"]
            or pilot_manifest["projected_prior_pair_attempts"] != PREVIOUS_ATTEMPTS):
        raise ValueError("repair previous 24 attempts provenance changed")
    provider = _CacheOnlyRepair(directory / "provider-cache")
    expected_cases, expected_requests = pilot_cases(), []
    for case in expected_cases:
        for swapped in (False, True):
            a, b = (case["B"], case["A"]) if swapped else (case["A"], case["B"])
            expected_requests.append(provider._identity("/v1/chat/completions",
                                     provider.pair_payload(case["vacancy"], a, b, case["policy"]), "pair"))
    if (pilot_manifest["cases"] != expected_cases or pilot_manifest["requests"] != expected_requests
            or pilot_manifest["teacher"] != provider.teacher_identity()
            or pilot_manifest["class_only_policy_hash"] != CLASS_ONLY_POLICY_HASH):
        raise ValueError("repair pilot differs from preregistered fixtures and identities")
    pilot = _verified_pilot(directory, pilot_manifest)
    base = _base_dataset(directory, pilot_manifest)
    selection = _verified(target / "manifest.json", "manifest_hash")
    nomination = _blind_nomination(directory, base, Path(selection["original_blind_nomination"]["review_path"]))
    expected_ids = {family["manifest"]["family_id"] for family in base["families"]
                    if _class_only(family["vacancy"]) or family["quarantined"]} | set(nomination["nonclass_disagreement_family_ids"])
    selected = {row["family_id"]: row for row in selection["selected"]}
    if (len(selection["selected"]) != len(selected) or set(selected) != expected_ids or len(selected) > 25
            or selection["base_dataset_hash"] != base["dataset_hash"]
            or selection["original_blind_nomination"] != nomination or selection["selection_uses_model_metrics"] is not False
            or selection["pilot_manifest_hash"] != pilot_manifest["manifest_hash"]):
        raise ValueError("repair selection violates the fixed input-only predicate")
    dataset = _verified(dataset_path, "dataset_hash")
    report = _verified(report_path, "report_hash")
    if (dataset["derived_from"] != {"cohort_id": BASE_COHORT, "dataset_hash": base["dataset_hash"], "manifest_hash": base["manifest_hash"]}
            or dataset["pair_repair_manifest_hash"] != selection["manifest_hash"]
            or report["dataset_hash"] != dataset["dataset_hash"] or report["base_dataset_hash"] != base["dataset_hash"]
            or report["manifest_hash"] != selection["manifest_hash"] or report["pilot_hash"] != pilot["report_hash"]
            or report["pilot_manifest_hash"] != pilot_manifest["manifest_hash"]):
        raise ValueError("derived dataset/report linkage changed")
    expected_families_out = []
    for original in base["families"]:
        identifier = original["manifest"]["family_id"]
        if identifier not in selected:
            expected_families_out.append(original)
            continue
        row = selected[identifier]
        policy = revised_policy(original["vacancy"], original["manifest"]["policy"])
        is_class = _class_only(original["vacancy"])
        reasons = (["class_only_policy_applicability"] if is_class else
                   (["quarantined_reverse_audits"] if original["quarantined"] else []) +
                   (["original_blind_disagreement"] if identifier in nomination["nonclass_disagreement_family_ids"] else []))
        expected_row = {"family_id": identifier, "reason": "+".join(reasons), "reasons": reasons,
                        "policy_scope": "class_only_revision" if is_class else "original_nonclass",
                        "policy_id": policy["id"], "original_record_hash": original["record_hash"],
                        "old_policy_hash": digest(original["manifest"]["policy"]),
                        "new_policy_hash": digest(policy), "pair_policy": policy}
        if row != expected_row:
            raise ValueError("repair policy semantic revision changed")
        if identifier in report["failed_families"]:
            retained = deepcopy(original)
            retained.update(quarantined=True, pair_repair_failure="Whole-family repair not admitted")
            retained.pop("record_hash")
            expected_families_out.append(_hashed(retained, "record_hash"))
            continue
        traits = original["traits"]
        answers = [_replay_job(directory, provider, f"{identifier}-pair-{a}-{b}", original["vacancy"],
                               traits[a], traits[b], policy, "family") for a, b in _PAIRS]
        reverse = [_replay_job(directory, provider, f"{identifier}-pair-{b}-{a}", original["vacancy"],
                               traits[b], traits[a], policy, "family") for a, b in (_PAIRS[index] for index in _AUDIT_INDICES)]
        pairs = [[a, b, answer["label"]] for (a, b), answer in zip(_PAIRS, answers, strict=True)]
        audits = [reverse[position]["label"] == _REVERSE[answers[index]["label"]] for position, index in enumerate(_AUDIT_INDICES)]
        repaired = {**deepcopy(original), "teacher": provider.teacher_identity(), "pairs": pairs, "pair_answers": answers,
                    "swap_answers": reverse, "swap_audits": audits, "quarantined": sum(audits) < 2,
                    "cyclic_components": _cycles(7, pairs), "scalar_order_ceiling": _ordering_ceiling(7, pairs),
                    "pair_repair": row, "original_teacher": original["teacher"], "suitability_receipts_preserved": True}
        repaired.pop("record_hash")
        expected_families_out.append(_hashed(repaired, "record_hash"))
    if dataset["families"] != expected_families_out:
        raise ValueError("repair changed preserved source/features/grades or admitted partial family")
    quality = _quality(expected_families_out)
    quality["passed"] = quality["passed"] and not report["failed_families"]
    base_report = _verified(directory / BASE_COHORT / "report.json", "report_hash")
    budget = _read(target / "budget.json")
    observed = dict.fromkeys(("pilot_initial", "pilot_retry", "family_initial", "family_retry"), 0)
    for path in (target / "jobs").glob("*.json"):
        receipt = _read(path)
        category, attempt = receipt.get("category"), receipt.get("attempt")
        if category not in ("pilot", "family") or type(attempt) is not int or attempt not in (0, 1):
            raise ValueError("repair budget job category or nonce differs")
        observed[f"{category}_{'retry' if attempt else 'initial'}"] += 1
    current_ledger = _read(directory / "provider-cache/usage.json")
    if (dataset["quality"] != quality or report["quality"] != quality
            or report["previous_repair"] != previous or report["prior_pair_attempts"] != PREVIOUS_ATTEMPTS
            or report["pilot_closure_observation_hash"] != _verified_pilot_closure(directory, pilot_manifest, pilot)["observation_hash"]
            or report["original_blind_nomination"] != nomination
            or report["post_repair_blind_review"] != {"state": "requires_new_policy_corrected_review", "included_in_reverse_quality": False}
            or report["base_report_hash"] != base_report["report_hash"] or report["ledger_before"] != base_report["ledger_after"]
            or report["ledger_after"] != current_ledger or report["budget"] != budget
            or budget != observed or sum(budget.values()) > MAX_ATTEMPTS
            or any(budget[field] > maximum for field, maximum in (
                ("pilot_initial", 20), ("pilot_retry", 20), ("family_initial", 600), ("family_retry", 32)))
            or current_ledger["pair"] > LIMITS["pair"]
            or report["activation_allowed"] or dataset["activation_allowed"]):
        raise ValueError("repair quality, cumulative budget or original ledger pins changed")
    for reviewer in ("assistant", "user"):
        original_packet = _verified(directory / BASE_COHORT / "reviews" / f"{reviewer}-packet.json", "packet_hash")
        revised = deepcopy(original_packet)
        revised.pop("packet_hash")
        revised.update(base_packet_hash=original_packet["packet_hash"], pair_repair_pilot_manifest_hash=pilot_manifest["manifest_hash"],
                       policy_revision={"version": CLASS_ONLY_POLICY["version"], "hash": CLASS_ONLY_POLICY_HASH})
        for case in revised["cases"]:
            if _class_only(case["vacancy"]):
                case["policy"] = revised_policy(case["vacancy"], case["policy"])
        revised["packet_hash"] = digest(revised)
        if _verified(target / "reviews" / f"{reviewer}-packet.json", "packet_hash") != revised:
            raise ValueError("policy-corrected blind packet changed original fixed selection")
    return dataset, report


def verify_repair_dataset(repair_directory: Path) -> dict:
    """Validate namespace/dataset/report cache-only; directory is the repair output folder."""
    if repair_directory.name != VERSION:
        raise ValueError("unexpected repair output directory")
    dataset, _report = validate_pair_repair(repair_directory.parent)
    return dataset


def main() -> None:
    """Run only the explicitly selected bounded repair; --pilot-only never relabels families."""
    parser = argparse.ArgumentParser(description="Bounded pair-only policy repair")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--pilot-only", action="store_true")
    parser.add_argument("--original-review-file", type=Path, help="Original assistant blind review; default v9/reviews/assistant-review.json")
    args = parser.parse_args()
    report = run_pair_repair(args.directory, pilot_only=args.pilot_only, original_review_file=args.original_review_file)
    print(json.dumps({"state": report["state"], "activation_allowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
