"""Append-only strict coordinates and evidence-aware ordinal normalization."""

import hashlib
import json

from matching_engine.models import CandidateProfile, Coordinate, StrictSchema

INITIAL_GROUPS = {
    "domain": (
        "algorithms", "sql", "doc_databases", "rest_api", "cybersecurity",
        "data_analytics_stats", "classical_ml", "neural_nets", "nlp_and_arbitrary_data",
        "ai_agents", "math", "ux_userflow", "architecture_thinking", "devops",
        "lowlevel_optimization",
    ),
    "stack": (
        "python", "node", "go", "php", "react", "vue", "angular", "html_css_jquery",
        "tailwind", "electron", "figma", "postgres", "mongo", "sqlite", "mysql", "docker",
    ),
    "soft_skill": (
        "teamwork", "representation", "time_management", "project_management",
        "leadership", "self_organization",
    ),
}


def schema_hash(coordinates: list[Coordinate]) -> str:
    """Hash ordering, group, key and level/7 rule for compatibility checks."""
    payload = {
        "normalization": "ordinal-level-divided-by-seven:v1",
        "evidence": "all-declared-except-rejected:v1",
        "coordinates": [coordinate.model_dump() for coordinate in coordinates],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def initial_schema() -> StrictSchema:
    """Return the canonical context/35 registry: 15 domains, 16 stacks, 6 soft skills."""
    coordinates = [
        Coordinate(index=index, group=group, key=key)
        for index, (group, key) in enumerate(
            (group, key) for group, keys in INITIAL_GROUPS.items() for key in keys
        )
    ]
    return StrictSchema(version=1, schema_hash=schema_hash(coordinates), coordinates=coordinates)


def validate_coordinates(profile: CandidateProfile, schema: StrictSchema) -> None:
    """Reject unknown upstream keys; a new parameter needs a registry migration first."""
    known = {(coordinate.group, coordinate.key) for coordinate in schema.coordinates}
    for group, skills in (
        ("domain", profile.domains), ("stack", profile.stacks), ("soft_skill", profile.soft_skills)
    ):
        for skill in skills:
            if (group, skill.key) not in known:
                raise ValueError(f"unknown coordinate: {group}/{skill.key}")


def build_strict_vector(profile: CandidateProfile, schema: StrictSchema) -> list[float]:
    """Map all declared levels to [0,1]; rejected and absent claims stay zero."""
    validate_coordinates(profile, schema)
    values = {
        (group, skill.key): skill.level / 7
        for group, skills in (
            ("domain", profile.domains), ("stack", profile.stacks),
            ("soft_skill", profile.soft_skills),
        )
        for skill in skills
        if skill.status != "rejected"
    }
    return [values.get((coordinate.group, coordinate.key), 0.0) for coordinate in schema.coordinates]
