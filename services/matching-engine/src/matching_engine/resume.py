"""Typed, Cyrillic PDF resumes with separate owner and public projections."""

from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from matching_engine.models import CandidateProfile, Category
from matching_engine.privacy import sanitize_public_text

_FONT_LOCK = Lock()
_STATUS = {"confirmed": "подтверждено", "partial": "частично подтверждено",
           "self_reported": "самооценка", "pending": "ожидает проверки", "rejected": "отклонено"}
_GRADE_STATUS = {"approved": "подтверждено тестированием",
                 "self_reported": "заявлено самостоятельно (самооценка; тестирование ещё не пройдено)"}


def render_resume(profile: CandidateProfile, category: Category, public: bool = True) -> bytes:
    """Generate a paginated PDF; public resumes hide identity and contacts.

    Inputs are escaped as plain text, never reportlab markup. Evidence links
    and opaque metadata stay in private storage rather than public artifacts.
    """
    with _FONT_LOCK:
        if "ComitSans" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("ComitSans", str(fonts / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("ComitSansBold", str(fonts / "DejaVuSans-Bold.ttf")))
    body = ParagraphStyle("ComitBody", fontName="ComitSans", fontSize=10, leading=14, spaceAfter=4,
                          splitLongWords=True)
    heading = ParagraphStyle("ComitHeading", parent=body, fontName="ComitSansBold", fontSize=13,
                             leading=18, spaceBefore=10, spaceAfter=6, keepWithNext=True)
    title = ParagraphStyle("ComitTitle", parent=heading, fontSize=24, leading=30, spaceBefore=0)
    content: list[Paragraph | Spacer] = []

    def add(text: str, style: ParagraphStyle = body, *, redact: bool = True) -> None:
        clean = sanitize_public_text(text, profile) if public and redact else text
        content.append(Paragraph(escape(clean).replace("\n", "<br/>"), style))

    add("Резюме кандидата" if public else profile.display_name, title)
    add(f"{category.specialization.upper()} / {category.grade.upper()}")
    add(f"Статус грейда: {_GRADE_STATUS[category.grade_status]}")
    add(f"Город: {profile.city}")
    if profile.salary:
        add(f"Ожидания: {profile.salary.from_rub:,} - {profile.salary.to_rub:,} RUB".replace(",", " "), redact=False)
    if public:
        add("Контакты доступны после принятия приглашения.")
    else:
        add(f"Email: {profile.email or '-'}\nТелефон: {profile.phone or '-'}")
    if profile.description:
        add("О себе", heading)
        add(profile.description)
    for label, skills in (("Домены", profile.domains), ("Стек", profile.stacks),
                          ("Soft skills", profile.soft_skills)):
        if skills:
            add(label, heading)
            for skill in skills:
                add(f"{skill.key}: {skill.level}/7 - {_STATUS[skill.status]}")
    for label, claims in (("Проекты", profile.projects), ("Опыт работы", profile.experience),
                          ("Достижения", profile.achievements)):
        if claims:
            add(label, heading)
            for claim in claims:
                add(f"{claim.title} - {_STATUS[claim.status]}", heading)
                if claim.description:
                    add(claim.description)
                if claim.facts is not None:
                    for fact_label, refs in (("Домены проекта", claim.facts.domains),
                                             ("Стек проекта", claim.facts.stacks)):
                        for ref in refs:
                            add(f"{fact_label}: {ref.key} - {_STATUS[ref.status]}")
                    for name, fact_label in (("duration_months", "Длительность, месяцев"),
                                             ("role", "Роль"), ("production_support", "Эксплуатация")):
                        fact = getattr(claim.facts, name)
                        if fact is not None:
                            value = ("да" if fact.value else "нет") if isinstance(fact.value, bool) else str(fact.value)
                            add(f"{fact_label}: {value} - {_STATUS[fact.status]}")
                for key in ("summary", "my_role", "role", "position", "stack", "duration"):
                    value = claim.details.get(key)
                    if isinstance(value, (str, list)):
                        labels = {"summary": "Описание", "my_role": "Моя роль", "role": "Роль",
                                  "position": "Должность", "stack": "Стек", "duration": "Период"}
                        formatted = ", ".join(str(item) for item in value) if isinstance(value, list) else value
                        add(f"{labels[key]}: {formatted}")
    if profile.preferences:
        add("Предпочтения", heading)
        for value in profile.preferences:
            add(value)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(210 * mm, 297 * mm),
                           rightMargin=20 * mm, leftMargin=20 * mm,
                           topMargin=20 * mm, bottomMargin=20 * mm,
                           title="Comit - Candidate resume", author="Comit")

    def footer(canvas, document) -> None:
        canvas.setFont("ComitSans", 8)
        canvas.setFillColor(colors.HexColor("#536477"))
        canvas.drawString(20 * mm, 12 * mm, "COMIT / Профиль и статусы подтверждения")
        canvas.drawRightString(190 * mm, 12 * mm, str(document.page))

    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
