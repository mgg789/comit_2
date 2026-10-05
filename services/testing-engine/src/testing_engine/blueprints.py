"""Blueprint registry: parameterized task templates resistant to answer leakage.

One blueprint = a family of tasks. A seeded variant instantiates the template
with bounded parameter ranges, so difficulty stays comparable between candidates
while the leaked answer of one variant does not compromise the family (TZ §2).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Blueprints live outside the installed package (data, not code): resolve via
# env override first, then cwd — the container copies them to /app/blueprints.
DEFAULT_BLUEPRINT_DIR = Path(os.environ.get("BLUEPRINT_DIR", Path.cwd() / "blueprints"))


@dataclass(frozen=True)
class Blueprint:
    id: str
    specialization: str
    topic: str
    task_type: str
    difficulty_base: float

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Blueprint":
        required = ("id", "specialization", "topic", "type", "difficulty")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"blueprint is missing required fields: {missing}")

        difficulty = raw["difficulty"]
        if not isinstance(difficulty, dict) or "base" not in difficulty:
            raise ValueError("blueprint difficulty must contain 'base'")

        return cls(
            id=str(raw["id"]),
            specialization=str(raw["specialization"]),
            topic=str(raw["topic"]),
            task_type=str(raw["type"]),
            difficulty_base=float(difficulty["base"]),
        )


def load_blueprints(directory: Path = DEFAULT_BLUEPRINT_DIR) -> list[Blueprint]:
    blueprints: list[Blueprint] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            blueprints.extend(Blueprint.from_dict(item) for item in raw)
        else:
            blueprints.append(Blueprint.from_dict(raw))
    return blueprints
