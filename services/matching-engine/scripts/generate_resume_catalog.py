"""Generate/check the PDF display snapshot from the canonical shared TypeScript catalog."""

import argparse
import json
import re
from pathlib import Path


def main() -> None:
    """Write the packaged JSON, or fail --check when canonical labels have drifted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    source_path = "packages/shared/src/skill-catalog.ts"
    source = (root / source_path).read_text(encoding="utf-8")
    labels = {}
    for group, section in (("domains", "domainChoices"), ("stacks", "stackChoices"),
                           ("soft_skills", "softChoices")):
        body = re.search(r"export const " + section + r" = \[(.*?)\] as const;", source, re.DOTALL)
        if body is None:
            raise ValueError(f"canonical catalog section not found: {section}")
        labels[group] = dict(re.findall(r'\["([^"]+)", "([^"]+)"\]', body[1]))
        if not labels[group]:
            raise ValueError(f"canonical catalog section empty: {section}")
    level_section = re.search(r"export const SKILL_LEVELS = \[(.*?)\] as const;", source)
    if level_section is None:
        raise ValueError("canonical skill levels not found")
    levels = re.findall(r'"([^"]+)"', level_section[1])
    if len(levels) != 7:
        raise ValueError("canonical catalog must contain seven ordinal skill levels")
    serialized = json.dumps({"source": source_path, "labels": labels, "levels": levels},
                            ensure_ascii=False, indent=2) + "\n"
    destination = root / "services/matching-engine/src/matching_engine/artifacts/resume-skill-catalog.json"
    if args.check:
        if destination.read_text(encoding="utf-8") != serialized:
            raise SystemExit("PDF display catalog is stale; regenerate with this script")
    else:
        destination.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
