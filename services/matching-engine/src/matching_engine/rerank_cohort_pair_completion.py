"""Bounded qa-06 completion over immutable hybrid v3; no generation or grade calls."""

import argparse
import fcntl
import json
import re
import tempfile
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from matching_engine import rerank_cohort_pair_repair as parent
from matching_engine.rerank_cohort_provider import CohortProviders, source_citations
from matching_engine.rerank_cohort_review import _markdown
from matching_engine.rerank_experiment import _parallel
from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import LIMITS, _atomic_write

VERSION = "rerank-grounded-pair-repair-v4"
PARENT_REPAIR_NAMESPACE = parent.VERSION
ORACLE_RECEIPT_NAMESPACE = parent.VERSION
BASE_COHORT = parent.BASE_COHORT
oracle = parent.oracle
CLASS_ONLY_POLICY = parent.CLASS_ONLY_POLICY
CLASS_ONLY_POLICY_HASH = parent.CLASS_ONLY_POLICY_HASH
PAIR_PROMPT_VERSION = "rerank-policy-scoped-pair-v1"
FAMILY_ID = "qa-06"
ATTEMPT_LIMITS = {"pilot_initial": 8, "pilot_retry": 8, "family_initial": 24, "family_retry": 24}
MAX_ATTEMPTS = 64
_PRIOR_BUDGET = {"pilot_initial": 8, "pilot_retry": 0, "family_initial": 69, "family_retry": 1}
_PRIOR_ATTEMPTS = 78
_REASON = {"A": "prefer_known_facts", "B": "prefer_known_facts", "tie": "comparable_fit",
           "insufficient": "insufficient_source_facts"}
_SCOPE = (
    "POLICY-SCOPED PAIR CONTRACT v1. Preserve the original policy verbatim. Its first priority is declared fit "
    "to the STATED STACKS ONLY: python. Compare the two provided python levels before confirmation; "
    "greater declared depth wins. If the declared levels are equal, apply the original secondary priority "
    "to python's OWN confirmation: confirmed > partial > self_reported > pending. "
    "If both level and own confirmation are equal, tie. This accepted source has both known nonrejected "
    "python claims and no essential exclusive measurement. Never substitute grade, parent profile status, "
    "project references, domains, soft skills, salary, duration, role or prose for the stated stack criterion. "
    "The full raw profiles remain provenance/context, not additional preference criteria. "
    "Return compact JSON, with each evidence array EXACTLY the two provided own python tokens in the given order, "
    "each once. Root status_A/status_B copy their respective originals independently. "
    "A/B reason prefer_known_facts; tie reason comparable_fit; insufficient reason insufficient_source_facts. "
    "This contract applies identically to the initial and single repair request. "
)


def revised_policy(vacancy: dict, original: dict) -> dict:
    """Expose the frozen hybrid policy mapping; completion revises no policy."""
    return parent.revised_policy(vacancy, original)


def _read(path: Path) -> dict:
    return parent._read(path)


def _verified(path: Path, field: str) -> dict:
    return parent._verified(path, field)


def _seal(path: Path, value: dict) -> None:
    parent._seal(path, value)


def _hashed(value: dict, field: str) -> dict:
    return parent._hashed(value, field)


def _target(family: dict) -> None:
    policy, vacancy = family["manifest"]["policy"], family["vacancy"]
    if (family["manifest"]["family_id"] != FAMILY_ID or policy["id"] != "global-baseline-prior-3"
            or policy["ordered_priorities"] != ["Prefer declared fit to the stated stacks.",
                                                "Then prefer stronger confirmation of those matching skills."]
            or policy.get("exclusive_criterion", False) or policy.get("required_measurements", [])
            or vacancy.get("stacks") != {"python": 4}):
        raise ValueError("completion requires the exact qa-06 original stack policy")
    if any("python" not in trait["levels"] or trait["skill_statuses"].get("python") not in
           ("confirmed", "partial", "self_reported", "pending") for trait in family["traits"]):
        raise ValueError("qa-06 completion requires its accepted known nonrejected python claims")


def _refs(a: dict, b: dict) -> dict:
    citations = source_citations(a, b)
    return {side: [next(token for token, fact in citations.items() if fact["candidate"] == side and fact["field"] == field)
                   for field in ("levels.python", "skill_statuses.python")] for side in ("A", "B")}


