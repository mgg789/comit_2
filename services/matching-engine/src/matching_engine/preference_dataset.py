"""Sealed, temporal observation partitions for vacancy-local shadow learning."""

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from matching_engine.combine_store import _lock
from matching_engine.database import connect
from matching_engine.learning_store import DATASET_VERSION, pseudonym
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import RerankArtifact

PLAN_VERSION = "vacancy-preference-temporal-v1"
MAX_ROWS = 100000
MAX_EXAMPLES = 10000
IDENTITY_FIELDS = ("owner_group", "vacancy_group", "vacancy_revision",
                   "strict_schema_hash", "feature_schema_hash", "main_policy_hash")
HEX = re.compile(r"[0-9a-f]{64}")


def timestamp(value: str) -> datetime:
    """Parse an explicit timezone-aware event timestamp; publication is never a label time."""
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("observation timestamp must include timezone")
    return result


def _hash(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def read_observations(directory: Path, baseline: dict, *, allow_synthetic: bool = False,
                      after: str | None = None) -> tuple[list[dict], dict]:
    """Verify a complete private export, freeze first decisions, quarantine contradictions.

    One candidate supplies at most one example per exact vacancy/model context,
    across profile revisions and sessions. First explicit reaction wins only when
    all observed reactions agree; contradictions remove the whole candidate group.
    No-action, server allocation and nonlearned baseline cards supply no labels.
    This local temporal protocol does not train a global R₀ or estimate causality.
    """
    artifact = RerankArtifact.model_validate(baseline)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("manifest_hash") != digest({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise ValueError("export manifest integrity mismatch")
    if manifest.get("dataset_version") != DATASET_VERSION or manifest.get("truncated") is not False:
        raise ValueError("unsupported or incomplete export")
    source = manifest.get("source")
    if source not in ("operational", "synthetic") or (source == "synthetic" and not allow_synthetic):
        raise ValueError("synthetic training requires explicit opt-in")
    if source == "operational" and manifest.get("consent_scope") != "authoritative_api_and_matching":
        raise ValueError("operational export requires authoritative consent")
    cutoff = timestamp(after) if after is not None else None
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    contexts: dict[str, dict] = {}
    counts: Counter = Counter()
    checksum = hashlib.sha256()
    with (directory / "observations.jsonl").open("rb") as handle:
        for line in handle:
            checksum.update(line)
            counts["rows"] += 1
            if counts["rows"] > MAX_ROWS or len(line) > 1000000:
                raise ValueError("observation import bound exceeded")
            row = json.loads(line)
            if row.get("source") != source or row.get("dataset_version") != DATASET_VERSION:
                raise ValueError("mixed dataset sources or versions")
            features = row["features"]
            if digest(features) != row["feature_hash"]:
                raise ValueError("feature snapshot integrity mismatch")
            names, values, available = features["names"], features["values"], features["available"]
            if (names != artifact.feature_names or features["schema_hash"] != artifact.feature_schema_hash
                    or row["feature_schema_hash"] != artifact.feature_schema_hash
                    or row["strict_schema_hash"] != artifact.strict_schema_hash
                    or row["main_policy_hash"] != artifact.main_policy_hash):
                raise ValueError("baseline feature or policy incompatibility")
            if (len(values) != len(names) or len(available) != len(names)
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in values)
                    or any(type(x) is not bool for x in available)):
                raise ValueError("invalid numeric feature snapshot")
            if any(not _hash(row[field]) for field in ("owner_group", "vacancy_group", "candidate_group", "main_run_id")):
                raise ValueError("invalid pseudonymous group identity")
            if type(row["vacancy_revision"]) is not int or row["vacancy_revision"] < 1:
                raise ValueError("invalid vacancy revision")
            for exposure in row["exposures"]:
                action = exposure["action"]
                label = exposure["label"]
                if action is None:
                    if label is not None or exposure["label_kind"] != "unlabeled":
                        raise ValueError("no-action observation cannot carry a label")
                    counts["unlabeled"] += 1
                    continue
                if (action not in ("like", "dislike") or type(label) is not int
                        or label != int(action == "like") or exposure["label_kind"] != "explicit_behavior"):
                    raise ValueError("invalid explicit reaction label")
                counts["explicit_decisions"] += 1
                events = exposure["events"]
                decision_events = [event for event in events if event["kind"] in ("like", "dislike")]
                delivered = [timestamp(event["at"]) for event in events if event["kind"] == "delivered"]
                if len(decision_events) != 1 or decision_events[0]["kind"] != action or not delivered:
                    raise ValueError("explicit reaction requires one action and actual delivery")
                at = timestamp(decision_events[0]["at"])
                if at < min(delivered):
                    raise ValueError("reaction precedes delivery")
                if cutoff is not None and at <= cutoff:
                    counts["before_cutoff"] += 1
                    continue
                if exposure["mode"] != "learned" or exposure["artifact_hash"] != artifact.content_hash:
                    counts["incompatible_baseline_exposures"] += 1
                    continue
                score = exposure["rerank_score"]
                if type(score) not in (int, float) or not math.isfinite(score):
                    raise ValueError("nonfinite baseline score")
                expected = math.fsum(coefficient * ((value - mean) / scale if observed else 0.)
                                     for coefficient, value, mean, scale, observed in zip(
                                         artifact.coefficients, values, artifact.means, artifact.scales, available, strict=True))
                if not math.isclose(score, expected, rel_tol=1e-9, abs_tol=1e-9):
                    raise ValueError("baseline score differs from frozen features")
                if not _hash(exposure["exposure_id"]) or type(exposure["activation_revision"]) is not int:
                    raise ValueError("invalid exposure identity")
                identity = {field: row[field] for field in IDENTITY_FIELDS}
                identity.update(artifact_hash=exposure["artifact_hash"],
                                activation_revision=exposure["activation_revision"],
                                approval_receipt_hash=exposure.get("approval_receipt_hash"))
                context_key = digest(identity)
                contexts[context_key] = identity
                grouped[(context_key, row["candidate_group"])].append({
                    "features": features, "baseline_score": float(score), "label": label,
                    "candidate_key": row["candidate_group"], "profile_revision": row["profile_revision"],
                    "context_key": context_key, "exposure_key": exposure["exposure_id"],
                    "decision_at": at.isoformat(), "feature_hash": row["feature_hash"],
                })
                counts["compatible_decisions"] += 1
                if counts["compatible_decisions"] > MAX_EXAMPLES:
                    raise ValueError("explicit observation bound exceeded")
    if (checksum.hexdigest() != manifest["observations_sha256"]
            or counts["rows"] != manifest["rows"]
            or counts["explicit_decisions"] != manifest["explicit_decisions"]):
        raise ValueError("export observations checksum or counts mismatch")
    examples = []
    for values in grouped.values():
        if len({value["label"] for value in values}) != 1:
            counts["conflicting_candidates"] += 1
            continue
        values.sort(key=lambda item: (timestamp(item["decision_at"]), item["exposure_key"]))
        examples.append(values[0])
        counts["duplicate_decisions"] += len(values) - 1
    examples.sort(key=lambda item: (item["context_key"], timestamp(item["decision_at"]), item["candidate_key"]))
    summary = {"source": source, "manifest_hash": manifest["manifest_hash"],
               "dataset_hash": manifest["observations_sha256"], "baseline_hash": artifact.content_hash,
               "pseudonym_key_fingerprint": manifest.get("pseudonym_key_fingerprint"),
               "after": after, "counts": dict(counts), "contexts": contexts,
               "eligible_examples": len(examples), "propensity_available": False}
    return examples, summary


@contextmanager
def current_consent(examples: list[dict], summary: dict, database_url: str | None, key: bytes | None):
    """Recheck operational consent and retain source row locks until shadow fit/write completes.

    A sealed historical export is not proof of current consent. Operational fit
    requires the export's private HMAC key and authoritative API database, without
    copying IDs or the key into reports. Synthetic simulations need neither.
    """
    if summary["source"] == "synthetic":
        yield "synthetic_only"
        return
    if not database_url or key is None or len(key) < 32:
        raise ValueError("operational fit requires authoritative database and private pseudonym key")
    if hashlib.sha256(key).hexdigest() != summary["pseudonym_key_fingerprint"]:
        raise ValueError("operational export pseudonym key mismatch")
    with connect(database_url) as connection:
        _lock(connection)
        if not connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]:
            raise ValueError("authoritative consent table is required")
        eligible = set()
        with connection.cursor(name="preference_consent_guard") as cursor:
            cursor.execute("""SELECT c.candidate_id FROM candidate_pipeline.candidates c
                JOIN public.candidate_profiles p USING(candidate_id)
                WHERE p.processing_consent AND p.pool_consent
                  AND (c.profile->'consents'->>'processing')::boolean IS TRUE
                  AND (c.profile->'consents'->>'pool')::boolean IS TRUE FOR SHARE OF p""")
            for row in cursor:
                eligible.add(pseudonym(key, "candidate", row["candidate_id"]))
                if len(eligible) > MAX_ROWS:
                    raise ValueError("consent revalidation bound exceeded")
        if not {row["candidate_key"] for row in examples}.issubset(eligible):
            raise ValueError("consent changed since export; create a new export and plan")
        yield "authoritative_api_and_matching_locked"


def partition(examples: list[dict]) -> dict[str, list[dict]]:
    """Partition first decisions chronologically 60/20/20 without splitting a timestamp tie."""
    ordered = sorted(examples, key=lambda item: (timestamp(item["decision_at"]), item["candidate_key"]))
    n = len(ordered)
    first, second = int(n * .6), int(n * .8)
    while 0 < first < n and timestamp(ordered[first - 1]["decision_at"]) == timestamp(ordered[first]["decision_at"]):
        first += 1
    second = max(first, second)
    while 0 < second < n and timestamp(ordered[second - 1]["decision_at"]) == timestamp(ordered[second]["decision_at"]):
        second += 1
    return {"train": ordered[:first], "validation": ordered[first:second], "golden": ordered[second:]}


def make_plan(examples: list[dict], summary: dict) -> dict:
    """Seal context/candidate assignments before training; no coefficients or labels tune the split."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for example in examples:
        groups[example["context_key"]].append(example)
    assignments = {key: {part: [value["candidate_key"] for value in values]
                         for part, values in partition(items).items()} for key, items in sorted(groups.items())}
    plan = {"version": PLAN_VERSION, "summary": summary, "assignments": assignments,
            "scope": "vacancy_local_temporal_unseen_candidate",
            "owner_vacancy_overlap": "intentional_within_local_model_only",
            "global_r0_training": False, "live_activation": False,
            "example_hash": digest(examples)}
    plan["plan_hash"] = digest(plan)
    return plan


def verify_plan(plan: dict, examples: list[dict], summary: dict) -> dict[str, dict]:
    """Reconstruct the exact preregistered plan; edits or a changed export require a new plan."""
    if plan != make_plan(examples, summary):
        raise ValueError("sealed preference plan mismatch")
    lookup = {(row["context_key"], row["candidate_key"]): row for row in examples}
    return {key: {"identity": summary["contexts"][key],
                  **{part: [lookup[(key, candidate)] for candidate in candidates]
                     for part, candidates in assignments.items()}}
            for key, assignments in plan["assignments"].items()}
