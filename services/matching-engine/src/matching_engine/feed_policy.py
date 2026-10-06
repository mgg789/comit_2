"""Deterministic, text-free profile delta policy for the candidate feed.

The feed stores a canonical structural projection of a profile as its baseline.
This module deliberately has no database, clock, model, or mutable state.  A
worker can therefore replay ``assess`` against the last stored baseline and get
the same result after a restart.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Final

JsonObject = dict[str, object]

_GRADE_ORDINAL: Final[dict[str, int]] = {
    "intern": 0,
    "junior": 1,
    "middle": 2,
    "senior": 3,
}
_STATUS_WEIGHT: Final[dict[str, float]] = {
    "confirmed": 1.0,
    "approved": 1.0,
    "partial": 0.8,
    "self_reported": 0.6,
    "pending": 0.5,
    "rejected": 0.0,
}
_SKILL_GROUPS: Final[tuple[str, ...]] = ("domains", "stacks", "soft_skills")
_NARRATIVE_KINDS: Final[tuple[str, ...]] = ("projects", "experience", "achievements")
_NARRATIVE_STATUS_ORDER: Final[tuple[str, ...]] = (
    "confirmed",
    "partial",
    "self_reported",
    "pending",
    "rejected",
)


def default_policy() -> JsonObject:
    """Return the complete frozen v1 feed policy as a fresh JSON object.

    ``threshold`` is intentionally one distance unit and the comparison is
    strict: a baseline is refreshed only when ``N > K``.  Confidence weights,
    group weights, caps, and explanation bounds are all part of this object so
    that a persisted policy is sufficient to replay a decision.
    """
    return {
        "version": "feed-delta-v1",
        "distance_version": 1,
        "threshold": 1.0,
        "round_digits": 6,
        "max_explanations": 8,
        "confidence_weights": {
            "confirmed": 1.0,
            "approved": 1.0,
            "partial": 0.8,
            "self_reported": 0.6,
            "pending": 0.5,
            "rejected": 0.0,
        },
        "group_weights": {
            "grade": 1.0,
            "direction": 1.0,
            "skills": 1.0,
            "salary": 0.5,
            "location": 0.5,
            "work_types": 0.5,
            "relocation": 0.5,
            "narrative": 1.0,
        },
        "group_caps": {
            "grade": 3.0,
            "direction": 1.0,
            "skills": 2.0,
            "salary": 1.0,
            "location": 1.0,
            "work_types": 1.0,
            "relocation": 1.0,
            "narrative": 2.0,
        },
        "normalizers": {
            "salary_rub": 1_000_000.0,
            "duration_months": 120.0,
            "narrative_count": 10.0,
            "skill_level": 7.0,
            "grade_ordinal": 3.0,
        },
        "feature_weights": {
            "grade_step": 2.0,
            "grade_confidence": 3.0,
        },
    }


def policy_hash(policy: Mapping[str, object] | None = None) -> str:
    """Return the SHA-256 fingerprint of a policy's canonical JSON encoding."""
    value = default_policy() if policy is None else dict(policy)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def snapshot(profile: Mapping[str, object]) -> JsonObject:
    """Project a raw persisted profile into canonical, text-free feed input.

    The projection retains only grade, direction, skills, employment
    constraints, and typed narrative facts.  IDs, revisions, contacts,
    descriptions, titles, provenance, metadata, and source ordering are
    intentionally discarded.  The input is treated as untrusted JSON and is
    never mutated.
    """
    grade_result = _mapping(profile.get("grade_result"))
    if not grade_result:
        grade_result = _mapping(profile.get("category"))
    grade = grade_result.get("grade")
    grade_name = _string_or_none(grade)
    grade_status = _string_or_none(grade_result.get("status", grade_result.get("grade_status")))
    salary = _salary_projection(profile)
    relocation_cities = _sorted_strings(profile.get("relocation_cities"))
    work_types = _sorted_strings(profile.get("work_types"))
    return {
        "version": 1,
        "grade": {
            "ordinal": _GRADE_ORDINAL.get(grade_name) if grade_name is not None else None,
            "status": grade_status,
        },
        "direction": _string_or_none(profile.get("primary_direction", profile.get("direction"))),
        "skills": {group: _skill_projection(profile.get(group)) for group in _SKILL_GROUPS},
        "salary": salary,
        "city": _normalized_text(profile.get("city")),
        "work_types": work_types,
        "relocation": {
            "ready": _bool_or_none(profile.get("relocation_ready")),
            "cities": relocation_cities,
        },
        "narrative": {
            kind: _narrative_projection(profile.get(kind)) for kind in _NARRATIVE_KINDS
        },
    }


