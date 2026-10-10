"""Contact-free coordinate evidence for explainable strict vector consumers."""

from matching_engine.models import CandidateProfile, CoordinateEvidence, StrictSchema


def build_strict_evidence(profile: CandidateProfile, schema: StrictSchema) -> list[dict]:
    """Retain the difference between absent, rejected and declared strict dimensions."""
    claims = {
        (group, skill.key): skill
        for group, skills in (
            ("domain", profile.domains), ("stack", profile.stacks),
            ("soft_skill", profile.soft_skills),
        ) for skill in skills
    }
    evidence = []
    for coordinate in schema.coordinates:
        skill = claims.get((coordinate.group, coordinate.key))
        evidence.append(CoordinateEvidence(
            index=coordinate.index, group=coordinate.group, key=coordinate.key,
            status=skill.status if skill else None, level=skill.level if skill else 0,
        ).model_dump())
    return evidence