class PairCompletionProviders(CohortProviders):
    """Scoped pair teacher with exact two-token evidence arrays, shared cache/slots/ledger."""

    def __init__(self, directory: Path, *, key_file: Path | None = None) -> None:
        """Bind a fresh namespace without reading credentials or resetting cumulative limits."""
        super().__init__(directory, cohort_id=VERSION, key_file=key_file)

    def _endpoint(self, kind: str) -> str:
        if kind != "pair":
            raise ValueError("pair completion forbids generation and grade")
        return super()._endpoint(kind)

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        identity = super()._identity(path, payload, kind)
        identity["prompt_version"] = PAIR_PROMPT_VERSION + ("-repair1" if identity["attempt"] else "")
        return identity

    def teacher_identity(self, kind: str = "pair", attempt: int = 0) -> dict:
        """Return the new pair protocol identity; no grade/generation identity is allowed."""
        self._endpoint(kind)
        value = super().teacher_identity(kind, attempt)
        value["prompt_version"] = PAIR_PROMPT_VERSION + ("-repair1" if attempt else "")
        return value

    def generation_payload(self, direction: str, family_id: str, keys: Sequence[str], count: int = 7,
                           attempt: int = 0) -> dict:
        """Reject profile generation in this completion operator."""
        raise ValueError("pair completion forbids generation")

    def suitability_payload(self, vacancy: dict, candidate: dict, policy: dict, attempt: int = 0) -> dict:
        """Reject grade requests; independent v2 grades remain unchanged."""
        raise ValueError("pair completion forbids grade")

    def pair_payload(self, vacancy: dict, a: dict, b: dict, policy: dict, attempt: int = 0) -> dict:
        """Keep raw inputs while constraining decision/evidence to the original stack policy."""
        _target({"manifest": {"family_id": FAMILY_ID, "policy": policy}, "vacancy": vacancy, "traits": [a, b]})
        payload = super().pair_payload(vacancy, a, b, policy, attempt)
        refs = _refs(a, b)
        state = json.loads(payload["messages"][1]["content"])
        state["available_citations"] = refs
        state["source_citations"] = {token: fact for token, fact in state["source_citations"].items()
                                     if token in refs[fact["candidate"]]}
        state["decision_inputs"] = {side: {"python": {"level": candidate["levels"]["python"],
             "own_status": candidate["skill_statuses"]["python"]}} for side, candidate in (("A", a), ("B", b))}
        payload["messages"][1]["content"] = json.dumps(state, ensure_ascii=False, sort_keys=True)
        payload["messages"][0]["content"] = _SCOPE + payload["messages"][0]["content"]
        schema = payload["response_format"]["json_schema"]["schema"]
        for side in ("A", "B"):
            # Enumerating the complete array structurally prevents duplicate/extra tokens.
            schema["properties"][f"evidence_{side}"] = {"type": "array", "enum": [refs[side]]}
        branches = []
        for label, reason in _REASON.items():
            branch = deepcopy(schema)
            branch["properties"]["label"]["enum"] = [label]
            branch["properties"]["reason_code"]["enum"] = [reason]
            branches.append(branch)
        payload["response_format"]["json_schema"]["schema"] = {"type": "object", "anyOf": branches}
        return payload

    def judge(self, vacancy: dict, a: dict, b: dict, policy: dict, attempt: int = 0) -> dict:
        """Require the exact own proof order; never deduplicate or repair a raw answer."""
        answer = super().judge(vacancy, a, b, policy, attempt)
        if any(answer[f"evidence_{side}"] != tokens for side, tokens in _refs(a, b).items()):
            raise ValueError("completion evidence differs from its two fixed own stack tokens")
        return answer


class _CacheOnlyCompletion(PairCompletionProviders):
    def request(self, path: str, payload: dict, kind: str) -> dict:
        """Read an exact immutable response without API, key reads or cache writes."""
        identity = self._identity(path, payload, kind)
        key = digest(identity)
        receipt = _read(self.directory / f"{key}.json")
        if (receipt["request"] != identity or receipt["request_hash"] != key
                or receipt["response_hash"] != digest(receipt["response"])):
            raise ValueError("completion raw receipt identity/hash changed")
        return receipt["response"]


