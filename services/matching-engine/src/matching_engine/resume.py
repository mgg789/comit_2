"""Typed, Cyrillic PDF resumes with separate owner and public projections."""

import json
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, HRFlowable, Paragraph, SimpleDocTemplate

from matching_engine.models import CandidateProfile, Category
from matching_engine.privacy import sanitize_public_text

_FONT_LOCK = Lock()
RESUME_TEMPLATE_VERSION = "comit-resume-template:2026-10-v3"
# Current employer brand, adapted to a white print surface (ui-kit/tokens.css).
_ACCENT = colors.HexColor("#bfe2ff")
_INK = colors.HexColor("#272930")
_MUTED = colors.HexColor("#727478")
_STATUS = {"confirmed": "подтверждено", "partial": "частично подтверждено",
           "self_reported": "самооценка", "pending": "ожидает проверки", "rejected": "отклонено"}
_GRADE_STATUS = {"approved": "подтверждено тестированием",
                 "self_reported": "заявлено самостоятельно (самооценка; тестирование ещё не пройдено)"}


def has_current_resume_template(pdf: bytes) -> bool:
    """Recognize our renderer's uncompressed metadata marker without parsing profile text."""
    return f"/Keywords ({RESUME_TEMPLATE_VERSION})".encode("ascii") in pdf


@lru_cache(maxsize=1)
def _skill_catalog() -> dict:
    """Load the packaged display snapshot generated from shared/skill-catalog.ts."""
    return json.loads((Path(__file__).parent / "artifacts" / "resume-skill-catalog.json").read_text())


