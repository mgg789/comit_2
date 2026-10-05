"""Conservative contact scrubbing for pre-acceptance resume projections."""

import re

from matching_engine.models import CandidateProfile

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[\w-]+", re.UNICODE)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_LINK = re.compile(r"(?:https?://|www\.)\S+|(?<!\w)@[\w.-]+", re.IGNORECASE)


def sanitize_public_text(text: str, profile: CandidateProfile) -> str:
    """Remove known identity, contact patterns and links before acceptance.

    Free-text redaction cannot detect every obfuscated contact or third-party
    identity; upstream moderation must enforce the same visibility policy.
    """
    for value in (profile.email, profile.phone, profile.display_name):
        if value and value.strip():
            text = re.sub(re.escape(value.strip()), "[скрыто]", text, flags=re.IGNORECASE)
    text = _EMAIL.sub("[контакт скрыт]", text)
    text = _PHONE.sub(
        lambda match: "[контакт скрыт]" if (
            match.group().startswith("+") or len(re.sub(r"\D", "", match.group())) >= 10
        ) else match.group(), text,
    )
    return _LINK.sub("[ссылка скрыта]", text)
