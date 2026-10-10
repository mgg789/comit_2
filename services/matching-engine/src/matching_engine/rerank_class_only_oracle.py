"""Pure synthetic class-only labels from an explicit policy; never calls a model."""

from matching_engine.rerank_cohort_provider import source_citations, validate_candidate
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import salary_comparison

VERSION = "class-only-pair-oracle-v1"
LABEL_SOURCE = "rule_derived_class_only"
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
_GRADE = {"intern": 0, "junior": 1, "middle": 2, "senior": 3}
_STATUS = {"pending": 0, "self_reported": 1, "partial": 2, "confirmed": 3}
_REASONS = {"A": "prefer_known_facts", "B": "prefer_known_facts", "tie": "comparable_fit",
            "insufficient": "insufficient_source_facts"}


def _measurement_state(candidate: dict, path: str) -> str:
    value = candidate
    for field in path.split("."):
        if field == "description":
            return "excluded_prose"
        if value is None:
            return "null"
        if type(value) is not dict or field not in value:
            return "absent"
        value = value[field]
    return "null" if value is None else "present"


def _applicable(a: dict, b: dict, value: str, status: str) -> bool:
    return all(candidate[value] is not None and candidate[status] != "rejected" for candidate in (a, b))


def _policy(policy: dict) -> None:
    allowed = {"exclusive_criterion", "required_measurements"}
    if (type(policy) is not dict or set(policy) != set(CLASS_ONLY_POLICY)
            or any(policy[key] != value for key, value in CLASS_ONLY_POLICY.items() if key not in allowed)
            or type(policy["exclusive_criterion"]) is not bool
            or type(policy["required_measurements"]) is not list
            or any(type(field) is not str or not field for field in policy["required_measurements"])):
        raise ValueError("class-only oracle requires exact versioned policy semantics")


def class_only_decision(a: dict, b: dict, policy: dict) -> dict:
    """Return label, decisive criterion and own-field proof from the frozen rule.

    Only explicit exclusive measurement metadata may vary from the base policy.
    Parent status, salary, prose and technical skills never decide class-only fit.
    """
    _policy(policy)
    validate_candidate(a)
    validate_candidate(b)
    absence = {side: [{"path": path, "state": _measurement_state(candidate, path)}
                      for path in policy["required_measurements"] if _measurement_state(candidate, path) != "present"]
               for side, candidate in (("A", a), ("B", b))}
    if policy["exclusive_criterion"] and any(absence.values()):
        return {"label": "insufficient", "decision_criterion": "essential_availability",
                "proof": {"missing_required_measurements": {side: [witness["path"] for witness in witnesses]
                                                            for side, witnesses in absence.items()}},
                "absence_evidence": absence, "evidence_fields": []}
    comparisons = [("formal_grade", _GRADE[a["grade"]], _GRADE[b["grade"]], ["grade"])]
    if _applicable(a, b, "production_support", "production_status"):
        comparisons.extend([
            ("production_value", int(a["production_support"]), int(b["production_support"]),
             ["grade", "production_support", "production_status"]),
            ("production_confirmation", _STATUS[a["production_status"]], _STATUS[b["production_status"]],
             ["grade", "production_support", "production_status"])])
    if _applicable(a, b, "duration_months", "duration_status"):
        comparisons.append(("duration", a["duration_months"], b["duration_months"],
                            ["grade", "duration_months", "duration_status"]))
    steps = []
    for criterion, left, right, fields in comparisons:
        steps.append({"criterion": criterion, "A": left, "B": right})
        if left != right:
            return {"label": "A" if left > right else "B", "decision_criterion": criterion,
                    "proof": {"applicable_steps": steps}, "absence_evidence": {"A": [], "B": []},
                    "evidence_fields": fields}
    return {"label": "tie", "decision_criterion": "no_applicable_difference",
            "proof": {"applicable_steps": steps}, "absence_evidence": {"A": [], "B": []},
            "evidence_fields": comparisons[-1][3]}


def class_only_label(a: dict, b: dict, policy: dict) -> str:
    """Return the pure rule label; false/zero are known and missing facts are skipped."""
    return class_only_decision(a, b, policy)["label"]


def class_only_answer(vacancy: dict, a: dict, b: dict, policy: dict) -> dict:
    """Return a synthetic answer with actual decisive own citations and provenance.

    Local evidence contains 1-4 actual own citations, or explicit absent/null/
    excluded-prose witnesses for an essential unknown instead of invented values.
    Input/policy hashes preserve raw facts, including prose that never determines
    the label. The provenance explicitly denies human review and model inference.
    """
    if any(vacancy.get(field) for field in ("domens", "stacks", "soft_skills")):
        raise ValueError("class-only oracle forbids technical vacancies")
    decision = class_only_decision(a, b, policy)
    label = decision["label"]
    citations = source_citations(a, b)
    evidence = {side: [token for field in decision["evidence_fields"] for token, fact in citations.items()
                      if fact["candidate"] == side and fact["field"] == field] for side in ("A", "B")}
    return {"label": label, "reason_code": _REASONS[label], "status_A": a["status"], "status_B": b["status"],
            "evidence_A": evidence["A"], "evidence_B": evidence["B"],
            "decision_criterion": decision["decision_criterion"], "proof": decision["proof"],
            "absence_evidence": decision["absence_evidence"],
            "salary_comparison_choice": salary_comparison(a, b)["cheaper_side"],
            "label_source": LABEL_SOURCE, "oracle_version": VERSION, "policy_hash": digest(policy),
            "input_hash": digest({"vacancy": vacancy, "A": a, "B": b, "policy": policy}),
            "human_authorship_verified": False, "independent_human_evaluation": False}