def assess(
    baseline: Mapping[str, object],
    current: Mapping[str, object],
    policy: Mapping[str, object] | None = None,
) -> JsonObject:
    """Assess one profile against its last baseline using a bounded distance.

    ``baseline`` is always the comparison anchor: the function never sums
    intermediate revisions.  The returned distance is rounded before the
    strict ``> threshold`` decision, and explanations are sorted and bounded
    by the persisted policy.  ``changed`` describes any structural change;
    ``positive_professional`` independently describes an improvement in grade,
    evidence confidence, skills, or typed professional facts.
    """
    active = default_policy() if policy is None else _copy_policy(policy)
    _validate_policy(active)
    _validate_snapshot(baseline)
    _validate_snapshot(current)
    explanations, positive = _changes(baseline, current, active)
    groups = _group_summary(explanations, active)
    distance = _round(sum(float(value["capped"]) for value in groups.values()), active)
    threshold = _number(active.get("threshold"), 1.0)
    limit = max(0, int(_number(active.get("max_explanations"), 8.0)))
    bounded = sorted(explanations, key=_explanation_sort_key)[:limit]
    return {
        "version": str(active.get("version", "feed-delta-v1")),
        "policy_hash": policy_hash(active),
        "distance": distance,
        "threshold": threshold,
        "changed": distance > 0.0,
        "refresh": distance > threshold,
        "positive_professional": positive,
        "groups": groups,
        "explanations": bounded,
    }


def _copy_policy(policy: Mapping[str, object]) -> JsonObject:
    """Copy a caller policy without filling fields from a different version."""
    return deepcopy(dict(policy))


def _mapping(value: object) -> Mapping[str, object]:
    """Return a mapping value or an empty mapping for malformed optional JSON."""
    return value if isinstance(value, Mapping) else {}


def _validate_snapshot(value: Mapping[str, object]) -> None:
    """Reject snapshots from unknown structural versions before scoring them."""
    if value.get("version") != 1:
        raise ValueError("unsupported feed snapshot version")


def _validate_policy(value: Mapping[str, object]) -> None:
    """Reject a policy snapshot whose version or confidence contract is unknown."""
    if value.get("version") != "feed-delta-v1":
        raise ValueError("unsupported feed policy version")
    weights = _mapping(value.get("confidence_weights"))
    required = {"confirmed", "approved", "partial", "self_reported", "pending", "rejected"}
    if not required.issubset(weights) or any(
        not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(float(weight))
        or not 0 <= float(weight) <= 1 for weight in weights.values()
    ):
        raise ValueError("invalid feed policy confidence weights")


def _string_or_none(value: object) -> str | None:
    """Coerce only strings to a stable optional string."""
    return value if isinstance(value, str) else None


def _bool_or_none(value: object) -> bool | None:
    """Coerce only JSON booleans to an optional boolean."""
    return value if isinstance(value, bool) else None


def _number(value: object, fallback: float) -> float:
    """Read a finite numeric policy value, falling back for malformed input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return fallback
    return float(value)


def _normalized_text(value: object) -> str | None:
    """Normalize human text used as a structural key without retaining prose."""
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) or None


def _sorted_strings(value: object) -> list[str]:
    """Return unique normalized strings in canonical order."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return sorted({normalized for item in value if (normalized := _normalized_text(item)) is not None})


