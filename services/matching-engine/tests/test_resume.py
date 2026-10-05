"""Resume projections, Cyrillic content, markup escaping and pagination."""

from io import BytesIO

from pypdf import PdfReader

from matching_engine.models import CandidateProfile, Category
from matching_engine.privacy import sanitize_public_text
from matching_engine.resume import render_resume


def test_public_resume_hides_contacts_in_every_narrative_and_preserves_statuses() -> None:
    profile = CandidateProfile.model_validate({
        "source_revision": 1, "display_name": "Иван Иванов", "email": "ivan@example.test",
        "phone": "+7 999 123 45 67", "city": "Москва",
        "description": "Иван Иванов пишет Python. ivan@example.test +7 999 123 45 67",
        "salary": {"from_rub": 100000, "to_rub": 200000},
        "domains": [{"key": "sql", "level": 4, "status": "partial"}],
        "soft_skills": [{"key": "teamwork", "level": 5, "status": "self_reported"}],
        "projects": [{"title": "<b>Каталог</b>", "description": "https://private.test/profile", "status": "pending"}],
        "experience": [{"title": "Разработчик", "status": "partial", "details": {"duration": "2024-2026"}}],
    })
    category = Category(specialization="backend", grade="middle")
    public = render_resume(profile, category)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(public)).pages)
    for private in ("Иван Иванов", "ivan@example.test", "+7 999", "private.test"):
        assert private not in text
    for visible in ("Резюме кандидата", "Москва", "частично подтверждено", "самооценка", "<b>Каталог</b>"):
        assert visible in text
    assert "100 000 - 200 000 RUB" in text
    assert "2024-2026" in text
    owner = render_resume(profile, category, public=False)
    owner_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(owner)).pages)
    assert "ivan@example.test" in owner_text and "Иван Иванов" in owner_text


def test_long_cyrillic_resume_paginates_without_losing_tail() -> None:
    profile = CandidateProfile(source_revision=1, description="Тестовый профиль. " * 400,
                               preferences=["ПОСЛЕДНЯЯ СТРОКА"])
    pdf = PdfReader(BytesIO(render_resume(profile, Category(specialization="data", grade="senior"))))
    assert len(pdf.pages) > 1
    assert "ПОСЛЕДНЯЯ СТРОКА" in pdf.pages[-1].extract_text()


def test_contact_patterns_are_scrubbed_without_removing_normal_skill_levels() -> None:
    profile = CandidateProfile(source_revision=1)
    text = sanitize_public_text("SQL 4/7 x@example.test +7 (999) 123-45-67 @telegram www.example.test", profile)
    assert "SQL 4/7" in text
    assert "example.test" not in text and "999" not in text and "@telegram" not in text
