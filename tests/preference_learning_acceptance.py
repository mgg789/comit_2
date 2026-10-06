"""Numeric shadow-learning acceptance; no new candidate profiles, teachers or model promotion."""

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from matching_engine.learning_store import DATASET_VERSION
from matching_engine.preference_experiment import baseline_from, write_private
from matching_engine.rerank_hash import digest

POLICIES = (("grade", 1.), ("grade", -1.), ("salary_affordability", 1.),
            ("stack.proximity", 1.), ("experience.production", 1.), ("semantic_cosine", 1.))


def simulate(baseline: dict) -> list[dict]:
    """Simulate scalar signals/reactions, not plausible new profiles or independent human judgments.

    Full snapshots reuse frozen R₀ feature order/statistics. These numeric
    simulations prove fitting and provenance mechanics only. Actual candidate
    generation and the earlier teacher budget are untouched.
    """
    rows = []
    rng = random.Random(1701)
    for context, (target, direction) in enumerate(POLICIES):
        target_index = baseline["feature_names"].index(target)
        for index in range(80):
            positive = index % 2 == 1
            features = {"names": baseline["feature_names"], "values": list(baseline["means"]),
                        "available": [True] * len(baseline["means"]), "schema_hash": baseline["feature_schema_hash"]}
            # Small deterministic nuisance differences make each candidate unique.
            for name in ("rating", "grade", "salary_affordability", "stack.proximity", "semantic_cosine"):
                slot = baseline["feature_names"].index(name)
                features["values"][slot] += rng.uniform(-.15, .15) * baseline["scales"][slot]
            sign = direction * (1 if positive else -1)
            features["values"][target_index] += sign * baseline["scales"][target_index] * rng.uniform(.7, 1.)
            if context in (0, 1):
                # Class-only masking cannot create a learned missing-skill penalty.
                for slot, name in enumerate(baseline["feature_names"]):
                    if name.startswith(("domain.", "stack.", "soft_skill.", "available.domain.", "available.stack.", "available.soft_skill.")):
                        features["available"][slot] = False
                        features["values"][slot] = 0.
            score = math.fsum(w * ((x - mean) / scale if known else 0.) for w, x, mean, scale, known in zip(
                baseline["coefficients"], features["values"], baseline["means"], baseline["scales"], features["available"], strict=True))
            at = (datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=context, minutes=index)).isoformat()
            action = "like" if positive else "dislike"
            row = {"dataset_version": DATASET_VERSION, "source": "synthetic", "simulation": "numeric_signals_only",
                "main_run_id": digest(f"main-{context}"), "owner_group": digest(f"owner-{context}"),
                "vacancy_group": digest(f"vacancy-{context}"), "candidate_group": digest(f"candidate-{context}-{index}"),
                "vacancy_revision": 1, "profile_revision": 1, "published_at": at,
                "feature_hash": digest(features), "features": features,
                "feature_schema_hash": baseline["feature_schema_hash"], "strict_schema_hash": baseline["strict_schema_hash"],
                "main_policy_hash": baseline["main_policy_hash"], "exposures": [{
                    "exposure_id": digest(f"exposure-{context}-{index}"), "mode": "learned", "rerank_score": score,
                    "artifact_hash": baseline["content_hash"], "activation_revision": 1,
                    "approval_receipt_hash": digest("numeric-simulation-receipt"), "action": action,
                    "label": int(positive), "label_kind": "explicit_behavior",
                    "events": [{"kind": "delivered", "at": at}, {"kind": action, "at": at}],
                }]}
            rows.append(row)
    return rows


def main() -> None:
    """Execute the real plan/fit CLI twice against one sealed numeric simulation, never production."""
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Numeric preference acceptance, not candidate generation")
    parser.add_argument("--output", type=Path, default=root / "tests/artifacts/preferences/numeric-acceptance-v2")
    args = parser.parse_args()
    directory = args.output.resolve()
    if not directory.is_relative_to((root / "tests/artifacts/preferences").resolve()):
        parser.error("acceptance output must remain inside tests/artifacts/preferences")
    baseline = baseline_from(None)
    rows = simulate(baseline)
    raw = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    manifest = {"dataset_version": DATASET_VERSION, "source": "synthetic", "rows": len(rows),
                "explicit_decisions": len(rows), "truncated": False,
                "observations_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "consent_scope": "synthetic_matching_only", "simulation": "numeric_signals_only"}
    manifest["manifest_hash"] = digest(manifest)
    write_private(directory / "export", "manifest.json", manifest)
    target = directory / "export/observations.jsonl"
    with target.open("x", encoding="utf-8") as handle:
        target.chmod(0o600)
        handle.write(raw)
    command = [sys.executable, "-m", "matching_engine.preference_experiment"]
    subprocess.run([*command, "plan", "--export", str(directory / "export"), "--allow-synthetic",
                    "--output", str(directory / "plan")], cwd=root, check=True)
    reports = []
    for suffix in ("fit", "reproduction"):
        subprocess.run([*command, "fit", "--export", str(directory / "export"), "--allow-synthetic",
                        "--plan", str(directory / "plan/plan.json"), "--output", str(directory / suffix)], cwd=root, check=True)
        reports.append(json.loads((directory / suffix / "report.json").read_text()))
    assert reports[0] == reports[1], "shadow fit must be byte-reproducible"
    report = reports[0]
    assert report["live_activation"] is False
    assert report["source"] == "synthetic"
    assert report["state"] == "shadow_test_only", report["contexts"]
    assert report["model_count"] == len(POLICIES)
    assert all(context["fit"]["diagnostics"]["converged"] for context in report["contexts"].values())
    print(json.dumps({"state": "numeric_acceptance_completed", "observation_rows": len(rows),
                      "simulation": "numeric_signals_only", "reproducible": True,
                      "model_report_hash": report["report_hash"], "network_model_calls": 0,
                      "new_candidate_profiles": 0, "live_activation": False}))


if __name__ == "__main__":
    main()