def _salary_projection(profile: Mapping[str, object]) -> JsonObject:
    """Normalize candidate and vacancy-compatible salary spellings."""
    salary = _mapping(profile.get("salary"))
    lower = salary.get("from_rub", profile.get("zp_min"))
    upper = salary.get("to_rub", profile.get("zp_max"))
    return {
        "from_rub": int(lower) if isinstance(lower, (int, float)) and not isinstance(lower, bool) and math.isfinite(lower) else None,
        "to_rub": int(upper) if isinstance(upper, (int, float)) and not isinstance(upper, bool) and math.isfinite(upper) else None,
    }


def _skill_projection(value: object) -> list[JsonObject]:
    """Canonicalize skill arrays or key-to-level maps, deduplicating keys."""
    entries: dict[str, JsonObject] = {}
    if isinstance(value, Mapping):
        source: Sequence[object] = [
            {"key": key, "level": level, "status": "self_reported"}
            for key, level in value.items()
        ]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        source = value
    else:
        source = []
    for item in source:
        raw = _mapping(item)
        key = _normalized_text(raw.get("key"))
        level = raw.get("level")
        status = _string_or_none(raw.get("status")) or "pending"
        if key is None or not isinstance(level, (int, float)) or isinstance(level, bool) or not math.isfinite(level):
            continue
        candidate = {"key": key, "level": max(0, min(7, int(level))), "status": status}
        old = entries.get(key)
        if old is None or _skill_sort_key(candidate) > _skill_sort_key(old):
            entries[key] = candidate
    return [entries[key] for key in sorted(entries)]


def _skill_sort_key(value: Mapping[str, object]) -> tuple[int, float, str]:
    """Prefer the strongest level and evidence when malformed input duplicates a key."""
    status = _string_or_none(value.get("status")) or "pending"
    return (int(_number(value.get("level"), 0.0)), _STATUS_WEIGHT.get(status, 0.0), status)


def _narrative_projection(value: object) -> JsonObject:
    """Aggregate typed narrative facts while excluding claim prose and identity."""
    claims: list[Mapping[str, object]] = []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        claims = [_mapping(item) for item in value]
    unique: dict[str, JsonObject] = {}
    for claim in claims:
        facts = _mapping(claim.get("facts"))
        if not facts:
            continue
        typed = _typed_facts(facts)
        signature = json.dumps(_fact_identity(typed), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        status = _string_or_none(claim.get("status")) or "pending"
        old = unique.get(signature)
        if old is None:
            unique[signature] = {"facts": typed, "status": status}
        else:
            old["facts"] = _merge_typed_facts(_mapping(old["facts"]), typed)
            if _STATUS_WEIGHT.get(status, 0.0) > _STATUS_WEIGHT.get(str(old["status"]), 0.0):
                old["status"] = status
    domains = _dedupe_refs([item["facts"]["domains"] for item in unique.values()])
    stacks = _dedupe_refs([item["facts"]["stacks"] for item in unique.values()])
    durations = [item["facts"]["duration_months"] for item in unique.values()
                 if item["facts"]["duration_months"] is not None]
    roles = _dedupe_values([item["facts"]["role"] for item in unique.values()
                            if item["facts"]["role"] is not None])
    production = _dedupe_values([item["facts"]["production_support"] for item in unique.values()
                                 if item["facts"]["production_support"] is not None])
    status_counts = {status: sum(1 for item in unique.values() if item["status"] == status)
                     for status in _NARRATIVE_STATUS_ORDER}
    return {
        "count": len(unique),
        "status_counts": status_counts,
        "domains": domains,
        "stacks": stacks,
        "duration_months": _strongest_duration(durations),
        "roles": roles,
        "production_support": production,
    }


def _typed_facts(facts: Mapping[str, object]) -> JsonObject:
    """Keep the six typed narrative fact channels in a canonical shape."""
    return {
        "domains": _ref_projection(facts.get("domains")),
        "stacks": _ref_projection(facts.get("stacks")),
        "duration_months": _typed_value(facts.get("duration_months"), int),
        "role": _typed_value(facts.get("role"), str),
        "production_support": _typed_value(facts.get("production_support"), bool),
    }


def _fact_identity(facts: Mapping[str, object]) -> JsonObject:
    """Return typed fact values without evidence statuses for duplicate detection."""
    identity: JsonObject = {}
    for group in ("domains", "stacks"):
        refs = facts.get(group)
        ref_items = refs if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)) else []
        identity[group] = sorted(str(_mapping(item).get("key")) for item in ref_items)
    for field in ("duration_months", "role", "production_support"):
        value = _mapping(facts.get(field)).get("value")
        identity[field] = value
    return identity


