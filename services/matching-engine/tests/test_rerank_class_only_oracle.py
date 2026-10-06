"""Hand-specified class-only decisions and metamorphic proofs, without a teacher."""

from copy import deepcopy

import pytest

from matching_engine import rerank_class_only_oracle as oracle
from matching_engine.rerank_cohort_provider import source_citations
from matching_engine.rerank_hash import digest
from matching_engine.rerank_sample_probe import _cycles, _ordering_ceiling

STATUSES = ("confirmed", "partial", "self_reported", "pending", "rejected")
VACANCY = {"class": "backend", "domens": {}, "stacks": {}, "soft_skills": {}}


def profile(grade="middle", status="partial", **facts):
    """Build raw typed fixtures; expected decisions remain literal test constants."""
    value = {"grade": grade, "status": status, "levels": {"python": 3}, "skill_statuses": {"python": "pending"},
             "salary_min": 50000, "description": "Проверка API.", "structured_facts_available": False,
             "duration_months": None, "duration_status": None, "production_support": None, "production_status": None,
             "role": None, "role_status": None, "project_domains": [], "project_stacks": []}
    value.update(facts)
    value["structured_facts_available"] = any(value[key] is not None for key in ("duration_months", "production_support", "role"))
    return value


def fixtures():
    """Cover every rule branch with independently stated expected labels and reasons."""
    policy = deepcopy(oracle.CLASS_ONLY_POLICY)
    exclusive = {**deepcopy(policy), "exclusive_criterion": True, "required_measurements": ["uptime_percentage"]}
    return [
        (profile("senior", "rejected"), profile("middle", "confirmed"), policy, "A", "formal_grade"),
        (profile(status="pending"), profile(status="confirmed"), policy, "tie", "no_applicable_difference"),
        (profile(production_support=True, production_status="pending"),
         profile(production_support=False, production_status="confirmed"), policy, "A", "production_value"),
        (profile(production_support=False, production_status="confirmed"),
         profile(production_support=False, production_status="partial"), policy, "A", "production_confirmation"),
        (profile(production_support=True, production_status="rejected"),
         profile(production_support=False, production_status="confirmed"), policy, "tie", "no_applicable_difference"),
        (profile(duration_months=0, duration_status="confirmed"),
         profile(duration_months=6, duration_status="pending"), policy, "B", "duration"),
        (profile(), profile(duration_months=120, duration_status="confirmed"), policy, "tie", "no_applicable_difference"),
        (profile(duration_months=120, duration_status="rejected"),
         profile(duration_months=0, duration_status="pending"), policy, "tie", "no_applicable_difference"),
        (profile(duration_months=6, duration_status="confirmed"),
         profile(duration_months=6, duration_status="pending"), policy, "tie", "no_applicable_difference"),
        (profile(production_support=True, production_status="pending", duration_months=0, duration_status="pending"),
         profile(production_support=False, production_status="confirmed", duration_months=120, duration_status="confirmed"),
         policy, "A", "production_value"),
        (profile("senior"), profile("intern"), exclusive, "insufficient", "essential_availability"),
        (profile(production_support=False, production_status="partial", duration_months=6, duration_status="confirmed"),
         profile(production_support=False, production_status="partial", duration_months=12, duration_status="pending"),
         policy, "B", "duration"),
    ]


