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
    public_evidence = []
    for evidence in profile.external_evidence:
        if evidence.kind == "weekly_challenge":
            continue  # Internal reward is not an externally verified public achievement.
        if evidence.status not in ("confirmed", "partial", "pending"):
            continue
        value = evidence.verified_value
        if isinstance(value, dict):
            if evidence.kind == "fsp":
                events = value.get("events", [])
                safe_events = []
                if isinstance(events, list):
                    for event in events[:50]:
                        if isinstance(event, dict):
                            safe_events.append({
                                "title": clean(str(event.get("title", "")))[:300],
                                "year": event.get("year"), "place": event.get("place"),
                            })
                value = {"rank": value.get("rank"), "rating": value.get("rating"), "events": safe_events,
                         "demo_registry": value.get("demo_registry") is True,
                         "provider_version": clean(str(value.get("provider_version", "")))[:100],
                         "certificates": [clean(str(item))[:300] for item in value.get("certificates", [])[:50]]
                         if isinstance(value.get("certificates", []), list) else []}
            elif evidence.kind == "repository":
                narrative = value.get("contribution_narrative")
                value = {"url": value.get("url", evidence.subject), "repository_nonempty": value.get("repository_nonempty") is True,
                         "contribution_narrative": clean(narrative) if isinstance(narrative, str) else "",
                         "authorship_proven": False}
            else:
                experience = value.get("experience", [])
                safe_experience = []
                if isinstance(experience, list):
                    for item in experience[:20]:
                        if isinstance(item, dict):
                            safe_experience.append({key: clean(str(item[key]))[:300] for key in ("title", "company") if isinstance(item.get(key), str)})
                value = {"resume_id": "hidden", "title": clean(str(value.get("title", "")))[:300],
                         "area": clean(str(value.get("area", "")))[:300], "experience": safe_experience,
                         "ownership_proven": False}
            if evidence.kind == "repository":
                # A repository being public and non-empty never proves authorship.
                value["authorship_proven"] = False
        public_evidence.append({"kind": evidence.kind, "subject": clean(evidence.subject),
                                "status": evidence.status, "verified_value": value})
    result["external_evidence"] = public_evidence
    return PublicProfile.model_validate(result).model_dump(mode="json")
