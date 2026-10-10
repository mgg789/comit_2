"""Explicit operator commands for sealed shadow training; never mutate a live feed."""

import argparse
import json
import os
from importlib.resources import files
from pathlib import Path

from matching_engine.preference_dataset import (
    current_consent,
    make_plan,
    read_observations,
    verify_plan,
)
from matching_engine.rerank_model import validate_artifact


def write_private(directory: Path, name: str, value: dict) -> None:
    """Create a new private result; an existing experiment cannot be overwritten."""
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    target = directory / name
    with target.open("x", encoding="utf-8") as handle:
        os.chmod(target, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def baseline_from(path: Path | None) -> dict:
    """Read a hash-checked R₀; the default is the exact reviewed packaged artifact."""
    raw = path.read_text() if path is not None else files("matching_engine").joinpath("artifacts/r0-c18f4bc1.json").read_text()
    return validate_artifact(json.loads(raw))


def main() -> None:
    """Freeze a temporal plan, then fit explicitly; synthetic inputs need an explicit flag."""
    parser = argparse.ArgumentParser(description="Vacancy preference shadow experiment")
    parser.add_argument("mode", choices=("plan", "fit"))
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--after", help="Ignore reactions at/before this timezone-aware reset cutoff")
    parser.add_argument("--allow-synthetic", action="store_true")
    parser.add_argument("--key-file", type=Path, help="Original private HMAC key; operational fit only")
    args = parser.parse_args()
    if args.mode == "fit" and args.plan is None:
        parser.error("fit requires a previously saved --plan")
    baseline = baseline_from(args.baseline)
    examples, summary = read_observations(args.export, baseline, allow_synthetic=args.allow_synthetic, after=args.after)
    if args.mode == "plan":
        value = make_plan(examples, summary)
        name = "plan.json"
        compact = {"state": "plan_sealed", "contexts": len(value["assignments"]),
                   "eligible_examples": len(examples), "source": summary["source"], "plan_hash": value["plan_hash"]}
    else:
        from matching_engine.preference_evaluation import evaluate
        plan = json.loads(args.plan.read_text())
        contexts = verify_plan(plan, examples, summary)
        key = None
        if args.key_file is not None:
            if args.key_file.stat().st_mode & 0o077:
                parser.error("pseudonym key file must be private")
            key = args.key_file.read_bytes()
        with current_consent(examples, summary, os.environ.get("DATABASE_URL"), key) as consent:
            value = evaluate(contexts, baseline, source=summary["source"],
                             dataset_hash=summary["dataset_hash"], split_hash=plan["plan_hash"])
            value["data_quality"] = summary["counts"]
            value["consent_revalidation"] = consent
            # Recompute the final report seal after attaching quality counts.
            from matching_engine.rerank_hash import digest
            value.pop("report_hash", None)
            value["report_hash"] = digest(value)
            write_private(args.output, "report.json", value)
        print(json.dumps({"state": value["state"], "source": summary["source"],
                          "live_activation": False, "report_hash": value["report_hash"]}, sort_keys=True))
        return
    write_private(args.output, name, value)
    print(json.dumps(compact, sort_keys=True))


if __name__ == "__main__":
    main()
