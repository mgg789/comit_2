"""Fail closed on absent/invalid coverage or package-specific line/branch regressions."""
import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def percentages(report: dict, kind: str) -> tuple[float, float]:
    """Read actual tool counters, rejecting empty reports instead of treating them as 100%."""
    if kind not in {"c8", "python"}:
        raise ValueError("unsupported coverage report kind")
    if kind == "c8":
        totals = report["total"]
        counters = [(totals[key]["covered"], totals[key]["total"]) for key in ("lines", "branches")]
    else:
        totals = report["totals"]
        if not report["meta"]["branch_coverage"]:
            raise ValueError("branch coverage was not measured")
        counters = [(totals["covered_lines"], totals["num_statements"]),
                    (totals["covered_branches"], totals["num_branches"])]
    for covered, total in counters:
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            raise ValueError("missing or empty coverage denominator")
        if isinstance(covered, bool) or not isinstance(covered, int) or not 0 <= covered <= total:
            raise ValueError("invalid covered counter")
    return tuple(100 * covered / total for covered, total in counters)


def check(package: str, report_path: Path, config_path: Path) -> tuple[float, float]:
    """Enforce both unrounded counters against the checked-in package floors."""
    config = json.loads(config_path.read_text())[package]
    values = percentages(json.loads(report_path.read_text()), config["kind"])
    for metric, actual in zip(("lines", "branches"), values):
        minimum = config[metric]
        if not isinstance(minimum, (int, float)) or not 0 < minimum <= 100:
            raise ValueError(f"{package}: invalid {metric} floor")
        if actual < minimum:
            raise ValueError(f"{package}: {metric} {actual:.2f}% below {minimum}%")
    return values


def main() -> None:
    """CLI entry point; failures return nonzero and never manufacture reports."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    parser.add_argument("report", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("thresholds.json"))
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--allow-skip", action="append", default=[])
    args = parser.parse_args()
    try:
        lines, branches = check(args.package, args.report, args.config)
        if args.junit:
            cases = [case for report in args.junit for case in ET.parse(report).iter("testcase")]
            if not cases:
                raise ValueError("empty test report")
            if any(case.find("failure") is not None or case.find("error") is not None for case in cases):
                raise ValueError("test report contains failures")
            skipped = [case.get("name") for case in cases if case.find("skipped") is not None]
            unexpected = set(skipped) - set(args.allow_skip)
            if unexpected:
                raise ValueError(f"unexpected skipped tests: {sorted(unexpected)}")
            print(f"Explicit contour exclusions: {skipped}")
    except (OSError, KeyError, TypeError, ValueError, ET.ParseError) as error:
        parser.exit(1, f"Coverage gate failed: {error}\n")
    print(f"{args.package}: lines {lines:.2f}%, branches {branches:.2f}% — gate passed")


if __name__ == "__main__":
    main()