def _merge_typed_facts(left: Mapping[str, object], right: Mapping[str, object]) -> JsonObject:
    """Merge duplicate fact evidence by retaining the strongest status per field."""
    merged: JsonObject = {
        "domains": _dedupe_refs([left.get("domains", []), right.get("domains", [])]),
        "stacks": _dedupe_refs([left.get("stacks", []), right.get("stacks", [])]),
    }
    for field in ("duration_months", "role", "production_support"):
        values = _dedupe_values([left.get(field), right.get(field)])
        merged[field] = values[0] if values else None
    return merged


def _ref_projection(value: object) -> list[JsonObject]:
    """Project typed references to key and evidence status only."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    refs: dict[str, JsonObject] = {}
    for item in value:
        raw = _mapping(item)
        key = _normalized_text(raw.get("key"))
        status = _string_or_none(raw.get("status")) or "pending"
        if key is not None:
            candidate = {"key": key, "status": status}
            old = refs.get(key)
            if old is None or _STATUS_WEIGHT.get(status, 0.0) > _STATUS_WEIGHT.get(str(old["status"]), 0.0):
                refs[key] = candidate
    return [refs[key] for key in sorted(refs)]


def _typed_value(value: object, expected: type[object]) -> JsonObject | None:
    """Project a typed narrative scalar, rejecting bool/int ambiguity."""
    raw = _mapping(value)
    scalar = raw.get("value")
    if expected is int and (not isinstance(scalar, int) or isinstance(scalar, bool)):
        return None
    if expected is bool and not isinstance(scalar, bool):
        return None
    if expected is str and not isinstance(scalar, str):
        return None
    return {"value": scalar, "status": _string_or_none(raw.get("status")) or "pending"}


def _strongest_duration(values: Sequence[object]) -> JsonObject | None:
    """Use the maximum typed duration, with evidence as a deterministic tie-breaker."""
    candidates = [_mapping(value) for value in values]
    candidates = [value for value in candidates if isinstance(value.get("value"), int)
                  and not isinstance(value.get("value"), bool)]
    if not candidates:
        return None
    best = max(candidates, key=lambda value: (
        int(value["value"]),
        _STATUS_WEIGHT.get(str(value.get("status")), 0.0),
        str(value.get("status", "pending")),
    ))
    return {"value": int(best["value"]), "status": best.get("status", "pending")}


def _dedupe_refs(groups: Sequence[object]) -> list[JsonObject]:
    """Union reference arrays by key, retaining the strongest status."""
    output: dict[str, JsonObject] = {}
    for group in groups:
        for item in group if isinstance(group, Sequence) else []:
            raw = _mapping(item)
            key = _string_or_none(raw.get("key"))
            if key is None:
                continue
            old = output.get(key)
            if old is None or _STATUS_WEIGHT.get(str(raw.get("status")), 0.0) > _STATUS_WEIGHT.get(
                str(old["status"]), 0.0
            ):
                output[key] = {"key": key, "status": raw.get("status", "pending")}
    return [output[key] for key in sorted(output)]


def _dedupe_values(values: Sequence[object]) -> list[JsonObject]:
    """Union typed scalar facts by value, retaining strongest evidence."""
    output: dict[str, JsonObject] = {}
    for value in values:
        raw = _mapping(value)
        if "value" not in raw:
            continue
        key = json.dumps(raw["value"], sort_keys=True, ensure_ascii=False)
        old = output.get(key)
        if old is None or _STATUS_WEIGHT.get(str(raw.get("status")), 0.0) > _STATUS_WEIGHT.get(str(old["status"]), 0.0):
            output[key] = {"value": raw["value"], "status": raw.get("status", "pending")}
    return [output[key] for key in sorted(output)]


def _changes(
    baseline: Mapping[str, object], current: Mapping[str, object], policy: Mapping[str, object]
) -> tuple[list[JsonObject], bool]:
    """Collect scalar changes and whether at least one is professionally positive."""
    changes: list[JsonObject] = []
    positive = False
    old_grade = _mapping(baseline.get("grade"))
    new_grade = _mapping(current.get("grade"))
    grade_old = _number(old_grade.get("ordinal"), 0.0)
    grade_new = _number(new_grade.get("ordinal"), 0.0)
    feature_weights = _mapping(policy.get("feature_weights"))
    grade_step_weight = _number(feature_weights.get("grade_step"), 2.0)
    grade_confidence_weight = _number(feature_weights.get("grade_confidence"), 3.0)
    grade_delta = abs(grade_new - grade_old) * grade_step_weight
    grade_conf_old = _confidence(old_grade.get("status"), policy)
    grade_conf_new = _confidence(new_grade.get("status"), policy)
    grade_delta += abs(grade_conf_new - grade_conf_old) * grade_confidence_weight
    if grade_delta:
        changes.append(_change("grade", "ordinal_or_status", grade_delta, max(grade_conf_old, grade_conf_new)))
        positive |= (grade_conf_new > 0 and grade_new > grade_old) or grade_conf_new > grade_conf_old

    old_direction = _string_or_none(baseline.get("direction"))
    new_direction = _string_or_none(current.get("direction"))
    if old_direction != new_direction:
        changes.append(_change("direction", "value", 1.0, 1.0))

    old_skills = _mapping(baseline.get("skills"))
    new_skills = _mapping(current.get("skills"))
    for group in _SKILL_GROUPS:
        skill_changes, skill_positive = _skill_changes(group, old_skills.get(group), new_skills.get(group), policy)
        changes.extend(skill_changes)
        positive |= skill_positive

    old_salary = _mapping(baseline.get("salary"))
    new_salary = _mapping(current.get("salary"))
    salary_delta = sum(abs(_number(new_salary.get(key), 0.0) - _number(old_salary.get(key), 0.0))
                       / _normalizer(policy, "salary_rub", 1_000_000.0) for key in ("from_rub", "to_rub")) / 2
    if salary_delta:
        changes.append(_change("salary", "range", salary_delta, 1.0))

    for field, group in (("city", "location"), ("work_types", "work_types"), ("relocation", "relocation")):
        delta = _structural_delta(baseline.get(field), current.get(field))
        if delta:
            changes.append(_change(group, field, delta, 1.0))

    old_narrative = _mapping(baseline.get("narrative"))
    new_narrative = _mapping(current.get("narrative"))
    narrative_changes, narrative_positive = _narrative_changes(old_narrative, new_narrative, policy)
    changes.extend(narrative_changes)
    positive |= narrative_positive
    return changes, positive


def _skill_changes(
    group: str, old_value: object, new_value: object, policy: Mapping[str, object]
) -> tuple[list[JsonObject], bool]:
    """Compare a skill group by key, level, and evidence confidence."""
    old_items = old_value if isinstance(old_value, Sequence) and not isinstance(
        old_value, (str, bytes, bytearray)
    ) else []
    new_items = new_value if isinstance(new_value, Sequence) and not isinstance(
        new_value, (str, bytes, bytearray)
    ) else []
    old = {str(_mapping(item).get("key")): _mapping(item) for item in old_items}
    new = {str(_mapping(item).get("key")): _mapping(item) for item in new_items}
    changes: list[JsonObject] = []
    positive = False
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key, {}), new.get(key, {})
        old_level = _number(before.get("level"), 0.0)
        new_level = _number(after.get("level"), 0.0)
        old_conf = _confidence(before.get("status"), policy)
        new_conf = _confidence(after.get("status"), policy)
        delta = abs(new_level - old_level) / _normalizer(policy, "skill_level", 7.0)
        delta += abs(new_conf - old_conf)
        if delta:
            changes.append(_change("skills", f"{group}.{key}", delta, max(old_conf, new_conf)))
            positive |= new_conf > 0 and (new_level > old_level or new_conf > old_conf)
    return changes, positive


def _narrative_changes(
    old: Mapping[str, object], new: Mapping[str, object], policy: Mapping[str, object]
) -> tuple[list[JsonObject], bool]:
    """Compare aggregate narrative fact channels without exposing source prose."""
    changes: list[JsonObject] = []
    positive = False
    for kind in _NARRATIVE_KINDS:
        before, after = _mapping(old.get(kind)), _mapping(new.get(kind))
        count_delta = abs(_number(after.get("count"), 0.0) - _number(before.get("count"), 0.0)) / _normalizer(
            policy, "narrative_count", 10.0
        )
        old_duration = _typed_number(before.get("duration_months"))
        new_duration = _typed_number(after.get("duration_months"))
        duration_delta = abs(new_duration - old_duration) / _normalizer(policy, "duration_months", 120.0)
        duration_delta += abs(_confidence(_mapping(after.get("duration_months")).get("status"), policy)
                              - _confidence(_mapping(before.get("duration_months")).get("status"), policy))
        fact_delta = (
            _structural_delta(before.get("domains"), after.get("domains"))
            + _structural_delta(before.get("stacks"), after.get("stacks"))
            + _structural_delta(before.get("roles"), after.get("roles"))
            + _structural_delta(before.get("production_support"), after.get("production_support"))
            + _structural_delta(before.get("status_counts"), after.get("status_counts"))
        )
        if count_delta:
            changes.append(_change("narrative", f"{kind}.count", count_delta, 1.0))
        if duration_delta:
            changes.append(_change("narrative", f"{kind}.duration_months", duration_delta, 1.0))
        if fact_delta:
            changes.append(_change("narrative", f"{kind}.typed_facts", min(1.0, fact_delta), 1.0))
        positive |= _narrative_positive(before, after, policy)
    return changes, positive


def _narrative_positive(
    before: Mapping[str, object], after: Mapping[str, object], policy: Mapping[str, object]
) -> bool:
    """Recognize additions and confidence gains, excluding replacements and rejection."""
    old_duration = _typed_number(before.get("duration_months"))
    new_duration = _typed_number(after.get("duration_months"))
    old_duration_conf = _confidence(_mapping(before.get("duration_months")).get("status"), policy)
    new_duration_conf = _confidence(_mapping(after.get("duration_months")).get("status"), policy)
    if new_duration > old_duration and new_duration_conf > 0:
        return True
    if new_duration_conf > old_duration_conf:
        return True
    for field in ("domains", "stacks", "roles", "production_support"):
        previous = {_value_identity(item): _mapping(item) for item in _sequence(before.get(field))}
        current = {_value_identity(item): _mapping(item) for item in _sequence(after.get(field))}
        for identity, item in current.items():
            confidence = _confidence(item.get("status"), policy)
            if confidence <= 0:
                continue
            old_item = previous.get(identity)
            if old_item is not None and confidence > _confidence(old_item.get("status"), policy):
                return True
            if old_item is None and previous.keys() <= current.keys():
                return True
    return False


def _sequence(value: object) -> Sequence[object]:
    """Return a JSON sequence while excluding strings and byte sequences."""
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _value_identity(value: object) -> str:
    """Serialize a typed fact value without its evidence status."""
    raw = _mapping(value)
    return json.dumps(raw.get("key", raw.get("value")), sort_keys=True, ensure_ascii=False)


def _typed_number(value: object) -> float:
    """Read a numeric value from an aggregated typed fact."""
    scalar = _mapping(value).get("value")
    return float(scalar) if isinstance(scalar, (int, float)) and not isinstance(scalar, bool) else 0.0


def _confidence(status: object, policy: Mapping[str, object]) -> float:
    """Read a bounded confidence coefficient from the versioned policy."""
    weights = _mapping(policy.get("confidence_weights"))
    name = _string_or_none(status) or "pending"
    return max(0.0, min(1.0, _number(weights.get(name), _STATUS_WEIGHT.get(name, 0.0))))


def _normalizer(policy: Mapping[str, object], key: str, fallback: float) -> float:
    """Return a positive finite normalizer from the policy snapshot."""
    value = _number(_mapping(policy.get("normalizers")).get(key), fallback)
    return value if value > 0 else fallback


def _change(group: str, field: str, delta: float, confidence: float) -> JsonObject:
    """Create one internal bounded explanation record."""
    return {"group": group, "field": field, "delta": max(0.0, delta), "confidence": max(0.0, min(1.0, confidence))}


def _structural_delta(old: object, new: object) -> float:
    """Return a bounded symmetric delta for scalar, mapping, and sequence values."""
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        keys = set(old) | set(new)
        return min(1.0, sum(_structural_delta(old.get(key), new.get(key)) for key in keys) / max(1, len(keys)))
    if (
        isinstance(old, Sequence)
        and isinstance(new, Sequence)
        and not isinstance(old, (str, bytes, bytearray))
        and not isinstance(new, (str, bytes, bytearray))
    ):
        left = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in old}
        right = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in new}
        union = left | right
        return 0.0 if not union else len(left ^ right) / len(union)
    return 0.0 if old == new else 1.0


def _group_summary(explanations: Sequence[JsonObject], policy: Mapping[str, object]) -> dict[str, JsonObject]:
    """Apply confidence, group weights, and caps to explanation records."""
    weights = _mapping(policy.get("group_weights"))
    caps = _mapping(policy.get("group_caps"))
    groups: dict[str, JsonObject] = {}
    for item in explanations:
        group = str(item["group"])
        raw = float(item["delta"]) * float(item["confidence"])
        entry = groups.setdefault(group, {"raw": 0.0, "weighted": 0.0, "capped": 0.0})
        entry["raw"] = float(entry["raw"]) + float(item["delta"])
        entry["weighted"] = float(entry["weighted"]) + raw * _number(weights.get(group), 1.0)
    for group, entry in groups.items():
        cap = max(0.0, _number(caps.get(group), float("inf")))
        entry["raw"] = _round(float(entry["raw"]), policy)
        entry["weighted"] = _round(float(entry["weighted"]), policy)
        entry["capped"] = _round(min(float(entry["weighted"]), cap), policy)
    return dict(sorted(groups.items()))


def _round(value: float, policy: Mapping[str, object]) -> float:
    """Round policy output to its configured finite precision."""
    digits = max(0, min(12, int(_number(policy.get("round_digits"), 6.0))))
    return round(value, digits)


def _explanation_sort_key(item: JsonObject) -> tuple[float, str, str]:
    """Sort strongest explanations first with stable structural tie-breakers."""
    return (-float(item["delta"]) * float(item["confidence"]), str(item["group"]), str(item["field"]))
