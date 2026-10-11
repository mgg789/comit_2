"""Ephemeral local resume extraction, isolated from the API process and external providers."""

import base64
import binascii
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import Field

from agents_service.models import Contract

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 100000
MAX_PAGES = 10
SLOTS = BoundedSemaphore(2)


class ResumeUpload(Contract):
    """Bounded inline bytes; no client filesystem path or external URL is accepted."""

    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=4, max_length=6990508)
    processing_consent: Literal[True]


class ResumeFields(Contract):
    """Only editable declarations; evidence, FSP identity and contact permissions are excluded."""

    display_name: str | None = None
    phone: str | None = None
    city: str | None = None
    description: str
    primary_direction: str | None = None
    declared_grade: str | None = None
    work_types: list[str] | None = None
    experience: list[dict[str, str]] = Field(default_factory=list)
    projects: list[dict[str, str]] = Field(default_factory=list)
    achievements: list[dict[str, str]] = Field(default_factory=list)


class ResumeResult(Contract):
    """Suggestions require review; raw text remains visible so extraction omissions are recoverable."""

    fields: ResumeFields
    extracted_text: str
    method: Literal["pdf_text", "ocr", "mixed"]
    warnings: list[str]


def parse_fields(text: str, method: str) -> dict:
    """Conservatively map explicit labels and sections without inventing skills or confirmed grades."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fields: dict = {"description": text[:10000]}
    labelled = {"display_name": r"^(?:фио|имя|name)\s*[:：]\s*(.+)$",
                "city": r"^(?:город|место проживания|city|location)\s*[:：]\s*(.+)$"}
    for key, pattern in labelled.items():
        for line in lines:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                fields[key] = match[1][:160]
                break
    if "display_name" not in fields and lines and re.fullmatch(r"[А-ЯЁA-Z][а-яёa-z-]+(?: [А-ЯЁA-Z][а-яёa-z-]+){1,2}", lines[0]):
        fields["display_name"] = lines[0][:160]
    phone = re.search(r"(?<!\d)(?:\+7|8)[ (.-]*\d{3}[ ).-]*\d{3}[ .-]*\d{2}[ .-]*\d{2}(?!\d)", text)
    if phone:
        fields["phone"] = phone[0][:64]
    header = " ".join(lines[:8]).lower()
    directions = {"fullstack": r"full[ -]?stack|фул[л]?ст[еэ]к", "frontend": r"front[ -]?end|фронтенд",
                  "backend": r"back[ -]?end|бэкенд|бекенд", "devops": r"devops|sre",
                  "mobile": r"android|ios|mobile|мобильн", "ml": r"machine learning|ml engineer|машинн",
                  "data": r"data (?:analyst|engineer|scientist)|аналитик данных", "qa": r"qa|тестировщик"}
    for value, pattern in directions.items():
        if re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", header):
            fields["primary_direction"] = value
            break
    for value, pattern in {"senior": "senior", "middle": "middle|mid-level", "junior": "junior", "intern": "intern|стаж[её]р"}.items():
        if re.search(r"\b(?:" + pattern + r")\b", header):
            fields["declared_grade"] = value
            break
    headings = {"experience": r"опыт(?: работы)?|work experience|employment",
                "projects": r"проекты|projects", "achievements": r"достижения|achievements"}
    active = None
    chunks: dict[str, list[str]] = {}
    for line in lines:
        heading = line.rstrip(":").lower()
        group = next((key for key, pattern in headings.items() if re.fullmatch(pattern, heading)), None)
        if group:
            active = group
            chunks.setdefault(group, [])
        elif re.fullmatch(r"образование|education|навыки|skills|контакты|contacts|о себе|about", heading):
            active = None
        elif active:
            chunks[active].append(line)
    for key, section in chunks.items():
        if section:
            fields[key] = [{"title": section[0][:300], "description": "\n".join(section)[:10000]}]
    warnings = ["Проверьте распознанные данные. Уровни навыков нужно указать самостоятельно."]
    if len(text) > 10000:
        warnings.append("В поле «О себе» помещены первые 10 000 символов; полный текст доступен ниже.")
    if method != "pdf_text":
        warnings.append("Текст распознан с изображения: возможны ошибки в именах, датах и цифрах.")
    return {"fields": fields, "extracted_text": text, "method": method, "warnings": warnings}


def _ocr(path: Path) -> str:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 20000000
    with Image.open(path) as source:
        if source.width * source.height > 20000000 or source.n_frames != 1:
            raise ValueError("resume_image_too_large")
        source.load()
        source.convert("RGB").save(path.with_suffix(".png"))
    if not shutil.which("tesseract"):
        raise ValueError("resume_ocr_unavailable")
    result = subprocess.run(["tesseract", str(path.with_suffix(".png")), "stdout", "-l", "rus+eng"],
                            capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise ValueError("resume_ocr_unavailable")
    return result.stdout.decode("utf-8", errors="replace")


def _extract(path: Path) -> tuple[str, str]:
    from pypdf import PdfReader
    if path.suffix != ".pdf":
        return _ocr(path), "ocr"
    if not path.read_bytes().startswith(b"%PDF-"):
        raise ValueError("resume_pdf_invalid")
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError("resume_pdf_encrypted")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("resume_page_limit")
    parts = []
    ocr_count = 0
    for number, page in enumerate(reader.pages, start=1):
        stream = page.get_contents()
        if stream is not None and len(stream.get_data()) > 10 * MAX_BYTES:
            raise ValueError("resume_pdf_too_complex")
        text = page.extract_text() or ""
        if len(text.strip()) < 30:
            if not shutil.which("pdftoppm"):
                raise ValueError("resume_ocr_unavailable")
            target = path.parent / f"page-{number}"
            subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-scale-to", "2400",
                            "-singlefile", "-png", str(path), str(target)], capture_output=True, timeout=10, check=True)
            text = _ocr(target.with_suffix(".png"))
            ocr_count += 1
        parts.append(text)
        if sum(map(len, parts)) > MAX_TEXT:
            raise ValueError("resume_text_limit")
    return "\n".join(parts), "pdf_text" if not ocr_count else "ocr" if ocr_count == len(parts) else "mixed"


def extract_resume(request: ResumeUpload) -> ResumeResult:
    """Run untrusted parsers with a 45-second wall limit and OS resource limits; erase temporary bytes."""
    suffix = Path(request.filename).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("resume_type_unsupported")
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("resume_encoding_invalid") from error
    if not content or len(content) > MAX_BYTES:
        raise ValueError("resume_size_invalid")
    with tempfile.TemporaryDirectory(prefix="comit-resume-") as directory:
        path = Path(directory) / ("resume" + suffix)
        path.write_bytes(content)
        try:
            process = subprocess.Popen([sys.executable, "-m", "agents_service.resume_import", str(path)],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                                       env={key: value for key, value in os.environ.items() if key in {"PATH", "PYTHONPATH", "LANG", "SYSTEMROOT"}})
            output, _ = process.communicate(timeout=45)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError("resume_timeout") from error
        if process.returncode or len(output) > MAX_TEXT * 12:
            raise ValueError("resume_parse_failed")
        payload = json.loads(output)
        if "error" in payload:
            raise ValueError(payload["error"])
        return ResumeResult.model_validate(payload)


def register_resume_routes(app: FastAPI, owner_dependency) -> None:
    """Expose extraction only behind the service token plus API-derived owner identity."""
    Owner = Annotated[UUID, Depends(owner_dependency)]

    @app.post("/internal/candidate-resume/parse", response_model=ResumeResult, response_model_exclude_none=True)
    def parse(request: ResumeUpload, _owner: Owner):
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(503, "resume_parser_busy")
        try:
            return extract_resume(request)
        except ValueError as error:
            code = str(error)
            if not re.fullmatch(r"resume_[a-z_]+", code):
                code = "resume_parse_failed"
            raise HTTPException(503 if code == "resume_ocr_unavailable" else 422, code) from None
        finally:
            SLOTS.release()


if __name__ == "__main__":
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        if sys.platform == "linux":
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        text, method = _extract(Path(sys.argv[1]))
        text = text.replace("\x00", "").strip()
        if len(text) > MAX_TEXT:
            raise ValueError("resume_text_limit")
        if len(text) < 20:
            raise ValueError("resume_text_not_found")
        print(json.dumps(parse_fields(text, method), ensure_ascii=False))
    except (ValueError, OSError, RuntimeError, MemoryError, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"resume_[a-z_]+", str(error)) else "resume_parse_failed"
        print(json.dumps({"error": code}))
