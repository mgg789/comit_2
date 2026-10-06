"""HTTP diagnostics retain fixed classifications, never reflected body or credentials."""

import io
import json
from urllib.error import HTTPError

import pytest

from matching_engine.rerank_hash import digest
from matching_engine.rerank_providers import (
    HTTP_ERROR_BODY_LIMIT,
    HTTP_SCHEMA_KEYWORDS,
    SystemOneProviders,
    classify_http_error,
)


class _Body(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def _error(status, body=b"", *, stream=None):
    return HTTPError("https://reflected.invalid/?key=sk-unit-reflected", status,
                     "sk-unit-error-message", {"Authorization": "sk-unit-header"},
                     _Body(body) if stream is None else stream)


@pytest.mark.parametrize("body", [
    b'{"error":{"message":"xgrammar: uniqueItems is not supported"}}',
    b"Unsupported uniqueItems in JSON schema compiler",
    b"<html><p>XGrammar UNIQUEITEMS unsupported</p></html>",
    b"XGrammar does not support uniqueItems",
    b"uniqueItems is not yet supported",
    b"uniqueItems is not currently supported",
])
def test_unsupported_schema_keyword_is_a_literal_allowlisted_mention(body):
    error = _error(400, body)
    stream = error.fp
    assert classify_http_error(error) == {
        "code": "schema_keyword_unsupported", "schema_keywords": ["uniqueItems"]}
    assert stream.closed and stream.read_sizes == [HTTP_ERROR_BODY_LIMIT]


@pytest.mark.parametrize(("status", "body", "expected"), [
    (400, b"Invalid JSON schema", {"code": "schema_rejected"}),
    (422, b'Invalid schema: "additionalProperties"',
     {"code": "schema_rejected", "schema_keywords": ["additionalProperties"]}),
    (400, b'Unsupported keyword: type',
     {"code": "schema_keyword_unsupported", "schema_keywords": ["type"]}),
    (400, b"Unsupported unrelated model type; not supported", {"code": "http_error"}),
    (400, b"uniqueItemsExtra unsupported", {"code": "http_error"}),
    (400, b"sk-unit-body unknown failure", {"code": "http_error"}),
    (403, b"uniqueItems unsupported; sk-unit-body", {"code": "auth_failed"}),
    (401, b"sk-unit-body", {"code": "auth_failed"}),
    (429, b"sk-unit-body", {"code": "rate_limited"}),
    (501, b'{"error":"uniqueItems unsupported sk-unit-body"}', {"code": "server_error"}),
    (503, b"<html>sk-unit-body unavailable</html>", {"code": "server_error"}),
    (404, b"JSON schema uniqueItems unsupported", {"code": "http_error"}),
])
def test_status_categories_and_unknown_bodies_return_only_fixed_values(status, body, expected):
    error = _error(status, body)
    stream = error.fp
    result = classify_http_error(error)
    assert result == expected and stream.closed
    assert set(result) <= {"code", "schema_keywords"}
    assert all(keyword in HTTP_SCHEMA_KEYWORDS for keyword in result.get("schema_keywords", []))
    encoded = json.dumps(result)
    assert "sk-unit" not in encoded and "reflected.invalid" not in encoded


def test_body_read_is_bounded_and_trailing_diagnostic_text_is_not_inspected():
    body = b"x" * HTTP_ERROR_BODY_LIMIT + b" uniqueItems unsupported sk-unit-body"
    error = _error(400, body)
    stream = error.fp
    assert classify_http_error(error) == {"code": "http_error"}
    assert stream.closed and stream.read_sizes == [32768]


def test_closed_or_unreadable_body_still_closes_and_classifies_status():
    closed = _Body(b"uniqueItems unsupported")
    closed.close()
    assert classify_http_error(_error(400, stream=closed)) == {"code": "http_error"}

    class Unreadable(_Body):
        def read(self, size=-1):
            raise OSError("sk-unit-reflected-read-error")

    unreadable = Unreadable(b"unused")
    assert classify_http_error(_error(403, stream=unreadable)) == {"code": "auth_failed"}
    assert unreadable.closed


def test_absent_or_non_utf8_body_is_safe():
    assert classify_http_error(_error(400, b"\xff\xfeuniqueItems unsupported")) == {
        "code": "schema_keyword_unsupported", "schema_keywords": ["uniqueItems"]}
    error = _error(501)
    error.fp = None
    assert classify_http_error(error) == {"code": "server_error"}


@pytest.mark.parametrize(("status", "body", "classification"), [
    (400, b"xgrammar uniqueItems not supported sk-unit-body", {
        "code": "schema_keyword_unsupported", "schema_keywords": ["uniqueItems"]}),
    (403, b"sk-unit-reflected-body Authorization: Bearer sk-unit-key", {"code": "auth_failed"}),
    (501, b"<html>sk-unit-reflected-body</html>", {"code": "server_error"}),
])
def test_failure_receipts_preserve_identity_and_budget_without_error_details(
        tmp_path, monkeypatch, status, body, classification):
    provider = SystemOneProviders(tmp_path / "cache")
    monkeypatch.setattr(provider, "_api_key", lambda: "sk-unit-key")
    payload = provider._pair_payload({}, {"grade": "junior"}, {"grade": "middle"}, "synthetic")
    identity = provider._identity("/v1/systemone", payload, "pair")
    key = digest(identity)
    error = _error(status, body)
    stream = error.fp

    def fail(_request):
        raise error

    monkeypatch.setattr(provider, "_open", fail)
    with pytest.raises(RuntimeError, match=r"^SystemOne request failed \(HTTPError\)$") as raised:
        provider.request("/v1/systemone", payload, "pair")
    assert raised.value.__cause__ is None and stream.closed
    receipt = json.loads((provider.directory / f"{key}.failure-1.json").read_text())
    assert receipt["request"] == identity and receipt["request_hash"] == key
    assert receipt["http_status"] == status and receipt["error_type"] == "HTTPError"
    assert receipt["http_error_classification"] == classification
    assert set(receipt) == {"request", "request_hash", "http_status", "error_type",
                            "http_error_classification", "elapsed_seconds"}
    assert json.loads((provider.directory / "usage.json").read_text()) == {"pair": 1}
    assert not (provider.directory / f"{key}.json").exists()
    encoded = json.dumps(receipt)
    for forbidden in ("sk-unit", "reflected.invalid", "Authorization", "xgrammar", "<html>"):
        assert forbidden not in encoded
