"""Actual local PDF extraction and strict private import boundaries."""
import base64
import shutil
from io import BytesIO
from uuid import uuid4

import pytest
from agents_service.main import create_app
from agents_service.resume_import import ResumeUpload, extract_resume, parse_fields
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def pdf_bytes(pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 30 700 Td (Name: Anna Petrova) Tj 0 -30 Td (City: Moscow) Tj 0 -30 Td (Senior frontend developer) Tj 0 -30 Td (Work experience) Tj 0 -30 Td (Acme - frontend developer) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-only")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def upload(content, filename="resume.pdf"):
    return ResumeUpload(filename=filename, content_base64=base64.b64encode(content).decode(), processing_consent=True)


def test_actual_pdf_child_extraction():
    result = extract_resume(upload(pdf_bytes()))
    assert result.method == "pdf_text"
    assert result.fields.display_name == "Anna Petrova"
    assert result.fields.city == "Moscow"
    assert result.fields.primary_direction == "frontend"
    assert result.fields.declared_grade == "senior"
    assert result.fields.experience[0]["title"] == "Acme - frontend developer"
    assert "evidence" not in result.fields.model_dump()


@pytest.mark.parametrize(("payload", "code"), [(pdf_bytes(pages=11), "resume_page_limit"), (pdf_bytes(encrypted=True), "resume_pdf_encrypted"), (b"invalid", "resume_pdf_invalid")])
def test_reject_invalid_pdf(payload, code):
    with pytest.raises(ValueError, match=code):
        extract_resume(upload(payload))


def test_mapping_is_self_declared_and_preserves_unknown_text():
    text = "Имя: Анна Иванова\nГород: Казань\nФСП: admin\nПодтверждённый грейд senior\nНавыки\nPython 7/7\nОпыт работы\nКомпания: тест\nРазработка сервиса"
    result = parse_fields(text, "ocr")
    assert result["fields"]["city"] == "Казань"
    assert "fsp_id" not in result["fields"] and "stacks" not in result["fields"]
    assert result["extracted_text"] == text
    assert "ошибки" in result["warnings"][-1]


def test_internal_auth_consent_and_no_owner_override():
    client = TestClient(create_app(token="test-only-resume-token"))
    body = upload(pdf_bytes()).model_dump()
    url = "/internal/candidate-resume/parse"
    assert client.post(url, json=body).status_code == 401
    headers = {"x-internal-token": "test-only-resume-token", "x-owner-id": str(uuid4())}
    assert client.post(url, headers=headers, json={**body, "processing_consent": False}).status_code == 422
    assert client.post(url, headers=headers, json={**body, "candidate_id": str(uuid4())}).status_code == 422
    assert client.post(url, headers=headers, json=body).json()["fields"]["city"] == "Moscow"


@pytest.mark.parametrize(("image_format", "filename"), [("PNG", "resume.png"), ("JPEG", "resume.jpg")])
def test_actual_ocr_when_runtime_installed(image_format, filename):
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract not installed on host; covered in agents-service image acceptance")
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1600, 450), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 48)
    draw.text((30, 40), "Name: Anna Petrova\nCity: Moscow\nSenior frontend developer", fill="black", font=font)
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    result = extract_resume(upload(buffer.getvalue(), filename))
    assert result.method == "ocr" and "Petrova" in result.extracted_text
