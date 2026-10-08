"""Golden regression for the preference fit/apply math (deterministic drift guard).

Builds 24 fixed synthetic snapshots over the packaged artifact's real feature
schema, fits the real preference_model.train() and applies the real apply(),
then compares a digest of the resulting parameters against the committed
fixture. Any silent change to the fit math breaks this test; intentional
changes require UPDATE_GOLDEN=1 regeneration with the diff explained in the
commit body (the combine golden regression convention).
"""

import json
import os
from pathlib import Path

from matching_engine.preference_model import apply, train
from matching_engine.rerank_hash import digest

FIXTURE = Path(__file__).parent / "fixtures" / "preference_golden.json"
ARTIFACT = (Path(__file__).parents[1] / "src" / "matching_engine" / "artifacts"
            / "r0-c18f4bc1.json")
EXAMPLES = 24


def _examples(artifact: dict) -> list[dict]:
    names = artifact["feature_names"]
    rows = []
    for index in range(EXAMPLES):
        values = [0.0] * len(names)
        available = [False] * len(names)
        for position in range(index % 7, len(names), 13):
            values[position] = (((7 * index + 3 * position) % 13) - 6) / 6
            available[position] = True
        rows.append({
            "features": {"names": names, "values": values, "available": available,
                         "schema_hash": artifact["feature_schema_hash"]},
            "baseline_score": ((3 * index) % 7) / 7.0,
            "label": index % 2,
            "candidate_key": "golden-" + str(index)})
    return rows


def _fingerprint(artifact: dict) -> dict:
    model = train(_examples(artifact), artifact)
    snapshot = _examples(artifact)[3]["features"]
    result = apply(snapshot, 0.5, model, artifact)
    return {
        "content_hash": model.content_hash,
        "gamma": model.gamma,
        "trust": model.trust,
        "calibration_intercept": model.calibration_intercept,
        "calibration_slope": model.calibration_slope,
        "coefficients_digest": digest(model.coefficients),
        "means_digest": digest(model.means),
        "scales_digest": digest(model.scales),
        "diagnostics": {"n_examples": model.diagnostics["n_examples"],
                        "iterations": model.diagnostics["iterations"],
                        "class_balance": model.diagnostics["class_balance"]},
        "apply": {"score": result["score"], "adjustment": result["adjustment"],
                  "uncertainty": result["uncertainty"],
                  "contributions_digest": digest(result["contributions"])},
    }


def test_preference_math_matches_golden_fixture() -> None:
    """Fit and apply stay byte-stable; regenerate only with UPDATE_GOLDEN=1."""
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    fingerprint = _fingerprint(artifact)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        FIXTURE.write_text(json.dumps(fingerprint, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
    assert FIXTURE.exists(), "fixture missing; run with UPDATE_GOLDEN=1 once"
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fingerprint == golden
