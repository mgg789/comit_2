"""Sealed temporal adapter tests; synthetic vectors are explicitly numeric simulations."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from matching_engine.learning_store import DATASET_VERSION
from matching_engine.preference_dataset import (
    current_consent,
    make_plan,
    partition,
    read_observations,
    verify_plan,
)
from matching_engine.preference_experiment import baseline_from, write_private
from matching_engine.rerank_hash import digest


def synthetic_rows(n: int = 40) -> list[dict]:
    """Build numeric collector-contract examples without generating candidate profiles or API calls."""
    baseline = baseline_from(None)
    rows = []
    for index in range(n):
        features = {"names": baseline["feature_names"], "values": list(baseline["means"]),
                    "available": [True] * len(baseline["means"]), "schema_hash": baseline["feature_schema_hash"]}
        at = (datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=index)).isoformat()
        action = "like" if index % 2 else "dislike"
        rows.append({"dataset_version": DATASET_VERSION, "source": "synthetic",
            "main_run_id": digest("main"), "owner_group": digest("owner"), "vacancy_group": digest("vacancy"),
            "candidate_group": digest(f"candidate-{index}"), "vacancy_revision": 1, "profile_revision": 1,
            "published_at": at, "feature_hash": digest(features), "features": features,
            "feature_schema_hash": baseline["feature_schema_hash"], "strict_schema_hash": baseline["strict_schema_hash"],
            "main_policy_hash": baseline["main_policy_hash"], "exposures": [{
                "exposure_id": digest(f"exposure-{index}"), "mode": "learned", "rerank_score": 0.,
                "artifact_hash": baseline["content_hash"], "activation_revision": 1,
                "approval_receipt_hash": digest("receipt"), "action": action, "label": int(action == "like"),
                "label_kind": "explicit_behavior", "events": [{"kind": "delivered", "at": at}, {"kind": action, "at": at}],
            }]})
    return rows


def save_export(directory: Path, rows: list[dict], **changes) -> None:
    """Create a complete synthetic export with real hashing semantics and private files."""
    directory.mkdir(mode=0o700)
    raw = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    (directory / "observations.jsonl").write_text(raw)
    manifest = {"dataset_version": DATASET_VERSION, "source": "synthetic", "rows": len(rows),
                "explicit_decisions": sum(e["action"] is not None for row in rows for e in row["exposures"]),
                "truncated": False, "observations_sha256": hashlib.sha256(raw.encode()).hexdigest(), **changes}
    manifest["manifest_hash"] = digest(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest))


def load(directory: Path):
    """Opt into synthetic examples only for these isolated tests."""
    return read_observations(directory, baseline_from(None), allow_synthetic=True)


def test_plan_is_frozen_before_fit_and_has_temporal_candidate_separation(tmp_path: Path):
    save_export(tmp_path / "data", synthetic_rows())
    examples, summary = load(tmp_path / "data")
    plan = make_plan(examples, summary)
    context = next(iter(verify_plan(plan, examples, summary).values()))
    assert [len(context[k]) for k in ("train", "validation", "golden")] == [24, 8, 8]
    assert max(x["decision_at"] for x in context["train"]) < min(x["decision_at"] for x in context["validation"])
    assert not {x["candidate_key"] for x in context["train"]} & {x["candidate_key"] for x in context["golden"]}
    plan["assignments"][next(iter(plan["assignments"]))]["golden"].clear()
    with pytest.raises(ValueError, match="plan mismatch"):
        verify_plan(plan, examples, summary)


def test_duplicates_cannot_cross_partitions_and_conflicting_candidate_is_quarantined(tmp_path: Path):
    rows = synthetic_rows()
    repeat = deepcopy(rows[0])
    repeat["exposures"][0]["exposure_id"] = digest("repeat")
    rows.append(repeat)
    contradiction = deepcopy(rows[1])
    exposure = contradiction["exposures"][0]
    exposure.update(action="dislike", label=0, exposure_id=digest("contradiction"))
    exposure["events"][-1]["kind"] = "dislike"
    rows.append(contradiction)
    save_export(tmp_path / "data", rows)
    examples, summary = load(tmp_path / "data")
    assert len(examples) == 39
    assert summary["counts"]["duplicate_decisions"] == 1
    assert summary["counts"]["conflicting_candidates"] == 1


def test_no_action_is_unlabeled_and_revision_gets_own_model_context(tmp_path: Path):
    rows = synthetic_rows(3)
    rows[0]["exposures"][0].update(action=None, label=None, label_kind="unlabeled", events=[])
    rows[1]["vacancy_revision"] = 2
    save_export(tmp_path / "data", rows)
    examples, summary = load(tmp_path / "data")
    assert len(examples) == 2 and len(make_plan(examples, summary)["assignments"]) == 2
    assert summary["counts"]["unlabeled"] == 1


@pytest.mark.parametrize("change", ["bytes", "feature_hash", "truncated", "source", "delivery", "baseline_schema"])
def test_corrupt_or_incomplete_inputs_fail_closed(tmp_path: Path, change: str):
    rows = synthetic_rows(1)
    changes = {}
    if change == "feature_hash":
        rows[0]["feature_hash"] = "invalid"
    if change == "truncated":
        changes["truncated"] = True
    if change == "source":
        rows[0]["source"] = "operational"
    if change == "delivery":
        rows[0]["exposures"][0]["events"].pop(0)
    if change == "baseline_schema":
        rows[0]["feature_schema_hash"] = "wrong"
    save_export(tmp_path / "data", rows, **changes)
    if change == "bytes":
        with (tmp_path / "data/observations.jsonl").open("a") as handle:
            handle.write("\n")
    with pytest.raises((ValueError, KeyError)):
        load(tmp_path / "data")


def test_synthetic_opt_in_reset_cutoff_and_no_overwrite(tmp_path: Path):
    save_export(tmp_path / "data", synthetic_rows())
    with pytest.raises(ValueError, match="opt-in"):
        read_observations(tmp_path / "data", baseline_from(None))
    examples, summary = read_observations(tmp_path / "data", baseline_from(None), allow_synthetic=True,
                                         after="2026-10-01T00:20:00+00:00")
    assert len(examples) == 19 and summary["counts"]["before_cutoff"] == 21
    write_private(tmp_path / "private", "plan.json", make_plan(examples, summary))
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "private/plan.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private(tmp_path / "private", "plan.json", {})


def test_timestamp_ties_do_not_cross_partitions():
    rows = [{"candidate_key": str(i), "decision_at": "2026-10-01T00:00:00Z"} for i in range(40)]
    parts = partition(rows)
    assert len(parts["train"]) == 40
    assert parts["validation"] == parts["golden"] == []


def test_real_fit_cannot_trust_old_export_consent_without_authority():
    with (pytest.raises(ValueError, match="authoritative"),
          current_consent([], {"source": "operational"}, None, None)):
        pytest.fail("operational fit must fail closed")
    with current_consent([], {"source": "synthetic"}, None, None) as state:
        assert state == "synthetic_only"
