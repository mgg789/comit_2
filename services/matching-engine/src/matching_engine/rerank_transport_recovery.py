"""Bounded transport diagnosis with a distinct cache identity and historical receipts intact."""

import argparse
import fcntl
import json
from pathlib import Path

from matching_engine.rerank_diagnostics import REVERSE, _read, _seal
from matching_engine.rerank_gemma_format import _attempt_receipt
from matching_engine.rerank_gemma_pilot import _verified
from matching_engine.rerank_hash import digest
from matching_engine.rerank_pair_teacher import (
    COMPARATIVE_MODELS,
    ComparativeTeacherProviders,
    CompatibleTeacherProviders,
)
from matching_engine.rerank_providers import LIMITS
from matching_engine.rerank_teacher_comparison import prepare_comparison


class CompatibilityProbeProviders(ComparativeTeacherProviders):
    """Reproduce the v3 wire payload once without overwriting previously sealed failure receipts."""

    def _identity(self, path: str, payload: dict, kind: str) -> dict:
        return {**super()._identity(path, payload, kind), "experiment": "rerank-http-compatibility-probe-v1"}


def run_probe(directory: Path) -> dict:
    """Reproduce one original request inside the remaining diagnostic allowance; never implicitly retry."""
    parent = prepare_comparison(directory)
    case = parent["cases"][1]
    state = case["state"]
    teacher = CompatibilityProbeProviders(directory / "provider-cache")
    args = (state["vacancy"], state["A"], state["B"], state["preferences"])
    identity = teacher._identity("/v1/chat/completions", teacher._pair_payload(*args), "pair")
    key = digest(identity)
    target = directory / "diagnostic-http-compatibility-v1"
    manifest = {"source_manifest_hash": parent["manifest_hash"], "case_id": case["case_id"],
                "request": identity, "request_hash": key, "maximum_attempts": 1,
                "purpose": "Reproduce HTTP rejection; same v3 wire payload, distinct experiment identity"}
    manifest["manifest_hash"] = digest(manifest)
    _seal(target / "manifest.json", manifest)
    cache = directory / "provider-cache"
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            result = _verified(target / "report.json", "report_hash")
            if result["manifest_hash"] != manifest["manifest_hash"] or result["raw_receipt"] != _attempt_receipt(directory, key):
                raise ValueError("compatibility probe receipt changed")
            return result
        if (target / "started.json").exists():
            raise ValueError("interrupted compatibility probe requires operator review")
        before = _read(cache / "usage.json")
        spent = before.get("pair", 0) - parent["diagnostic_pair_baseline"]
        if spent < 0 or spent + 1 > parent["diagnostic_allowance"] or before.get("pair", 0) + 1 > LIMITS["pair"]:
            raise ValueError("compatibility probe exceeds diagnostic budget")
        if (cache / f"{key}.json").exists() or list(cache.glob(f"{key}.failure-*.json")):
            raise ValueError("unpublished compatibility probe requires operator review")
        old = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": manifest["manifest_hash"], "ledger_before": before})
        answer, error = None, None
        try:
            answer = teacher.judge(*args)
        except Exception as exc:  # noqa: BLE001 - raw body/headers/exception text never leave the transport.
            error = type(exc).__name__
        after = _read(cache / "usage.json")
        result = {"manifest_hash": manifest["manifest_hash"], "answer": answer, "error_type": error,
                  "raw_receipt": _attempt_receipt(directory, key), "ledger_before": before, "ledger_after": after,
                  "historical_cache_bytes_preserved": all((cache / name).exists()
                      and digest((cache / name).read_bytes().hex()) == value for name, value in old.items()),
                  "budget_preserved": after.get("pair", 0) - before.get("pair", 0) == 1
                      and all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade")),
                  "training_started": False, "activation_allowed": False}
        result["report_hash"] = digest(result)
        _seal(target / "report.json", result)
        return result


