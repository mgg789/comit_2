"""Pure, bounded comparison policy for the ten vacancy-local hypothesis families.

Candidates must already pass strict filtering and the caller must scope the pool
and outcomes to one vacancy/context revision. Thresholds are versioned initial
settings, not calibrated measurements or claims of causal hiring preferences.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import NormalDist

from matching_engine.embeddings import EMBEDDING_SPACE_ID
from matching_engine.rerank_hash import digest

POLICY_VERSION = "vacancy-hypothesis-comparison-v1"
FAMILIES = tuple(f"H{index}" for index in range(1, 11))
MIN_DECISIVE_PAIRS = 20
FAMILYWISE_ALPHA = 0.05
MAX_NUISANCE_DISTANCE = 0.15
MAX_NUISANCE_COMPONENT = 0.25
MAX_UTILITY_LOSS = 0.35
MIN_CONTRAST = 0.15
NEAR_HARD_DISTANCE = 0.10
MAX_PAIRS_PER_FAMILY = 12
NUMERIC_TOLERANCE = 1e-12
FACT_STATUSES = frozenset(("confirmed", "partial", "self_reported", "pending"))


def policy_hash() -> str:
    """Identify every comparison/statistical threshold, including the disabled H9."""
    return digest({
        "version": POLICY_VERSION, "families": FAMILIES,
        "minimum_decisive": MIN_DECISIVE_PAIRS, "familywise_alpha": FAMILYWISE_ALPHA,
        "multiple_testing": "bonferroni-ten-two-sided-wilson",
        "sequential_testing": "first20-decisive-frozen-single-batch",
        "max_nuisance_mean": MAX_NUISANCE_DISTANCE,
        "max_nuisance_component": MAX_NUISANCE_COMPONENT,
        "max_utility_loss": MAX_UTILITY_LOSS, "default_score_scale": 1.0,
        "min_contrast": MIN_CONTRAST, "near_hard_distance": NEAR_HARD_DISTANCE,
        "max_pairs_per_family": MAX_PAIRS_PER_FAMILY,
        "numeric_tolerance": NUMERIC_TOLERANCE,
        "total_duration": "longest-explicit-nonrejected-experience-months",
        "production_control": "explicit-false-and-explicit-developer-role",
        "H9": "disabled-until-adjacency-taxonomy", "calibration": "uncalibrated_baseline",
    })


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    values: dict[str, float]
    available: dict[str, bool]
    names: tuple[str, ...]
    schema_hash: str
    score: float
    snapshot: dict

    def observed(self, name: str) -> float | None:
        return self.values.get(name) if self.available.get(name, False) else None


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("hypothesis policy requires finite numeric values")
    return float(value)


def _candidate(raw: dict) -> _Candidate:
    features = raw.get("features")
    if not isinstance(features, Mapping):
        raise TypeError("candidate feature snapshot required")
    names = tuple(features.get("names", ()))
    values = tuple(features.get("values", ()))
    available = tuple(features.get("available", ()))
    if (len(names) != len(values) or len(names) != len(available) or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names) or any(type(flag) is not bool for flag in available)):
        raise ValueError("invalid hypothesis feature snapshot dimensions/names/availability")
    schema = features.get("schema_hash")
    candidate_id = raw.get("candidate_id")
    snapshot = raw.get("snapshot", {})
    if (not isinstance(schema, str) or not schema or not isinstance(candidate_id, str)
            or not candidate_id or not isinstance(snapshot, dict)):
        raise ValueError("candidate identity/schema/snapshot required")
    return _Candidate(candidate_id, dict(zip(names, map(_number, values), strict=True)),
                      dict(zip(names, available, strict=True)), names, schema,
                      _number(raw.get("score")), snapshot)


def _mean(candidate: _Candidate, groups: tuple[str, ...], signal: str) -> float | None:
    values = [candidate.observed(f"{group}.{signal}") for group in groups]
    observed = [value for value in values if value is not None]
    return math.fsum(observed) / len(observed) if observed else None


def _coordinates(candidate: _Candidate, groups: tuple[str, ...] = ("domain", "stack")) -> list[str]:
    return [name[:-7] for name in candidate.names if name.endswith(".target")
            and name.split(".")[0] in groups and candidate.available[name]]


def _known_hard(candidate: _Candidate, *, allow_rejected: bool = False) -> bool:
    coordinates = _coordinates(candidate)
    return bool(coordinates) and all(
        candidate.observed(f"{prefix}.level") is not None
        and (allow_rejected or candidate.observed(f"{prefix}.rejected") == 0)
        and sum(candidate.observed(f"{prefix}.{status}") == 1
                for status in (*FACT_STATUSES, "rejected")) == 1
        for prefix in coordinates
    )


def _duration(candidate: _Candidate) -> float | None:
    # This deliberately does not use experience.duration: that builder describes
    # only relevant jobs, while H5 requires an explicitly observed general span.
    durations: list[float] = []
    for claim in candidate.snapshot.get("narrative_facts", []):
        if not isinstance(claim, dict) or claim.get("kind") != "experience" or claim.get("status") not in FACT_STATUSES:
            continue
        facts = claim.get("facts")
        fact = facts.get("duration_months") if isinstance(facts, dict) else None
        if isinstance(fact, dict) and fact.get("status") in FACT_STATUSES:
            value = fact.get("value")
            if type(value) is int and 0 <= value <= 1200:
                durations.append(float(value))
    return max(durations) / 1200 if durations else None


def _production(candidate: _Candidate, expected: bool) -> bool:
    for claim in candidate.snapshot.get("narrative_facts", []):
        if not isinstance(claim, dict) or claim.get("status") not in FACT_STATUSES or claim.get("kind") not in ("project", "experience"):
            continue
        facts = claim.get("facts")
        if not isinstance(facts, dict):
            continue
        required = set(_coordinates(candidate))
        relevant = any(isinstance(ref, dict) and ref.get("status") in FACT_STATUSES
                       and f"{group}.{ref.get('key')}" in required
                       for group, refs in (("domain", facts.get("domains", [])),
                                           ("stack", facts.get("stacks", [])))
                       for ref in refs)
        if not relevant:
            continue
        production, role = facts.get("production_support"), facts.get("role")
        if (isinstance(production, dict) and production.get("status") in FACT_STATUSES
                and production.get("value") is expected
                and (expected or (isinstance(role, dict) and role.get("status") in FACT_STATUSES
                                  and role.get("value") == "developer"))):
            return True
    return False


def _signals(candidate: _Candidate) -> dict[str, float | None]:
    hard = ("domain", "stack")
    result = {"grade": candidate.observed("grade"),
              "hard_fit": _mean(candidate, hard, "proximity"),
              "hard_coverage": _mean(candidate, hard, "coverage"),
              "hard_confirmation": _mean(candidate, hard, "confirmed_share"),
              "hard_depth": _mean(candidate, hard, "depth"),
              "hard_breadth": _mean(candidate, hard, "weakest"),
              "domain_fit": candidate.observed("domain.proximity"),
              "stack_fit": candidate.observed("stack.proximity"),
              "soft_fit": candidate.observed("soft_skill.proximity"),
              "affordability": candidate.observed("salary_affordability"),
              "semantic": candidate.observed("semantic_cosine"),
              "project_fit": candidate.observed("project_relevance_evidence"),
              "experience_fit": candidate.observed("experience_relevance_evidence"),
              "total_duration": _duration(candidate)}
    levels = [candidate.observed(f"{prefix}.level") for prefix in _coordinates(candidate)]
    observed = [value for value in levels if value is not None]
    result["key_depth"] = max(observed) if observed else None
    for kind in ("project", "experience", "achievement"):
        for signal in ("production", "production_evidence", "role_evidence", "developer", "devops",
                       "lead", "architect", "analyst", "designer", "researcher", "qa", "other"):
            result[f"{kind}.{signal}"] = candidate.observed(f"{kind}.{signal}")
    return result


def _grade_status(candidate: _Candidate) -> object:
    category = candidate.snapshot.get("category")
    return category.get("grade_status") if isinstance(category, dict) else candidate.snapshot.get("grade_status")


# Exempt only the features involved in the stated trade-off and their direct
# derivatives. Every other observed material signal remains a nuisance check.
_EXEMPT = {
    "H1": {"grade", "hard_fit", "hard_coverage", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit"},
    "H2": {"hard_fit", "hard_confirmation", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit"},
    "H3": {"hard_fit", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit"},
    "H4": {"hard_fit", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit"},
    "H5": {"project_fit", "experience_fit", "total_duration"},
    "H6": {"project_fit", "experience_fit"},
    "H7": {"hard_fit", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit", "soft_fit"},
    "H8": {"affordability", "hard_fit", "hard_depth", "hard_breadth", "key_depth", "domain_fit", "stack_fit"},
    "H10": {"semantic"},
}
_EXEMPT["H6"].update(f"{kind}.{signal}" for kind in ("project", "experience")
                       for signal in ("production", "production_evidence", "role_evidence",
                                      "developer", "devops", "lead", "architect"))


def _contrast(hypothesis: str, test: _Candidate, control: _Candidate,
              a: dict[str, float | None], b: dict[str, float | None]) -> dict | None:
    definitions = {
        "H1": ("hard_coverage", "grade"), "H2": ("hard_confirmation", "hard_depth"),
        "H3": ("domain_fit", "stack_fit"), "H4": ("key_depth", "hard_breadth"),
        "H5": ("project_fit", "total_duration"), "H7": ("soft_fit", "hard_fit"),
        "H8": ("affordability", "hard_fit"),
    }
    allow_rejected = hypothesis == "H1"
    if (hypothesis == "H9" or not _known_hard(test, allow_rejected=allow_rejected)
            or not _known_hard(control, allow_rejected=allow_rejected)):
        return None
    if hypothesis == "H6":
        if not (_production(test, True) and _production(control, False)
                and not _production(control, True)):
            return None
        # The canonical builder determines whether the typed production facts
        # concern this vacancy's explicit requirements.
        production = [test.observed(f"{kind}.production") for kind in ("project", "experience")]
        observed = [value for value in production if value is not None]
        if not observed or max(observed) < MIN_CONTRAST:
            return None
        return {"preferred": "explicit_production_support", "sacrificed": "explicit_component_developer",
                "preferred_delta": 1.0, "sacrificed_delta": 1.0}
    if hypothesis == "H10":
        spaces = [item.snapshot.get("semantic_space_id") for item in (test, control)]
        if spaces[0] != EMBEDDING_SPACE_ID or spaces[1] != EMBEDDING_SPACE_ID:
            return None
        if a["semantic"] is None or b["semantic"] is None or a["hard_fit"] is None or b["hard_fit"] is None:
            return None
        delta = a["semantic"] - b["semantic"]
        if delta < MIN_CONTRAST or abs(a["hard_fit"] - b["hard_fit"]) > NEAR_HARD_DISTANCE:
            return None
        return {"preferred": "semantic", "sacrificed": "near_equal_hard_profile",
                "preferred_delta": delta, "sacrificed_delta": 0.0}
    preferred, sacrificed = definitions[hypothesis]
    if any(value is None for value in (a[preferred], b[preferred], a[sacrificed], b[sacrificed])):
        return None
    gain, loss = a[preferred] - b[preferred], b[sacrificed] - a[sacrificed]
    # Longest explicit duration is in months/1200; require a full year trade-off.
    minimum_loss = 12 / 1200 if hypothesis == "H5" else MIN_CONTRAST
    if gain + NUMERIC_TOLERANCE < MIN_CONTRAST or loss + NUMERIC_TOLERANCE < minimum_loss:
        return None
    if hypothesis == "H4" and len(_coordinates(test)) < 2:
        return None
    if hypothesis == "H7" and loss > MAX_NUISANCE_COMPONENT:
        return None
    return {"preferred": preferred, "sacrificed": sacrificed,
            "preferred_delta": gain, "sacrificed_delta": loss}


def _nuisance(hypothesis: str, a: dict[str, float | None],
              b: dict[str, float | None]) -> float | None:
    differences = []
    for name in a.keys() - _EXEMPT[hypothesis]:
        if (a[name] is None) != (b[name] is None):
            return None
        if a[name] is not None:
            differences.append(abs(a[name] - b[name]))
    if not differences or max(differences) > MAX_NUISANCE_COMPONENT:
        return None
    distance = math.fsum(differences) / len(differences)
    return distance if distance <= MAX_NUISANCE_DISTANCE else None


def _uncertainty(test: _Candidate, control: _Candidate, model: dict | None) -> float:
    if not model:
        return 1.0
    names = model.get("feature_names", ())
    covariance, scales = model.get("covariance", ()), model.get("scales", ())
    compatibility = model.get("compatibility", {})
    if isinstance(compatibility, dict) and compatibility.get("snapshot_schema_hash", test.schema_hash) != test.schema_hash:
        return 1.0
    try:
        if not names or len(covariance) != len(names) or len(scales) != len(names):
            return 1.0
        delta = []
        for name, scale in zip(names, scales, strict=True):
            scale = _number(scale)
            if scale <= 0:
                return 1.0
            a, b = test.observed(name), control.observed(name)
            delta.append((a - b) / scale if a is not None and b is not None else 0.0)
        if any(len(row) != len(names) for row in covariance):
            return 1.0
        variance = math.fsum(delta[i] * _number(value) * delta[j]
                             for i, row in enumerate(covariance) for j, value in enumerate(row))
        if variance < 0:
            return 1.0
        if not math.isfinite(variance):
            return 1.0
        return min(1.0, max(0.0, variance / (1 + variance)))
    except (ValueError, TypeError, KeyError, OverflowError):
        return 1.0


def comparable_pairs(candidates: list[dict], model: dict | None = None) -> list[dict]:
    """Find directional, bounded comparisons in an already eligible unseen pool.

    Features are immutable builder snapshots; scores must be finite. Covariance
    optionally prioritizes unknown contrasts, while pool prevalence measures
    their potential impact. The policy returns candidates for caller-controlled
    random selection; it neither chooses show order nor invents propensities.
    """
    pool = sorted((_candidate(raw) for raw in candidates), key=lambda item: item.candidate_id)
    if len({item.candidate_id for item in pool}) != len(pool):
        raise ValueError("duplicate hypothesis candidate identity")
    if len(pool) < 2:
        return []
    signals = {item.candidate_id: _signals(item) for item in pool}
    best_score = max(item.score for item in pool)
    scale = _number(model.get("baseline_scale", 1.0)) if model else 1.0
    if scale < 1:
        raise ValueError("hypothesis score scale must be at least one")
    found: list[dict] = []
    for index, first in enumerate(pool):
        for second in pool[index + 1:]:
            if first.schema_hash != second.schema_hash or first.names != second.names:
                continue
            targets = [name for name in first.names if name.endswith(".target")]
            if any(first.available[name] != second.available[name] or first.values[name] != second.values[name] for name in targets):
                continue
            if _grade_status(first) != _grade_status(second):
                continue
            utility_loss = max(0.0, best_score - min(first.score, second.score)) / scale
            if utility_loss > MAX_UTILITY_LOSS:
                continue
            for test, control in ((first, second), (second, first)):
                a, b = signals[test.candidate_id], signals[control.candidate_id]
                for hypothesis in _EXEMPT:
                    contrast = _contrast(hypothesis, test, control, a, b)
                    if contrast is None:
                        continue
                    nuisance = _nuisance(hypothesis, a, b)
                    if nuisance is None:
                        continue
                    contrast = {**contrast, "utility_loss": utility_loss,
                                "feature_schema_hash": test.schema_hash,
                                "nuisance_limit": MAX_NUISANCE_DISTANCE,
                                "calibration_status": "uncalibrated_baseline"}
                    pair = {"hypothesis_id": hypothesis, "test_id": test.candidate_id,
                            "control_id": control.candidate_id, "contrast": contrast,
                            "nuisance_distance": nuisance,
                            "uncertainty": _uncertainty(test, control, model)}
                    pair["comparison_hash"] = digest({**pair, "policy_hash": policy_hash(),
                                                      "test_features": test.values, "control_features": control.values,
                                                      "test_available": test.available, "control_available": control.available,
                                                      "test_facts": test.snapshot.get("narrative_facts", []),
                                                      "control_facts": control.snapshot.get("narrative_facts", [])})
                    found.append(pair)
    counts = {family: sum(pair["hypothesis_id"] == family for pair in found) for family in FAMILIES}
    possible = len(pool) * (len(pool) - 1) / 2
    for pair in found:
        impact = min(1.0, counts[pair["hypothesis_id"]] / possible)
        pair["contrast"]["pool_impact"] = impact
        quality = 1 - pair["nuisance_distance"] / (2 * MAX_NUISANCE_DISTANCE)
        priority = pair.pop("uncertainty") * impact * quality * (1 - pair["contrast"]["utility_loss"])
        pair["priority"] = min(1.0, max(0.0, priority))
    found.sort(key=lambda pair: (-pair["priority"], pair["hypothesis_id"], pair["comparison_hash"]))
    kept: dict[str, int] = dict.fromkeys(FAMILIES, 0)
    result = []
    for pair in found:
        family = pair["hypothesis_id"]
        if kept[family] < MAX_PAIRS_PER_FAMILY:
            result.append(pair)
            kept[family] += 1
    return result


def _wilson(supporting: int, decisive: int) -> tuple[float, float]:
    if not decisive:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(1 - FAMILYWISE_ALPHA / (2 * len(FAMILIES)))
    p = supporting / decisive
    denominator = 1 + z * z / decisive
    center = (p + z * z / (2 * decisive)) / denominator
    spread = z * math.sqrt(p * (1 - p) / decisive + z * z / (4 * decisive * decisive)) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))


def summarize(outcomes: list[dict]) -> list[dict]:
    """Summarize complete local comparisons with ten-family corrected intervals.

    Only like versus explicit dislike is decisive. Same reactions and an
    undecided impression carry no directional label. Deduplicate durable pair
    IDs when supplied; caller must provide journal time order, scope revisions
    and exclude revoked data. Only the first twenty decisive pairs form the
    preregistered batch. Later pairs cannot reopen its confidence decision.
    """
    result = []
    for family in FAMILIES:
        rows = [row for row in outcomes if row.get("hypothesis_id") == family]
        seen: set[str] = set()
        supporting = opposing = total = observed_decisive = 0
        for row in rows:
            if row.get("status") == "not_testable":
                continue
            identity = row.get("comparison_id") or row.get("comparison_hash")
            if identity:
                if str(identity) in seen:
                    continue
                seen.add(str(identity))
            total += 1
            test, control = row.get("test_action"), row.get("control_action")
            supports = test == "like" and control == "dislike"
            opposes = test == "dislike" and control == "like"
            if supports or opposes:
                observed_decisive += 1
                if observed_decisive <= MIN_DECISIVE_PAIRS:
                    supporting += int(supports)
                    opposing += int(opposes)
        decisive = supporting + opposing
        low, high = _wilson(supporting, decisive)
        status = "unclear" if total else "not_testable"
        if family == "H9":
            status = "not_testable"
        elif decisive >= MIN_DECISIVE_PAIRS:
            if low > 0.5:
                status = "supported"
            elif high < 0.5:
                status = "not_supported"
        result.append({"hypothesis_id": family, "status": status,
                       "supporting": supporting, "opposing": opposing,
                       "decisive": decisive, "total": total,
                       "observed_decisive": observed_decisive,
                       "post_batch_decisive": max(0, observed_decisive - MIN_DECISIVE_PAIRS),
                       "batch_status": "frozen" if decisive == MIN_DECISIVE_PAIRS else "collecting",
                       "confidence_interval": [low, high],
                       "minimum_decisive_pairs": MIN_DECISIVE_PAIRS,
                       "familywise_alpha": FAMILYWISE_ALPHA,
                       "multiple_testing_families": len(FAMILIES),
                       "policy_hash": policy_hash(),
                       "calibration_status": "uncalibrated_baseline"})
    return result