@pytest.mark.parametrize("a,b,policy,label,criterion", fixtures())
def test_manual_branches_and_swap_proof(a, b, policy, label, criterion):
    decision = oracle.class_only_decision(a, b, policy)
    assert decision["label"] == label and decision["decision_criterion"] == criterion
    assert oracle.class_only_label(b, a, policy) == {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[label]
    original = deepcopy((a, b, policy))
    answer = oracle.class_only_answer(VACANCY, a, b, policy)
    assert answer["label"] == label and answer["decision_criterion"] == criterion
    assert answer["label_source"] == oracle.LABEL_SOURCE and answer["oracle_version"] == oracle.VERSION
    assert answer["human_authorship_verified"] is answer["independent_human_evaluation"] is False
    assert answer["policy_hash"] == digest(policy)
    assert answer["input_hash"] == digest({"vacancy": VACANCY, "A": a, "B": b, "policy": policy})
    citations = source_citations(a, b)
    for side in ("A", "B"):
        if criterion == "essential_availability":
            assert answer[f"evidence_{side}"] == []
            assert answer["absence_evidence"][side] == [{"path": "uptime_percentage", "state": "absent"}]
        else:
            assert 1 <= len(answer[f"evidence_{side}"]) <= 4
            assert answer["absence_evidence"][side] == []
            if criterion == "formal_grade":
                assert [citations[token]["field"] for token in answer[f"evidence_{side}"]] == ["grade"]
        assert all(citations[token]["candidate"] == side for token in answer[f"evidence_{side}"])
        assert not any(citations[token]["field"] in ("salary_min", "status", "description")
                       for token in answer[f"evidence_{side}"])
    assert (a, b, policy) == original


@pytest.mark.parametrize("parent_a", STATUSES)
@pytest.mark.parametrize("parent_b", STATUSES)
def test_parent_status_invariance_for_all_manual_branches(parent_a, parent_b):
    for a, b, policy, label, _criterion in fixtures():
        a, b = {**a, "status": parent_a}, {**b, "status": parent_b}
        assert oracle.class_only_label(a, b, policy) == label


@pytest.mark.parametrize("left", STATUSES)
@pytest.mark.parametrize("right", STATUSES)
def test_own_confirmation_order_and_rejected_inapplicability(left, right):
    a = profile(production_support=False, production_status=left)
    b = profile(production_support=False, production_status=right)
    order = {"confirmed": 3, "partial": 2, "self_reported": 1, "pending": 0}
    expected = "tie" if "rejected" in (left, right) or left == right else (
        "A" if order[left] > order[right] else "B")
    assert oracle.class_only_label(a, b, oracle.CLASS_ONLY_POLICY) == expected


def test_duration_evidence_when_equal_known_production_does_not_decide():
    a, b, policy, _label, _criterion = fixtures()[-1]
    answer = oracle.class_only_answer(VACANCY, a, b, policy)
    citations = source_citations(a, b)
    for side in ("A", "B"):
        assert {citations[token]["field"] for token in answer[f"evidence_{side}"]} == {
            "grade", "duration_months", "duration_status"}
    assert answer["proof"]["applicable_steps"][-1] == {"criterion": "duration", "A": 6, "B": 12}


@pytest.mark.parametrize("path", ["production_support", "duration_months"])
def test_false_and_zero_are_present_exclusive_measurements(path):
    policy = {**oracle.CLASS_ONLY_POLICY, "exclusive_criterion": True, "required_measurements": [path]}
    a = profile(production_support=False, production_status="pending", duration_months=0, duration_status="pending")
    assert oracle.class_only_label(a, a, policy) == "tie"
    assert oracle.class_only_label(a, profile("senior"), policy) == "insufficient"


@pytest.mark.parametrize("field,value", [("ordered_priorities", ["Prefer salary"]), ("instructions", "Different"),
                                        ("version", "forged"), ("extra", "unexpected")])
def test_forged_same_id_policy_rejected(field, value):
    policy = {**oracle.CLASS_ONLY_POLICY, field: value}
    with pytest.raises(ValueError, match="exact versioned policy"):
        oracle.class_only_label(profile(), profile(), policy)


def test_nonclass_and_invalid_raw_facts_fail_without_coercion():
    with pytest.raises(ValueError, match="technical"):
        oracle.class_only_answer({**VACANCY, "stacks": {"python": 4}}, profile(), profile(), oracle.CLASS_ONLY_POLICY)
    invalid = profile(production_support=False, production_status=None)
    with pytest.raises(ValueError):
        oracle.class_only_label(invalid, profile(), oracle.CLASS_ONLY_POLICY)


def test_salary_prose_and_skills_never_change_the_class_only_preference():
    a, b = profile(), profile()
    a.update(salary_min=180000, description="Другая проверка и другая зарплата.", levels={"python": 7},
             skill_statuses={"python": "confirmed"})
    assert oracle.class_only_label(a, b, oracle.CLASS_ONLY_POLICY) == "tie"


@pytest.mark.parametrize("path,state", [("uptime_percentage", "absent"), ("duration_months", "null"),
                                      ("description", "excluded_prose"), ("levels.unknown", "absent")])
def test_essential_absence_witness_grounded_in_exact_raw_path(path, state):
    policy = {**oracle.CLASS_ONLY_POLICY, "exclusive_criterion": True, "required_measurements": [path]}
    a, b = profile("senior"), profile("intern")
    answer = oracle.class_only_answer(VACANCY, a, b, policy)
    assert answer["label"] == "insufficient" and answer["evidence_A"] == answer["evidence_B"] == []
    assert answer["absence_evidence"] == {side: [{"path": path, "state": state}] for side in ("A", "B")}
    assert "value" not in answer["absence_evidence"]["A"][0]


def test_only_missing_side_has_absence_witness_and_rejected_is_present():
    policy = {**oracle.CLASS_ONLY_POLICY, "exclusive_criterion": True, "required_measurements": ["production_support"]}
    known = profile(production_support=False, production_status="rejected")
    answer = oracle.class_only_answer(VACANCY, known, profile(), policy)
    assert answer["absence_evidence"] == {"A": [], "B": [{"path": "production_support", "state": "null"}]}
    assert oracle.class_only_label(known, known, policy) == "tie"
    assert oracle.class_only_decision(known, known, policy)["proof"]["applicable_steps"] == [
        {"criterion": "formal_grade", "A": 2, "B": 2}]


def test_pairwise_optional_skip_can_cycle_and_scalar_ceiling_is_not_perfect():
    # A beats B on production; B beats C and C beats A on duration because C lacks production.
    a = profile(production_support=True, production_status="confirmed", duration_months=0, duration_status="confirmed")
    b = profile(production_support=False, production_status="confirmed", duration_months=12, duration_status="confirmed")
    c = profile(duration_months=6, duration_status="confirmed")
    policy = oracle.CLASS_ONLY_POLICY
    assert oracle.class_only_label(a, b, policy) == "A"
    assert oracle.class_only_label(b, c, policy) == "A"
    assert oracle.class_only_label(c, a, policy) == "A"
    pairs = [[0, 1, "A"], [0, 2, "B"], [1, 2, "A"]]
    assert _cycles(3, pairs) == [[0, 1, 2]]
    assert _ordering_ceiling(3, pairs) == {"decisive_pairs": 3, "maximum_satisfiable_pairs": 2,
        "minimum_unavoidable_disagreements": 1, "maximum_scalar_pair_accuracy": 2 / 3}