def run_recovery(directory: Path) -> dict:
    """Validate the portable schema on one frozen ambiguous pair for both models, at most four calls.

    First failure stops new calls. Every successful reply is validated by the
    unchanged strict local validator, and completed runs replay without HTTP.
    This targeted repair check cannot substitute for the complete quality pilot.
    """
    parent = prepare_comparison(directory)
    case = parent["cases"][0]
    state = case["state"]
    cache = directory / "provider-cache"
    requests, jobs = [], []
    for model in COMPARATIVE_MODELS:
        teacher = CompatibleTeacherProviders(cache, model=model)
        for orientation, a, b in (("direct", state["A"], state["B"]), ("reverse", state["B"], state["A"])):
            args = (state["vacancy"], a, b, state["preferences"])
            identity = teacher._identity("/v1/chat/completions", teacher._pair_payload(*args), "pair")
            requests.append({"model": model, "orientation": orientation, "request": identity, "request_hash": digest(identity)})
            jobs.append((teacher, args))
    target = directory / "diagnostic-wire-recovery-v4"
    manifest = {"source_manifest_hash": parent["manifest_hash"], "case": case, "requests": requests,
                "maximum_attempts": 4, "scope": "API/schema repair and targeted swap check; not a complete teacher quality pilot",
                "prior_exposure": "Validation case previously inspected; frozen score-blind assistant label reused",
                "gates": {"accepted_per_model": 2, "symmetric_per_model": True, "blind_agreement_per_model": True}}
    manifest["manifest_hash"] = digest(manifest)
    _seal(target / "manifest.json", manifest)
    with (target / ".run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / "report.json").exists():
            result = _verified(target / "report.json", "report_hash")
            if result["manifest_hash"] != manifest["manifest_hash"] or not 1 <= len(result["attempts"]) <= 4:
                raise ValueError("wire recovery report mismatch")
            for row, request in zip(result["attempts"], requests[:len(result["attempts"])], strict=True):
                if any(row[k] != request[k] for k in ("model", "orientation", "request_hash")) or row["raw_receipt"] != _attempt_receipt(directory, row["request_hash"]):
                    raise ValueError("wire recovery cached receipt changed")
            return result
        if (target / "started.json").exists():
            raise ValueError("interrupted wire recovery requires operator review")
        before = _read(cache / "usage.json")
        spent = before.get("pair", 0) - parent["diagnostic_pair_baseline"]
        if spent < 0 or spent + 4 > parent["diagnostic_allowance"] or before.get("pair", 0) + 4 > LIMITS["pair"]:
            raise ValueError("wire recovery exceeds diagnostic budget")
        if any((cache / f'{r["request_hash"]}.json').exists() or list(cache.glob(f'{r["request_hash"]}.failure-*.json')) for r in requests):
            raise ValueError("unpublished wire recovery requires operator review")
        old = {p.name: digest(p.read_bytes().hex()) for p in cache.glob("*.json") if p.name != "usage.json"}
        _seal(target / "started.json", {"manifest_hash": manifest["manifest_hash"], "ledger_before": before})
        attempts = []
        for request, (teacher, args) in zip(requests, jobs, strict=True):
            answer, error = None, None
            try:
                answer = teacher.judge(*args)
            except Exception as exc:  # noqa: BLE001 - retain only type and safe transport receipt.
                error = type(exc).__name__
            attempts.append({k: request[k] for k in ("model", "orientation", "request_hash")} | {
                "answer": answer, "error_type": error, "raw_receipt": _attempt_receipt(directory, request["request_hash"])})
            if answer is None:
                break
        models = {}
        for model in COMPARATIVE_MODELS:
            rows = [r for r in attempts if r["model"] == model]
            labels = [r["answer"]["label"] if r["answer"] else None for r in rows]
            complete = len(labels) == 2 and None not in labels
            models[model] = {"attempted": len(rows), "accepted": sum(r["answer"] is not None for r in rows),
                             "labels": labels, "symmetric": labels[1] == REVERSE[labels[0]] if complete else None,
                             "agrees_with_blind": labels == [case["blind_label"], REVERSE[case["blind_label"]]] if complete else None}
        after = _read(cache / "usage.json")
        preserved = all((cache / name).exists() and digest((cache / name).read_bytes().hex()) == value for name, value in old.items())
        budget_ok = after.get("pair", 0) - before.get("pair", 0) == len(attempts) and all(after.get(k, 0) == before.get(k, 0) for k in ("generation", "grade"))
        result = {"manifest_hash": manifest["manifest_hash"], "attempts": attempts, "models": models,
                  "state": "needs_fact_review" if preserved and budget_ok and len(attempts) == 4 and all(r["answer"] for r in attempts) else "stopped",
                  "ledger_before": before, "ledger_after": after, "budget_preserved": budget_ok,
                  "historical_cache_bytes_preserved": preserved, "factual_prose_review": "pending",
                  "training_started": False, "teacher_promoted": False, "activation_allowed": False,
                  "quality_gate_passed": False}
        result["report_hash"] = digest(result)
        _seal(target / "report.json", result)
        return result


def main() -> None:
    """Run or replay one explicitly scoped HTTP compatibility diagnosis."""
    parser = argparse.ArgumentParser(description="Safe one-call teacher HTTP diagnosis")
    parser.add_argument("--directory", type=Path, default=Path("tests/artifacts/reranker"))
    parser.add_argument("--recover", action="store_true", help="Validate the portable schema once, up to four calls")
    args = parser.parse_args()
    if args.recover:
        result = run_recovery(args.directory)
        print(json.dumps({key: result[key] for key in ("state", "models", "ledger_after")}), flush=True)
        return
    result = run_probe(args.directory)
    receipt = result["raw_receipt"]
    safe_errors = [{k: r[k] for k in ("error_type", "http_status", "http_error_classification") if k in r}
                   for r in receipt.get("failures", [])]
    print(json.dumps({"accepted": result["answer"] is not None, "errors": safe_errors,
                      "ledger_after": result["ledger_after"]}), flush=True)


if __name__ == "__main__":
    main()
