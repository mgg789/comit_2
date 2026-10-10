from uuid import UUID

from matching_engine.models import CandidateProfile, Category, ExternalEvidence
from matching_engine.resume import render_resume
from matching_engine.showcase_projection import public_profile


def profile() -> CandidateProfile:
    return CandidateProfile(
        source_revision=2,
        display_name="Private Candidate",
        email="private@example.test",
        email_verified=True,
        city="Москва",
        salary={"from_rub": 100, "to_rub": 200},
        primary_direction="backend",
        grade_result={"grade": "middle", "status": "self_reported"},
        external_evidence=[
            ExternalEvidence(kind="fsp", subject="fsp-001", status="confirmed", verified_value={"rating": 1876, "rank": "I"}),
            ExternalEvidence(kind="repository", subject="https://github.com/acme/project", status="confirmed",
                             verified_value={"url": "https://github.com/acme/project", "repository_nonempty": True,
                                             "contribution_narrative": "Implemented the adapter.", "authorship_proven": True}),
        ],
    )


def test_public_projection_keeps_facts_and_forces_no_authorship_proof() -> None:
    projected = public_profile(profile(), UUID(int=1))
    repo = next(item for item in projected["external_evidence"] if item["kind"] == "repository")
    assert repo["verified_value"]["repository_nonempty"] is True
    assert repo["verified_value"]["authorship_proven"] is False
    assert "email" not in str(projected)


def test_resume_contains_external_evidence_and_no_private_contact_in_public_pdf() -> None:
    pdf = render_resume(profile(), Category(specialization="backend", grade="middle", grade_status="self_reported"))
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(__import__("io").BytesIO(pdf)).pages)
    assert "private@example.test" not in text
    assert "github.com" in text