def render_resume(profile: CandidateProfile, category: Category, public: bool = True) -> bytes:
    """Generate a paginated PDF; public resumes hide identity and contacts.

    Inputs are escaped as plain text, never reportlab markup. Evidence links
    and opaque metadata stay in private storage rather than public artifacts.
    """
    with _FONT_LOCK:
        if "ComitInter" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("ComitInter", str(fonts / "Inter-Regular.ttf")))
            pdfmetrics.registerFont(TTFont("ComitInterBold", str(fonts / "Inter-Bold.ttf")))
            pdfmetrics.registerFontFamily("ComitInter", normal="ComitInter", bold="ComitInterBold")
    body = ParagraphStyle("ComitBody", fontName="ComitInter", fontSize=9.5, leading=15,
                          textColor=_INK, spaceAfter=6, splitLongWords=True)
    heading = ParagraphStyle("ComitHeading", parent=body, fontName="ComitInterBold", fontSize=15,
                             leading=21, textColor=_INK, spaceBefore=8, spaceAfter=10,
                             keepWithNext=True)
    claim_heading = ParagraphStyle("ComitClaim", parent=body, fontName="ComitInterBold",
                                   fontSize=11, leading=16, spaceBefore=8, spaceAfter=4,
                                   keepWithNext=True)
    meta = ParagraphStyle("ComitMeta", parent=body, textColor=_MUTED, fontSize=8.5, leading=13)
    title = ParagraphStyle("ComitTitle", parent=body, fontName="ComitInterBold", fontSize=32,
                           leading=39, spaceBefore=0, spaceAfter=12, keepWithNext=True)
    subtitle = ParagraphStyle("ComitSubtitle", parent=body, fontName="ComitInterBold",
                              fontSize=13, leading=19, textColor=_INK, spaceAfter=12,
                              keepWithNext=True)
    skill_style = ParagraphStyle("ComitSkill", parent=body, leftIndent=9,
                                 borderPadding=(5, 9, 5, 9), backColor=_ACCENT, spaceAfter=11)
    content: list[Flowable] = []

    def add(text: str, style: ParagraphStyle = body, *, redact: bool = True) -> None:
        clean = sanitize_public_text(text, profile) if public and redact else text
        if style is heading:
            rule = HRFlowable(width="100%", thickness=1, color=_ACCENT, spaceBefore=14)
            rule.keepWithNext = True
            content.append(rule)
        content.append(Paragraph(escape(clean).replace("\n", "<br/>"), style))

    add("Резюме кандидата" if public else profile.display_name, title)
    add(f"{category.specialization.upper()} / {category.grade.upper()}", subtitle)
    add(f"Статус грейда: {_GRADE_STATUS[category.grade_status]}", meta)
    add(f"Город: {profile.city}")
    if profile.salary:
        expectation = f"от {profile.salary.from_rub:,}" if profile.salary.to_rub is None else f"{profile.salary.from_rub:,} - {profile.salary.to_rub:,}"
        add(f"Ожидания: {expectation} RUB".replace(",", " "), redact=False)
    if public:
        add("Контакты доступны после принятия приглашения.", meta)
    else:
        add(f"Email: {profile.email or '-'}\nТелефон: {profile.phone or '-'}")
    if profile.description:
        add("О себе", heading)
        add(profile.description)
    catalog = _skill_catalog()
    for group, label, skills in (("domains", "Области знаний", profile.domains),
                                ("stacks", "Технологии", profile.stacks),
                                ("soft_skills", "Гибкие навыки", profile.soft_skills)):
        if skills:
            add(label, heading)
            for skill in skills:
                name = catalog["labels"][group].get(skill.key, skill.key)
                level = catalog["levels"][skill.level - 1] if skill.level else "Уровень не выбран"
                clean_key = sanitize_public_text(name, profile) if public else name
                content.append(Paragraph(
                    f'<b>{escape(clean_key)}</b>&#160;&#160;'
                    f'<b>{level}</b>&#160;&#160;'
                    f'<font color="#272930">- {_STATUS[skill.status]}</font>', skill_style,
                ))
    for label, claims in (("Проекты", profile.projects), ("Опыт работы", profile.experience),
                          ("Достижения", profile.achievements)):
        if claims:
            add(label, heading)
            for claim in claims:
                add(claim.title, claim_heading)
                add(_STATUS[claim.status], meta)
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
    if profile.external_evidence:
        add("Внешние подтверждения", heading)
        for evidence in profile.external_evidence:
            if evidence.kind == "fsp":
                value = evidence.verified_value or {}
                rating = value.get("rating") if isinstance(value, dict) else None
                rank = value.get("rank") if isinstance(value, dict) else None
                label = "Демо-реестр ФСП" if isinstance(value, dict) and value.get("demo_registry") is True else "ФСП"
                add(f"{label}: рейтинг {rating if rating is not None else '-'}, разряд {rank or '-'} - {_STATUS[evidence.status]}")
            elif evidence.kind == "repository":
                value = evidence.verified_value or {}
                url = value.get("url", evidence.subject) if isinstance(value, dict) else evidence.subject
                narrative = value.get("contribution_narrative", "") if isinstance(value, dict) else ""
                safe_url = isinstance(url, str) and any(
                    url.startswith(f"https://{host}/")
                    for host in ("github.com", "gitlab.com", "bitbucket.org", "gitverse.ru", "sourcecraft.dev", "git.sourcecraft.dev")
                )
                add(f"Репозиторий: {url if safe_url else '[ссылка скрыта]'} - {_STATUS[evidence.status]}", redact=False)
                if narrative:
                    add(f"Заявленный вклад: {narrative}")
            else:
                value = evidence.verified_value or {}
                if isinstance(value, dict):
                    add(f"HH резюме: {value.get('title') or '-'} - {_STATUS[evidence.status]}")
                    add("Владелец резюме не подтверждён аккаунтом HH.")
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(210 * mm, 297 * mm),
                           rightMargin=20 * mm, leftMargin=20 * mm,
                           topMargin=38 * mm, bottomMargin=22 * mm,
                           title="Comit - Candidate resume", author="Comit",
                           keywords=RESUME_TEMPLATE_VERSION)

    def footer(canvas, document) -> None:
        """Draw brand furniture outside the splittable text frame on every page."""
        canvas.saveState()
        canvas.drawImage(str(Path(__file__).parent / "fonts" / "brand-capsule.png"),
                         20 * mm, 272 * mm, width=46 * mm, height=13.4 * mm, mask="auto")
        canvas.setFont("ComitInter", 8)
        canvas.setFillColor(_MUTED)
        canvas.drawRightString(190 * mm, 278 * mm, "ПРОФИЛЬ КАНДИДАТА")
        canvas.setStrokeColor(_ACCENT)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, 18 * mm, 190 * mm, 18 * mm)
        canvas.setFont("ComitInter", 8)
        canvas.drawString(20 * mm, 12 * mm, "Профиль и статусы подтверждения")
        canvas.drawRightString(190 * mm, 12 * mm, f"Страница {document.page}")
        canvas.restoreState()

    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
