"""Disjoint preference variants expose an explicit decision policy without editing old labels or facts."""

import random
from copy import deepcopy
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_gemma_pilot import _verified
from matching_engine.rerank_hash import digest

PREFERENCE_VARIANTS = (
    "Technical fit requires declared React level at least 4. Among candidates meeting this stated minimum, prefer lower salary_min. Report confirmation separately; no additional priority is specified.",
    "Technical fit requires declared React level at least 4 and declared teamwork at least 6. Among candidates meeting both stated minima, prefer lower salary_min. Report confirmation separately.",
    "Technical fit requires formal test grade junior or higher (intern < junior < middle < senior), and declared React at least 3. Among candidates meeting both stated minima, prefer lower salary_min. A biography role cannot change formal grade; confirmation is reported separately.",
    "Technical fit requires declared Python at least 5 and SQL at least 4. Among candidates meeting both stated minima, prefer lower salary_min. Report confirmation separately; other attributes do not replace these stated minima.",
    "Technical fit requires declared Python at least 5 and SQL at least 4. Among candidates meeting both stated minima, prefer lower salary_min. Report confirmation separately; no additional priority is specified.",
    "Technical fit requires declared Python at least 5 and SQL at least 4. Among candidates meeting both stated minima, prefer lower salary_min. These are declared-level minima, not a demand that every declaration be confirmed.",
    "The sole preference is measured audited hours responding to production incidents. Work duration and production_support do not supply incident-response hours. Other attributes are not priorities and cannot substitute for a missing measurement.",
    "The sole preference is measured percentage reduction in API latency achieved in past projects. Skill levels, grade and work duration are not latency-reduction measurements. No other priority is specified.",
    "The sole preference is measured uptime percentage of previously maintained systems. production_support and total work duration are not uptime measurements. No other priority is specified.",
    "The sole preference is longer duration_months. Grade, skills, salary, narrative and production_support are not substitutes for a missing duration measurement. No other priority is specified.",
)


def prepare_explicit_cases(directory: Path) -> dict:
    """Freeze ten derived scenarios and a score-blind packet before any teacher output or review.

    Candidate facts and family/split ancestry are unchanged. Only explicitly
    stated requirements/preferences vary; these variants never enter train or golden.
    """
    parent = _verified(directory / "diagnostic-grounded-qwen-v5/manifest.json", "manifest_hash")
    cases, packet_cases, mapping = [], [], []
    if len(parent["cases"]) != len(PREFERENCE_VARIANTS):
        raise ValueError("explicit policy variants require ten frozen source cases")
    for index, (source, preferences) in enumerate(zip(parent["cases"], PREFERENCE_VARIANTS, strict=True)):
        state = deepcopy(source["state"])
        state["preferences"] = preferences
        vacancy = state["vacancy"]
        vacancy["domens"], vacancy["stacks"], vacancy["soft_skills"] = {}, {}, {}
        if index in (0, 1):
            vacancy["stacks"] = {"react": 4}
            if index == 1:
                vacancy["soft_skills"] = {"teamwork": 6}
        elif index == 2:
            vacancy["stacks"] = {"react": 3}
        elif index in (3, 4, 5):
            vacancy["stacks"], vacancy["domens"] = {"python": 5}, {"sql": 4}
        case_id = digest({"parent_case_id": source["case_id"], "state": state, "variant": "explicit-policy-v1"})[:16]
        cases.append({"case_id": case_id, "family": source["family"], "split": source["split"],
                      "a": source["a"], "b": source["b"], "state": state, "role": f'diagnostic_{source["split"]}',
                      "candidate_A_id": source["candidate_A_id"], "candidate_B_id": source["candidate_B_id"],
                      "source_case_id": source["case_id"], "source_facts_hash": digest({k: source["state"][k] for k in ("A", "B")})})
        flip = index % 2 == 0
        blind_state = deepcopy(state)
        if flip:
            blind_state["A"], blind_state["B"] = blind_state["B"], blind_state["A"]
        packet_cases.append({"case_id": case_id, **blind_state})
        mapping.append({"case_id": case_id, "swapped": flip})
    random.Random(5291).shuffle(packet_cases)
    packet = {"purpose": "Score-blind assistant fact review of explicit policy variants; not independent human evaluation",
              "semantics": "Use only explicitly stated minima and priorities. Declared levels and confirmation remain separate. Unknown is not zero/false; absence of required comparison measurements cannot be replaced with grade or narrative.",
              "cases": packet_cases}
    packet["packet_hash"] = digest(packet)
    manifest = {"version": "explicit-policy-v1", "source_manifest_hash": parent["manifest_hash"], "cases": cases,
                "blind_packet_hash": packet["packet_hash"], "training_eligible": False,
                "selection": "Targeted variants prepared after old diagnostic; no claim of held-out model quality"}
    manifest["manifest_hash"] = digest(manifest)
    target = directory / "diagnostic-explicit-policy-v1"
    _seal(target / "manifest.json", manifest)
    _seal(target / "blind-packet.json", packet)
    _seal(target / "blind-map.json", mapping)
    return manifest


def reviewed_explicit_cases(directory: Path) -> tuple[dict, dict, list[dict]]:
    """Verify the frozen blind review and map its labels back to stable source candidate IDs."""
    manifest = prepare_explicit_cases(directory)
    target = directory / "diagnostic-explicit-policy-v1"
    review = _verified(target / "blind-review.json", "review_hash")
    packet = _verified(target / "blind-packet.json", "packet_hash")
    mapping = _read(target / "blind-map.json")
    ids = {c["case_id"] for c in manifest["cases"]}
    if (review["packet_hash"] != manifest["blind_packet_hash"] or review["reviewer_kind"] != "assistant"
            or review["independent_human_evaluation"] is not False or len(review["cases"]) != 10
            or {r["case_id"] for r in review["cases"]} != ids or any(r["label"] not in REVERSE for r in review["cases"])
            or len(mapping) != 10 or {r["case_id"] for r in mapping} != ids
            or any(type(r["swapped"]) is not bool for r in mapping)):
        raise ValueError("complete score-blind explicit policy review required")
    flips = {r["case_id"]: r["swapped"] for r in mapping}
    labels = {r["case_id"]: r["label"] for r in review["cases"]}
    cases = []
    for case in manifest["cases"]:
        expected = deepcopy(case["state"])
        if flips[case["case_id"]]:
            expected["A"], expected["B"] = expected["B"], expected["A"]
        if next(r for r in packet["cases"] if r["case_id"] == case["case_id"]) != {"case_id": case["case_id"], **expected}:
            raise ValueError("explicit policy blind mapping modified")
        label = REVERSE[labels[case["case_id"]]] if flips[case["case_id"]] else labels[case["case_id"]]
        cases.append({**case, "blind_label": label})
    return manifest, review, cases
