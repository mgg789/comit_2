"""Resume projections, Cyrillic content, markup escaping and pagination."""

import subprocess
import sys
from io import BytesIO
from pathlib import Path

from matching_engine.models import CandidateProfile, Category
from matching_engine.privacy import sanitize_public_text
from matching_engine.resume import (
    RESUME_TEMPLATE_VERSION,
    has_current_resume_template,
    render_resume,
)
from pypdf import PdfReader


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


def test_public_resume_labels_self_reported_grade() -> None:
    profile = CandidateProfile(source_revision=1)
    pdf = render_resume(profile, Category(
        specialization="backend", grade="middle", grade_status="self_reported",
    ))
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert "Статус грейда:" in text
    assert "заявлено самостоятельно" in text


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


def test_designer_resume_embeds_inter_and_prints_all_sections_with_brand_color() -> None:
    profile = CandidateProfile.model_validate({
        "source_revision": 1, "city": "Казань", "salary": {"from_rub": 150000},
        "stacks": [{"key": "python", "level": 6, "status": "confirmed"}],
        "projects": [{"title": "Проект", "description": "Описание проекта", "status": "partial"}],
        "experience": [{"title": "Разработчик", "status": "confirmed"}],
        "achievements": [{"title": "Доклад", "status": "self_reported"}],
        "preferences": ["Удалённая работа"],
    })
    pdf = PdfReader(BytesIO(render_resume(profile, Category(specialization="backend", grade="middle"))))
    text = "\n".join(page.extract_text() for page in pdf.pages)
    for label in ("Технологии", "Python", "Senior", "Проекты", "Описание проекта", "Опыт работы",
                  "Достижения", "Удалённая работа", "от 150 000 RUB", "Страница 1"):
        assert label in text
    font_names = {
        str(font.get_object()["/BaseFont"])
        for page in pdf.pages for font in page["/Resources"]["/Font"].values()
    }
    assert any("Inter-Regular" in name for name in font_names)
    assert any("Inter-Bold" in name for name in font_names)
    for page in pdf.pages:
        for font_ref in page["/Resources"]["/Font"].values():
            font = font_ref.get_object()
            if "Inter" in str(font["/BaseFont"]):
                assert "/FontFile2" in font["/FontDescriptor"]
        # The printed accent is real vector color, independent of font availability.
        assert b".74902 .886275 1 RG" in page.get_contents().get_data()
        assert "/XObject" in page["/Resources"]


def test_maximum_narrative_and_long_unbroken_values_preserve_the_end() -> None:
    profile = CandidateProfile.model_validate({
        "source_revision": 1, "display_name": "И" * 300, "city": "К" * 300,
        "description": "Описание. " * 1000,
        "projects": [{"title": "П" * 300, "status": "pending", "description": "Ж" * 10000}],
        "preferences": ["КОНЕЦ ПОЛНОГО ПРОФИЛЯ"],
    })
    pdf = PdfReader(BytesIO(render_resume(
        profile, Category(specialization="backend", grade="senior"), public=False,
    )))
    assert len(pdf.pages) > 2
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert text.count("Ж") == 10000
    assert "КОНЕЦ ПОЛНОГО ПРОФИЛЯ" in pdf.pages[-1].extract_text()


def test_template_marker_and_skill_display_snapshot_match_canonical_ui_catalog() -> None:
    subprocess.run([sys.executable, str(Path(__file__).parents[1] / "scripts/generate_resume_catalog.py"),
                    "--check"], check=True)
    profile = CandidateProfile.model_validate({
        "source_revision": 1,
        "stacks": [{"key": "python", "level": 6, "status": "confirmed"},
                   {"key": "legacy_key", "level": 0, "status": "pending"}],
    })
    artifact = render_resume(profile, Category(specialization="backend", grade="middle"))
    assert has_current_resume_template(artifact)
    assert not has_current_resume_template(b"%PDF-1.4\nlegacy")
    pdf = PdfReader(BytesIO(artifact))
    assert pdf.metadata.keywords == RESUME_TEMPLATE_VERSION
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert "Python" in text and "Senior" in text
    assert "legacy_key" in text and "Уровень не выбран" in text
    assert "6/7" not in text
