"""Deterministic public projection shared by publication and backfill."""
from uuid import UUID

from matching_engine.models import CandidateProfile
from matching_engine.privacy import sanitize_public_text
from matching_engine.showcase_models import PublicProfile


def public_profile(profile: CandidateProfile, candidate_id: UUID) -> dict:
    """Publish only contact-redacted text, declared skills and typed narrative facts.

    Names, contacts, metadata, arbitrary details and provenance are never copied.
    Candidate identifiers are used solely for a stable anonymous display label.
    """
    if profile.salary is None:
        raise ValueError("published profile requires salary")
    clean = lambda value: sanitize_public_text(value, profile)
    result = {
        "projection_version": 1,
        "display_name": f"Candidate {candidate_id}",
        "city": clean(profile.city), "salary": profile.salary.model_dump(),
        "description": clean(profile.description),
        "work_types": profile.work_types, "relocation_ready": profile.relocation_ready,
        "relocation_cities": [clean(value) for value in profile.relocation_cities],
        "preferences": [clean(value) for value in profile.preferences],
    }
    for group in ("domains", "stacks", "soft_skills"):
        result[group] = [skill.model_dump(include={"key", "level", "status"}) for skill in getattr(profile, group)]
    for group in ("projects", "experience", "achievements"):
        claims = []
        for claim in getattr(profile, group):
            facts = None
            if claim.facts is not None:
                facts = {}
                for key in ("domains", "stacks"):
                    facts[key] = [ref.model_dump(include={"key", "status"}) for ref in getattr(claim.facts, key)]
                for key in ("duration_months", "role", "production_support"):
                    value = getattr(claim.facts, key)
                    facts[key] = value.model_dump(include={"value", "status"}) if value else None
            claims.append({"title": clean(claim.title), "description": clean(claim.description),
                           "status": claim.status, "facts": facts})
        result[group] = claims
    return PublicProfile.model_validate(result).model_dump(mode="json")
