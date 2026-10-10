"""Shared-coordinate target levels and explicit requirement presence for vacancies."""

from matching_engine.models import StrictSchema
from matching_engine.vacancy_models import VacancyInput


def build_strict_features(vacancy: VacancyInput, schema: StrictSchema) -> tuple[list[float], list[bool]]:
    """Normalize supplied targets by seven; a supplied zero is still an explicit requirement."""
    values = {(group, key): level for group, skills in (
        ("domain", vacancy.domens), ("stack", vacancy.stacks), ("soft_skill", vacancy.soft_skills)
    ) for key, level in skills.items()}
    known = {(coordinate.group, coordinate.key) for coordinate in schema.coordinates}
    unknown = values.keys() - known
    if unknown:
        group, key = min(unknown)
        raise ValueError(f"unknown coordinate: {group}/{key}")
    return (
        [values.get((coordinate.group, coordinate.key), 0) / 7 for coordinate in schema.coordinates],
        [(coordinate.group, coordinate.key) in values for coordinate in schema.coordinates],
    )


def semantic_text(vacancy: VacancyInput) -> str | None:
    """Only actual vacancy prose feeds Harrier; empty prose explicitly disables semantics."""
    bio = vacancy.bio.strip()
    return "Описание вакансии: " + bio if bio else None