def _parent(directory: Path) -> tuple[dict, dict, dict]:
    """Replay immutable v3 through a local frozen-ledger view and audit all 78 raw jobs."""
    target = directory / PARENT_REPAIR_NAMESPACE
    report = _verified(target / "repair-report.json", "report_hash")
    # Only this view substitutes historical usage metadata. Original artifacts/cache stay untouched.
    with tempfile.TemporaryDirectory(prefix="comit-v3-ledger-view-") as temporary:
        view = Path(temporary)
        for namespace in (BASE_COHORT, *parent.PRIOR_REPAIRS, PARENT_REPAIR_NAMESPACE):
            (view / namespace).symlink_to((directory / namespace).resolve(), target_is_directory=True)
        cache = view / "provider-cache"
        cache.mkdir()
        for path in (directory / "provider-cache").iterdir():
            if path.name != "usage.json":
                (cache / path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
        _atomic_write(cache / "usage.json", report["ledger_after"])
        dataset, checked = parent.validate_pair_repair(view)
    if (checked != report or report["failed_families"] != [FAMILY_ID]
            or report["quality"]["quarantined_families"] != [FAMILY_ID]
            or report["budget"] != _PRIOR_BUDGET or report["quality"]["passed"]):
        raise ValueError("completion requires the sealed v3 qa-06-only failure and exact 78 attempts")
    family = next(f for f in dataset["families"] if f["manifest"]["family_id"] == FAMILY_ID)
    _target(family)
    pilot = _verified(target / "pilot-manifest.json", "manifest_hash")
    selection = _verified(target / "manifest.json", "manifest_hash")
    selected = {row["family_id"]: row for row in selection["selected"]}
    families = {f["manifest"]["family_id"]: f for f in dataset["families"]}
    provider = parent._CacheOnlyRepair(directory / "provider-cache")
    jobs = {path.name: _read(path) for path in sorted((target / "jobs").glob("*.json"))}
    counts = dict.fromkeys(ATTEMPT_LIMITS, 0)
    raw = {}
    for name, job in jobs.items():
        pilot_match = re.fullmatch(r"pilot-(declared-[0-3])-([01])-([01])\.json", name)
        family_match = re.fullmatch(r"([a-z]+-\d\d)-pair-([0-6])-([0-6])-([01])\.json", name)
        if pilot_match:
            case_id, swap, nonce = pilot_match.groups()
            case = next(case for case in pilot["cases"] if case["case_id"] == case_id)
            a, b = (case["B"], case["A"]) if swap == "1" else (case["A"], case["B"])
            vacancy, policy, category = case["vacancy"], case["policy"], "pilot"
        elif family_match:
            identifier, left, right, nonce = family_match.groups()
            if identifier not in selected or selected[identifier]["label_source"] != "llm_pair_teacher":
                raise ValueError("unexpected v3 remote family")
            f = families[identifier]
            a, b = f["traits"][int(left)], f["traits"][int(right)]
            vacancy, policy, category = f["vacancy"], selected[identifier]["pair_policy"], "family"
        else:
            raise ValueError("unexpected v3 raw job id")
        attempt = int(nonce)
        if attempt and jobs.get(name[:-6] + "0.json", {}).get("state") != "failed":
            raise ValueError("v3 retry lacks its failed initial attempt")
        identity = provider._identity("/v1/chat/completions", provider.pair_payload(vacancy, a, b, policy, attempt), "pair")
        key = digest(identity)
        if (job.get("request_hash") != key or job.get("category") != category or job.get("attempt") != attempt
                or job.get("kind") != "pair" or job.get("state") not in ("passed", "failed")):
            raise ValueError("v3 raw job identity/state differs")
        receipt = _read(directory / "provider-cache" / f"{key}.json")
        if (receipt["request"] != identity or receipt["request_hash"] != key
                or receipt["response_hash"] != digest(receipt["response"])):
            raise ValueError("v3 raw response changed")
        try:
            answer = provider.judge(vacancy, a, b, policy, attempt)
        except (ValueError, TypeError, KeyError):
            if job["state"] != "failed":
                raise ValueError("v3 passed job has invalid raw response") from None
        else:
            if job["state"] != "passed" or job.get("answer") != answer or job.get("answer_hash") != digest(answer):
                raise ValueError("v3 job raw validation differs from sealed state")
        counts[f"{category}_{'retry' if attempt else 'initial'}"] += 1
        raw[key] = receipt["response_hash"]
    if counts != _PRIOR_BUDGET or len(jobs) != _PRIOR_ATTEMPTS or len(raw) != _PRIOR_ATTEMPTS:
        raise ValueError("v3 raw accounting differs from its exact 78 charges")
    pins = {"namespace": PARENT_REPAIR_NAMESPACE, "dataset_hash": dataset["dataset_hash"],
            "report_hash": report["report_hash"], "ledger_after": report["ledger_after"],
            "actual_pair_reservations": _PRIOR_ATTEMPTS, "raw_jobs_hash": digest(jobs),
            "raw_response_hashes": raw, "code_hash": pilot["code_hash"], "oracle_code_hash": pilot["oracle_code_hash"]}
    return dataset, report, pins


def pilot_cases(family: dict) -> list[dict]:
    """Freeze four raw qa pairs with hand-derived labels, never prior/model predictions."""
    _target(family)
    specs = [(0, 1, "tie", (2, "confirmed", 2, "confirmed")),
             (1, 3, "tie", (2, "confirmed", 2, "confirmed")),
             (3, 5, "A", (2, "confirmed", 1, "pending")),
             (2, 6, "B", (1, "self_reported", 2, "confirmed"))]
    result = []
    for index, (left, right, expected, inputs) in enumerate(specs):
        a, b = family["traits"][left], family["traits"][right]
        if (a["levels"]["python"], a["skill_statuses"]["python"],
                b["levels"]["python"], b["skill_statuses"]["python"]) != inputs:
            raise ValueError("preregistered qa-06 pilot raw facts differ")
        result.append({"case_id": f"qa-{index}", "a_index": left, "b_index": right, "expected": expected,
                       "A": a, "B": b, "vacancy": family["vacancy"], "policy": family["manifest"]["policy"]})
    return result


def prepare_completion(directory: Path) -> dict:
    """Freeze parent/raw/source hashes, whole-family selection and eight requests before HTTP."""
    dataset, report, pins = _parent(directory)
    provider = PairCompletionProviders(directory / "provider-cache")
    family = next(f for f in dataset["families"] if f["manifest"]["family_id"] == FAMILY_ID)
    cases = pilot_cases(family)
    requests = [provider._identity("/v1/chat/completions", provider.pair_payload(case["vacancy"], a, b, case["policy"]), "pair")
                for case in cases for a, b in ((case["A"], case["B"]), (case["B"], case["A"]))]
    path = directory / VERSION / "pilot-manifest.json"
    if path.exists():
        saved = _verified(path, "manifest_hash")
        if (saved["parent_repair"] != pins or saved["cases"] != cases or saved["requests"] != requests
                or saved["code_hash"] != digest(Path(__file__).read_bytes().hex())):
            raise ValueError("frozen completion input or code changed")
        return saved
    ledger = _read(directory / "provider-cache/usage.json")
    if ledger != report["ledger_after"] or ledger["pair"] + MAX_ATTEMPTS > LIMITS["pair"]:
        raise ValueError("completion ledger baseline/cap differs; never reset or absorb unknown usage")
    manifest = _hashed({"version": VERSION, "parent_repair": pins, "base_cohort": BASE_COHORT,
        "selected_families": [FAMILY_ID], "selection_reason": "Whole sole quarantined v3 family; duplicated raw evidence rejected on both attempts",
        "selection_uses_model_metrics": False, "source_hash": family["source_hash"],
        "source_hashes": {f["manifest"]["family_id"]: f["source_hash"] for f in dataset["families"]},
        "original_policy": family["manifest"]["policy"], "policy_hash": digest(family["manifest"]["policy"]),
        "cases": cases, "requests": requests, "expected_source": "Hand-derived stated python level then own confirmation; no prior teacher or model targets",
        "maximum_attempts": MAX_ATTEMPTS, "attempt_limits": ATTEMPT_LIMITS, "ledger_before_pilot": ledger,
        "code_hash": digest(Path(__file__).read_bytes().hex()), "teacher": provider.teacher_identity(),
        "oracle_code_hash": pins["oracle_code_hash"]}, "manifest_hash")
    _seal(path, manifest)
    return manifest


def _budget_check(directory: Path, manifest: dict, budget: dict) -> None:
    if (type(budget) is not dict or set(budget) != set(ATTEMPT_LIMITS)
            or any(type(budget[key]) is not int or not 0 <= budget[key] <= maximum for key, maximum in ATTEMPT_LIMITS.items())):
        raise ValueError("completion budget requires exact nonnegative integer counts")
    ledger = _read(directory / "provider-cache/usage.json")
    before = manifest["ledger_before_pilot"]
    if (ledger["pair"] < before["pair"] or ledger["generation"] != before["generation"]
            or ledger["grade"] != before["grade"] or ledger["pair"] + MAX_ATTEMPTS - sum(budget.values()) > LIMITS["pair"]):
        raise ValueError("completion cumulative ledger cannot reserve its remaining allowance")


def _job(directory: Path, provider: PairCompletionProviders, manifest: dict, job_id: str, case: dict, category: str) -> dict:
    target = directory / VERSION
    folder = target / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f".{job_id}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for attempt in (0, 1):
            payload = provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
            key = digest(provider._identity("/v1/chat/completions", payload, "pair"))
            path = folder / f"{job_id}-{attempt}.json"
            cache = directory / "provider-cache" / f"{key}.json"
            if path.exists():
                old = _read(path)
                if old["request_hash"] != key:
                    raise ValueError("completion job identity changed")
                if old["state"] == "failed":
                    if old.get("fatal_http_statuses"):
                        raise ValueError("fatal completion HTTP failure")
                    continue
                if not cache.exists():
                    raise ValueError("ambiguous started completion requires operator review")
                if old["state"] == "passed":
                    answer = provider.judge(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
                    if old["answer"] != answer or old["answer_hash"] != digest(answer):
                        raise ValueError("completion cached answer changed")
                    return answer
            else:
                with (target / ".budget.lock").open("a+") as budget_lock:
                    fcntl.flock(budget_lock, fcntl.LOCK_EX)
                    budget_path = target / "budget.json"
                    budget = _read(budget_path) if budget_path.exists() else dict.fromkeys(ATTEMPT_LIMITS, 0)
                    _budget_check(directory, manifest, budget)
                    field = f"{category}_{'retry' if attempt else 'initial'}"
                    if budget[field] >= ATTEMPT_LIMITS[field]:
                        raise ValueError("completion bounded allowance exhausted")
                    budget[field] += 1
                    _atomic_write(budget_path, budget)
                    _seal(path, {"state": "started", "request_hash": key, "category": category, "kind": "pair", "attempt": attempt})
            try:
                answer = provider.judge(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
            except (ValueError, TypeError, KeyError, RuntimeError) as error:
                statuses = [int(_read(failure).get("http_status", 0)) for failure in provider.directory.glob(f"{key}.failure-*.json")]
                fatal = [status for status in statuses if status in (400, 401, 403, 404, 422)]
                if not cache.exists() and not any(statuses):
                    raise ValueError("ambiguous started completion requires operator review") from None
                _atomic_write(path, {"state": "failed", "request_hash": key, "category": category, "kind": "pair",
                    "attempt": attempt, "error_type": type(error).__name__, "fatal_http_statuses": fatal})
                if fatal:
                    raise ValueError("fatal completion HTTP failure") from None
                continue
            _atomic_write(path, {"state": "passed", "request_hash": key, "category": category, "kind": "pair", "attempt": attempt,
                "answer": answer, "answer_hash": digest(answer)})
            return answer
    raise ValueError("completion response invalid after one bounded repair")


def _replay_job(directory: Path, provider: _CacheOnlyCompletion, job_id: str, case: dict, category: str) -> dict:
    for attempt in (0, 1):
        path = directory / VERSION / "jobs" / f"{job_id}-{attempt}.json"
        job = _read(path)
        identity = provider._identity("/v1/chat/completions", provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt), "pair")
        if job.get("request_hash") != digest(identity) or job.get("category") != category or job.get("kind") != "pair" or job.get("attempt") != attempt:
            raise ValueError("completion replay job metadata changed")
        provider.request("/v1/chat/completions", provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt), "pair")
        try:
            answer = provider.judge(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
        except (ValueError, TypeError, KeyError):
            if job["state"] != "failed":
                raise ValueError("completion invalid raw response is not recorded failed") from None
            continue
        if job["state"] != "passed" or job.get("answer") != answer or job.get("answer_hash") != digest(answer):
            raise ValueError("completion raw answer differs from receipt")
        return answer
    raise ValueError("completion response invalid after one bounded repair")


def _orientations(cases: list[dict]) -> list[tuple[str, dict, str]]:
    return [(f"pilot-{case['case_id']}-{swap}", {**case, "A": b if swap else a, "B": a if swap else b},
             parent._REVERSE[case["expected"]] if swap else case["expected"])
            for case in cases for a, b in ((case["A"], case["B"]),) for swap in (0, 1)]


def _pilot(directory: Path, provider: PairCompletionProviders, manifest: dict, *, replay: bool = False) -> dict:
    def compare(item):
        job_id, case, expected = item
        try:
            answer = (_replay_job(directory, provider, job_id, case, "pilot") if replay else
                      _job(directory, provider, manifest, job_id, case, "pilot"))
            error = None
        except ValueError as exception:
            if not str(exception).startswith("completion response invalid"):
                raise
            answer, error = None, str(exception)
        return {"job_id": job_id, "answer": answer, "expected": expected, "error": error}
    rows = [compare(item) for item in _orientations(manifest["cases"])] if replay else _parallel(compare, _orientations(manifest["cases"]))
    valid = sum(row["answer"] is not None for row in rows)
    correct = sum(row["answer"] is not None and row["answer"]["label"] == row["expected"] for row in rows)
    swaps = sum(rows[index]["answer"] is not None and rows[index+1]["answer"] is not None and
                rows[index+1]["answer"]["label"] == parent._REVERSE[rows[index]["answer"]["label"]] for index in range(0, 8, 2))
    value = {"pilot_manifest_hash": manifest["manifest_hash"], "rows": rows, "valid": valid, "correct": correct,
             "swap_correct": swaps, "passed": valid == correct == 8 and swaps == 4}
    path = directory / VERSION / "pilot-report.json"
    if replay:
        saved = _verified(path, "report_hash")
        if any(saved[key] != item for key, item in value.items()):
            raise ValueError("completion pilot report changed")
        return saved
    value["ledger_after_pilot"] = _read(directory / "provider-cache/usage.json")
    value = _hashed(value, "report_hash")
    _seal(path, value)
    _seal(directory / VERSION / "closure-observation.json", _closure(directory, manifest, value))
    return value


def _job_snapshot(directory: Path, *, pilot_only: bool = False) -> tuple[dict, dict]:
    paths = (directory / VERSION / "jobs").glob("pilot-*.json" if pilot_only else "*.json")
    jobs = {path.name: _read(path) for path in sorted(paths)}
    raw = {}
    for job in jobs.values():
        if job.get("state") not in ("passed", "failed"):
            raise ValueError("completion closure requires terminal raw jobs")
        receipt = _read(directory / "provider-cache" / f"{job['request_hash']}.json")
        if receipt["request_hash"] != digest(receipt["request"]) or receipt["response_hash"] != digest(receipt["response"]):
            raise ValueError("completion closure raw receipt hash differs")
        raw[job["request_hash"]] = receipt["response_hash"]
    return jobs, raw


def _closure(directory: Path, manifest: dict, pilot: dict) -> dict:
    jobs, raw = _job_snapshot(directory, pilot_only=True)
    return _hashed({"version": "pilot-closure-observation-v1", "observer_kind": "assistant",
        "observed_at": datetime.now(UTC).isoformat(), "cohort_id": VERSION, "pilot_manifest_hash": manifest["manifest_hash"],
        "pilot_report_hash": pilot["report_hash"], "pilot_passed": pilot["passed"], "job_receipts_hash": digest(jobs),
        "raw_response_hashes": raw, "actual_pair_reservations": len(raw), "global_ledger": pilot["ledger_after_pilot"],
        "base_cohort": BASE_COHORT, "base_manifest_hash": _verified(directory / BASE_COHORT / "manifest.json", "manifest_hash")["manifest_hash"],
        "base_attempt_budget": _read(directory / BASE_COHORT / "attempt-budget.json"), "base_completed_families": 60,
        "base_dataset_existed": True, "claim": "Local terminal receipt/counter observation, not remote timestamp attestation."}, "observation_hash")


def _verified_closure(directory: Path, manifest: dict, pilot: dict) -> dict:
    saved = _verified(directory / VERSION / "closure-observation.json", "observation_hash")
    expected = _closure(directory, manifest, pilot)
    if ({key: value for key, value in saved.items() if key not in ("observed_at", "observation_hash")} !=
            {key: value for key, value in expected.items() if key not in ("observed_at", "observation_hash")}
            or type(saved.get("observed_at")) is not str):
        raise ValueError("completion pilot closure differs from terminal raw receipts")
    return saved


def _family_cases(family: dict) -> list[tuple[str, dict]]:
    indices = [*parent._PAIRS, *[tuple(reversed(parent._PAIRS[index])) for index in parent._AUDIT_INDICES]]
    return [(f"{FAMILY_ID}-pair-{left}-{right}", {"vacancy": family["vacancy"], "policy": family["manifest"]["policy"],
             "A": family["traits"][left], "B": family["traits"][right]}) for left, right in indices]


def _derived_family(original: dict, answers: list[dict], teacher: dict, manifest: dict) -> dict:
    pairs = [[left, right, answer["label"]] for (left, right), answer in zip(parent._PAIRS, answers[:21], strict=True)]
    reverse = answers[21:]
    audits = [reverse[position]["label"] == parent._REVERSE[answers[index]["label"]] for position, index in enumerate(parent._AUDIT_INDICES)]
    result = {**deepcopy(original), "teacher": teacher, "pairs": pairs, "pair_answers": answers[:21],
        "swap_answers": reverse, "swap_audits": audits, "quarantined": sum(audits) < 2,
        "cyclic_components": parent._cycles(7, pairs), "scalar_order_ceiling": parent._ordering_ceiling(7, pairs),
        "pair_completion": {"namespace": VERSION, "manifest_hash": manifest["manifest_hash"], "label_source": "llm_pair_teacher",
            "original_record_hash": original["record_hash"], "original_policy_hash": digest(original["manifest"]["policy"])},
        "original_teacher": original["teacher"], "suitability_receipts_preserved": True}
    result.pop("pair_repair_failure", None)
    result.pop("record_hash")
    return _hashed(result, "record_hash")


def _review_packets(directory: Path, manifest: dict) -> None:
    for reviewer in ("assistant", "user"):
        old = _verified(directory / PARENT_REPAIR_NAMESPACE / "reviews" / f"{reviewer}-packet.json", "packet_hash")
        value = deepcopy(old)
        value.pop("packet_hash")
        value["pair_repair_pilot_manifest_hash"] = manifest["manifest_hash"]
        value = _hashed(value, "packet_hash")
        path = directory / VERSION / "reviews" / f"{reviewer}-packet.json"
        _seal(path, value)
        md_path = path.with_suffix(".md")
        text = _markdown(value)
        if md_path.exists() and md_path.read_text(encoding="utf-8") != text:
            raise ValueError("completion immutable review Markdown changed")
        if not md_path.exists():
            md_path.write_text(text, encoding="utf-8")


def run_pair_completion(directory: Path, *, pilot_only: bool = False) -> dict:
    """Run the preregistered pilot and whole qa family; preserve v3 artifacts and all grades."""
    target = directory / VERSION
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "repair-report.json").exists():
            return validate_pair_completion(directory)[1]
        manifest = prepare_completion(directory)
        _review_packets(directory, manifest)
        provider = PairCompletionProviders(directory / "provider-cache")
        pilot = (_pilot(directory, _CacheOnlyCompletion(directory / "provider-cache"), manifest, replay=True)
                 if (target / "pilot-report.json").exists() else _pilot(directory, provider, manifest))
        if pilot_only or not pilot["passed"]:
            return {"state": "pilot_passed" if pilot["passed"] else "pilot_failed", "pilot": pilot, "activation_allowed": False}
        closure = _verified_closure(directory, manifest, pilot)
        original, original_report, pins = _parent(directory)
        if pins != manifest["parent_repair"]:
            raise ValueError("parent changed after completion pilot freeze")
        family = next(f for f in original["families"] if f["manifest"]["family_id"] == FAMILY_ID)
        failed = []
        try:
            answers = _parallel(lambda item: _job(directory, provider, manifest, item[0], item[1], "family"), _family_cases(family))
        except ValueError as error:
            if not str(error).startswith("completion response invalid"):
                raise
            failed = [FAMILY_ID]
            completed = family
        else:
            completed = _derived_family(family, answers, provider.teacher_identity(), manifest)
            _seal(target / "families" / f"{FAMILY_ID}.json", completed)
        families = [completed if f["manifest"]["family_id"] == FAMILY_ID else deepcopy(f) for f in original["families"]]
        quality = parent._quality(families)
        quality["passed"] = quality["passed"] and not failed
        dataset = {**deepcopy(original), "version": VERSION, "families": families, "quality": quality,
            "completion_parent": pins, "pair_repair_manifest_hash": manifest["manifest_hash"],
            "pair_repair_teacher": provider.teacher_identity(), "pair_provenance": "v3 hybrid unchanged except whole qa-06 policy-scoped teacher completion",
            "activation_allowed": False, "training_started": False}
        dataset.pop("dataset_hash")
        dataset = _hashed(dataset, "dataset_hash")
        _seal(target / "dataset.json", dataset)
        jobs, raw = _job_snapshot(directory)
        report = _hashed({"version": VERSION, "state": "repaired_quality_passed" if quality["passed"] else "repair_incomplete_or_quality_failed",
            "dataset_hash": dataset["dataset_hash"], "manifest_hash": manifest["manifest_hash"], "pilot_manifest_hash": manifest["manifest_hash"],
            "pilot_hash": pilot["report_hash"], "pilot_closure_observation_hash": closure["observation_hash"],
            "actual_pair_reservations": len(raw), "raw_jobs_hash": digest(jobs), "raw_response_hashes": raw,
            "cached_pair_reservations": len(jobs) - len(raw),
            "parent_repair": pins, "selected_families": [FAMILY_ID], "failed_families": failed,
            "base_dataset_hash": original_report["base_dataset_hash"], "base_report_hash": original_report["base_report_hash"],
            "original_blind_nomination": original_report["original_blind_nomination"], "quality": quality,
            "source_metrics": original["source_metrics"], "oracle_code_hash": original["oracle_code_hash"],
            "previous_repair": original_report["previous_repair"], "prior_pair_attempts": parent.PREVIOUS_ATTEMPTS,
            "ledger_before": original_report["ledger_before"], "ledger_before_pilot": manifest["ledger_before_pilot"],
            "ledger_after": _read(directory / "provider-cache/usage.json"), "budget": _read(target / "budget.json"),
            "generation_calls": 0, "grade_calls": 0, "golden_model_predictions": 0, "training_started": False,
            "post_repair_blind_review": {"state": "requires_new_policy_corrected_review", "included_in_reverse_quality": False},
            "activation_allowed": False}, "report_hash")
        _seal(target / "repair-report.json", report)
        parent._progress(completed, admitted=not failed)
    return report


def validate_pair_completion(directory: Path) -> tuple[dict, dict]:
    """Replay v3 plus every continuation response cache-only and return verified dataset/report."""
    original, old_report, pins = _parent(directory)
    target = directory / VERSION
    manifest = _verified(target / "pilot-manifest.json", "manifest_hash")
    if (manifest["parent_repair"] != pins or manifest["code_hash"] != digest(Path(__file__).read_bytes().hex())
            or manifest["selected_families"] != [FAMILY_ID] or manifest["selection_uses_model_metrics"] is not False
            or manifest["maximum_attempts"] != MAX_ATTEMPTS or manifest["attempt_limits"] != ATTEMPT_LIMITS):
        raise ValueError("completion immutable provenance or scope changed")
    family = next(f for f in original["families"] if f["manifest"]["family_id"] == FAMILY_ID)
    cases = pilot_cases(family)
    provider = _CacheOnlyCompletion(directory / "provider-cache")
    expected_requests = [provider._identity("/v1/chat/completions", provider.pair_payload(case["vacancy"], a, b, case["policy"]), "pair")
                         for case in cases for a, b in ((case["A"], case["B"]), (case["B"], case["A"]))]
    if (manifest["cases"] != cases or manifest["requests"] != expected_requests or manifest["teacher"] != provider.teacher_identity()
            or manifest["source_hash"] != family["source_hash"] or manifest["original_policy"] != family["manifest"]["policy"]
            or manifest["policy_hash"] != digest(family["manifest"]["policy"]) or manifest["ledger_before_pilot"] != pins["ledger_after"]
            or manifest["oracle_code_hash"] != original["oracle_code_hash"]
            or manifest["source_hashes"] != {f["manifest"]["family_id"]: f["source_hash"] for f in original["families"]}):
        raise ValueError("completion request/source/policy changed")
    pilot = _pilot(directory, provider, manifest, replay=True)
    if not pilot["passed"]:
        raise ValueError("completion pilot gate failed")
    closure = _verified_closure(directory, manifest, pilot)
    dataset, report = _verified(target / "dataset.json", "dataset_hash"), _verified(target / "repair-report.json", "report_hash")
    if report["failed_families"] == [FAMILY_ID]:
        completed = family
    elif not report["failed_families"]:
        answers = [_replay_job(directory, provider, job_id, case, "family") for job_id, case in _family_cases(family)]
        completed = _derived_family(family, answers, provider.teacher_identity(), manifest)
        if _verified(target / "families" / f"{FAMILY_ID}.json", "record_hash") != completed:
            raise ValueError("completion family receipt changed")
    else:
        raise ValueError("completion cannot fail or change any other family")
    families = [completed if f["manifest"]["family_id"] == FAMILY_ID else f for f in original["families"]]
    quality = parent._quality(families)
    quality["passed"] = quality["passed"] and not report["failed_families"]
    expected = {**deepcopy(original), "version": VERSION, "families": families, "quality": quality,
        "completion_parent": pins, "pair_repair_manifest_hash": manifest["manifest_hash"],
        "pair_repair_teacher": provider.teacher_identity(), "pair_provenance": "v3 hybrid unchanged except whole qa-06 policy-scoped teacher completion",
        "activation_allowed": False, "training_started": False}
    expected.pop("dataset_hash")
    if dataset != _hashed(expected, "dataset_hash"):
        raise ValueError("completion altered unselected families, facts, grades, features or pair provenance")
    budget = _read(target / "budget.json")
    counts = dict.fromkeys(ATTEMPT_LIMITS, 0)
    jobs = {path.stem: _read(path) for path in (target / "jobs").glob("*.json")}
    allowed_jobs = {name: (case, "pilot") for name, case, _expected in _orientations(cases)}
    allowed_jobs.update({name: (case, "family") for name, case in _family_cases(family)})
    raw = {}
    for name, job in jobs.items():
        base_name, nonce = name.rsplit("-", 1)
        if base_name not in allowed_jobs or nonce not in ("0", "1"):
            raise ValueError("unexpected completion job")
        case, category = allowed_jobs[base_name]
        attempt = int(nonce)
        if attempt and jobs.get(base_name + "-0", {}).get("state") != "failed":
            raise ValueError("completion retry lacks its failed initial attempt")
        identity = provider._identity("/v1/chat/completions", provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt), "pair")
        if (job.get("request_hash") != digest(identity) or job.get("category") != category or job.get("kind") != "pair"
                or type(job.get("attempt")) is not int or job.get("attempt") != attempt or job.get("state") not in ("passed", "failed")):
            raise ValueError("completion raw job identity/state changed")
        provider.request("/v1/chat/completions", provider.pair_payload(case["vacancy"], case["A"], case["B"], case["policy"], attempt), "pair")
        try:
            answer = provider.judge(case["vacancy"], case["A"], case["B"], case["policy"], attempt)
        except (ValueError, TypeError, KeyError):
            if job["state"] != "failed":
                raise ValueError("completion passed response no longer validates") from None
        else:
            if job["state"] != "passed" or job.get("answer") != answer or job.get("answer_hash") != digest(answer):
                raise ValueError("completion raw admission changed")
        counts[f"{category}_{'retry' if attempt else 'initial'}"] += 1
        raw[job["request_hash"]] = _read(directory / "provider-cache" / f"{job['request_hash']}.json")["response_hash"]
    ledger = {**pins["ledger_after"], "pair": pins["ledger_after"]["pair"] + len(raw)}
    if (type(budget) is not dict or set(budget) != set(ATTEMPT_LIMITS)
            or any(type(value) is not int or value < 0 for value in budget.values())
            or counts != budget or sum(budget.values()) > MAX_ATTEMPTS
            or any(budget[key] > value for key, value in ATTEMPT_LIMITS.items())
            or pilot["ledger_after_pilot"] != {**pins["ledger_after"], "pair": pins["ledger_after"]["pair"] +
                len({job["request_hash"] for job in jobs.values() if job["category"] == "pilot"})}
            or report["ledger_after"] != ledger or _read(directory / "provider-cache/usage.json") != ledger
            or report["ledger_before"] != old_report["ledger_before"] or report["ledger_before_pilot"] != pins["ledger_after"]
            or report["parent_repair"] != pins or report["budget"] != budget or report["quality"] != quality
            or report["source_metrics"] != original["source_metrics"] or report["oracle_code_hash"] != original["oracle_code_hash"]
            or report["base_dataset_hash"] != old_report["base_dataset_hash"] or report["base_report_hash"] != old_report["base_report_hash"]
            or report["dataset_hash"] != dataset["dataset_hash"] or report["manifest_hash"] != manifest["manifest_hash"]
            or report["pilot_manifest_hash"] != manifest["manifest_hash"] or report["pilot_hash"] != pilot["report_hash"]
            or report["version"] != VERSION or report["state"] != ("repaired_quality_passed" if quality["passed"] else "repair_incomplete_or_quality_failed")
            or report["selected_families"] != [FAMILY_ID] or report["previous_repair"] != old_report["previous_repair"]
            or report["prior_pair_attempts"] != parent.PREVIOUS_ATTEMPTS or report["original_blind_nomination"] != old_report["original_blind_nomination"]
            or report["pilot_closure_observation_hash"] != closure["observation_hash"]
            or report["raw_jobs_hash"] != digest({path.name: _read(path) for path in (target / "jobs").glob("*.json")})
            or report["actual_pair_reservations"] != len(raw) or report["cached_pair_reservations"] != len(jobs) - len(raw)
            or report["raw_response_hashes"] != raw
            or any(type(report[field]) is not int or report[field] != 0 for field in ("generation_calls", "grade_calls", "golden_model_predictions"))
            or report["training_started"] is not False or dataset["training_started"] is not False
            or report["activation_allowed"] is not False or dataset["activation_allowed"] is not False
            or report["post_repair_blind_review"] != {"state": "requires_new_policy_corrected_review", "included_in_reverse_quality": False}):
        raise ValueError("completion quality, lineage or cumulative accounting changed")
    for reviewer in ("assistant", "user"):
        packet = deepcopy(_verified(directory / PARENT_REPAIR_NAMESPACE / "reviews" / f"{reviewer}-packet.json", "packet_hash"))
        packet.pop("packet_hash")
        packet["pair_repair_pilot_manifest_hash"] = manifest["manifest_hash"]
        if _verified(target / "reviews" / f"{reviewer}-packet.json", "packet_hash") != _hashed(packet, "packet_hash"):
            raise ValueError("completion changed fixed blind cases, facts or policies")
    return dataset, report


def validate_pair_repair(directory: Path, cohort_id: str = BASE_COHORT,
                         derived_dataset_path: Path | None = None,
                         repair_report_path: Path | None = None) -> tuple[dict, dict]:
    """Provide the registered training-validator interface for the fixed v4 namespace."""
    if (cohort_id != BASE_COHORT or any(path is not None and path.resolve() != (directory / VERSION / name).resolve()
            for path, name in ((derived_dataset_path, "dataset.json"), (repair_report_path, "repair-report.json")))):
        raise ValueError("completion paths must identify the registered v4 dataset/report")
    return validate_pair_completion(directory)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the opt-in completion CLI; inference requires an explicit invocation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--pilot-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_pair_completion(args.directory, pilot_only=args.pilot_only)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["state"] in ("pilot_passed", "repaired_quality_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
