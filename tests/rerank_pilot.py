"""Bounded real-provider pilot; writes only synthetic experiment artifacts."""

import itertools
import json
import time
from pathlib import Path

from matching_engine.rerank_providers import LocalProviders, digest


def main() -> None:
    """Generate five profiles and audit twenty directed comparisons before expansion."""
    root = Path(__file__).parent / "artifacts/reranker"
    root.mkdir(parents=True, exist_ok=True)
    providers = LocalProviders(root / "provider-cache")
    report = {"state": "running", "luna_calls": 0,
              "luna_unavailable_reason": "collaboration agent thread limit reached",
              "limits": {"families": 60, "profiles": 600, "pair_attempts": 3000, "grade_attempts": 600}}
    start = time.monotonic()
    try:
        profiles = providers.generate("backend", "Exact stack versus grade; confirmed versus declared strength")
        report["generation_seconds"] = time.monotonic() - start
        (root / "pilot-profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["generation_seconds"] > 90:
            raise RuntimeError("generation pilot exceeded 90 seconds")
        target = {"class": "backend", "stacks": {"python": 5}, "domens": {"sql": 4},
                  "soft_skills": {"teamwork": 3}, "salary": {"min": 50000, "max": 200000}}
        preference = "Prefer precise Python and SQL fit, reliable evidence and relevant production experience; grade alone is not decisive."
        comparisons, consistent, decisive = [], 0, 0
        for a, b in itertools.combinations(range(5), 2):
            before = time.monotonic()
            forward = providers.pair(target, profiles[a], profiles[b], preference)
            backward = providers.pair(target, profiles[b], profiles[a], preference)
            expected = {"A": "B", "B": "A", "tie": "tie", "insufficient": "insufficient"}[forward]
            consistent += backward == expected
            decisive += forward in ("A", "B")
            comparisons.append({"a": a, "b": b, "forward": forward, "backward": backward,
                                "seconds": time.monotonic() - before})
            print(json.dumps({"completed_pair_requests": 2 * len(comparisons),
                              "swap_consistent": consistent}), flush=True)
        report.update(comparisons=comparisons, swap_consistency=consistent / 10, decisive_pairs=decisive)
        if consistent < 8 or decisive < 6:
            raise RuntimeError("teacher pilot failed swap consistency or decisive-label coverage")
        # Keep all 600 grade attempts for the full 600-profile cohort.
        report["grade_contract_runtime_verified"] = False
        report["state"] = "passed"
    except Exception as exc:  # noqa: BLE001 - seal the failed pilot, never advance generation.
        report["state"] = "stopped"
        report["reason"] = str(exc)[:300]
    report["elapsed_seconds"] = time.monotonic() - start
    report["report_hash"] = digest(report)
    (root / "pilot-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
