"""Unit contracts for the owner-scoped organization tool registry."""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

from agents_service.database import migrate
from agents_service.models import SchemaSnapshot
from agents_service.organization_tools import OrganizationToolError, ToolRegistry


class Matching:
    """Small schema provider used by local registry tests."""

    def schema(self):
        return SchemaSnapshot(version=1, schema_hash="schema-v1", coordinates=[
            {"index": 0, "group": "domain", "key": "backend"},
        ])


def test_catalog_lists_every_tool_but_schemas_are_progressive():
    """Catalog discovery is cheap and schema discovery is opt-in by name."""
    registry = ToolRegistry("postgresql://unused", matching=Matching())
    catalog = registry.catalog()
    names = {item["name"] for item in catalog}
    assert {"vacancy_list", "vacancy_publish", "documents_create_md", "taxonomy", "candidate_feed"} <= names
    assert len(registry.schemas(["vacancy_publish"])) == 1
    assert registry.schemas([]) == []
    with pytest.raises(OrganizationToolError, match="tool_not_found"):
        registry.schemas(["internal_secret_tool"])


def test_openai_schema_is_strict_and_publish_requires_human_confirmation():
    """Unknown argument keys and an absent explicit confirmation are rejected before effects."""
    registry = ToolRegistry("postgresql://unused", matching=Matching())
    schema = registry.schemas(["vacancy_publish"])[0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    with pytest.raises(OrganizationToolError, match="invalid_tool_arguments"):
        registry.dispatch("00000000-0000-4000-8000-000000000001", "vacancy_publish", {
            "vacancy_id": "00000000-0000-4000-8000-000000000002", "expected_revision": 1,
            "summary_hash": "a" * 64,
        }, "attempt-1")


def test_bridge_dispatch_uses_fixed_url_owner_and_replay_envelope():
    """Bridge calls carry the owner and idempotency envelope, never a model URL."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"cards": []}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = ToolRegistry("postgresql://unused", matching=Matching(), api_url="http://api:8080",
                            internal_token="secret", transport=client)
    result = registry.dispatch("00000000-0000-4000-8000-000000000001", "candidate_feed", {
        "vacancy_id": "00000000-0000-4000-8000-000000000002", "limit": 3,
    }, "request-key", "00000000-0000-4000-8000-000000000003")
    assert result == {"cards": []}
    assert seen[0].url == "http://api:8080/internal/organization-agent/tools/candidate_feed"
    assert seen[0].headers["x-owner-id"] == "00000000-0000-4000-8000-000000000001"
    body = seen[0].content
    assert b"request-key" not in body
    assert str(uuid.uuid5(uuid.NAMESPACE_URL, "comit:organization-bridge:00000000-0000-4000-8000-000000000001:candidate_feed:request-key")).encode() in body
    assert b"00000000-0000-4000-8000-000000000003" in body


def test_bridge_chat_accepts_task_and_lease_proofs_from_envelope():
    """Worker proof fields may arrive in the envelope and are injected before validation."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"sent": True}})

    registry = ToolRegistry("postgresql://unused", matching=Matching(), api_url="http://api:8080",
                            internal_token="secret", transport=httpx.Client(transport=httpx.MockTransport(handler)))
    registry.dispatch("00000000-0000-4000-8000-000000000001", "candidate_chat_send", {
        "process_id": "00000000-0000-4000-8000-000000000002",
        "delegation_id": "00000000-0000-4000-8000-000000000003",
        "text": "hello", "idempotency_key": "chat-key",
    }, "chat-key", "00000000-0000-4000-8000-000000000004",
                     lease_token="00000000-0000-4000-8000-000000000005")
    assert b"00000000-0000-4000-8000-000000000004" in seen[0].content
    assert b"00000000-0000-4000-8000-000000000005" in seen[0].content
    wire = json.loads(seen[0].content)
    assert "after_sequence" not in wire["arguments"]
    assert "limit" not in wire["arguments"]


def test_bridge_interview_accepts_workflow_proofs_in_arguments_and_moves_them_to_envelope():
    """Interview workflow arguments may carry proofs, but bridge args stay allow-listed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"proposed": True}})

    registry = ToolRegistry("postgresql://unused", matching=Matching(), api_url="http://api:8080",
                            internal_token="secret", transport=httpx.Client(transport=httpx.MockTransport(handler)))
    registry.dispatch("00000000-0000-4000-8000-000000000001", "interview_propose", {
        "process_id": "00000000-0000-4000-8000-000000000002",
        "task_id": "00000000-0000-4000-8000-000000000004",
        "delegation_id": "00000000-0000-4000-8000-000000000003",
        "lease_token": "00000000-0000-4000-8000-000000000005",
        "expected_revision": 1, "kind": "general", "starts_at": "2026-10-08T10:00:00Z",
        "time_zone": "UTC", "duration_minutes": 30, "format": "remote", "location": "online",
        "idempotency_key": "interview-key",
    }, "interview-key")
    wire = json.loads(seen[0].content)
    assert wire["task_id"] == "00000000-0000-4000-8000-000000000004"
    assert wire["delegation_id"] == "00000000-0000-4000-8000-000000000003"
    assert wire["lease_token"] == "00000000-0000-4000-8000-000000000005"
    assert set(wire["arguments"]) == {"process_id", "expected_revision", "kind", "starts_at", "time_zone",
                                       "duration_minutes", "format", "location", "idempotency_key"}


def test_knowledge_page_rejects_path_escape(tmp_path):
    """Knowledge reads are package-rooted and do not accept filesystem paths."""
    (tmp_path / "organization.md").write_text("trusted page", encoding="utf-8")
    registry = ToolRegistry("postgresql://unused", knowledge_root=tmp_path)
    result = registry.dispatch("00000000-0000-4000-8000-000000000001", "knowledge_page_read",
                               {"page": "organization"}, "knowledge-1")
    assert result["content"] == "trusted page"
    with pytest.raises(OrganizationToolError, match="invalid_tool_arguments"):
        registry.dispatch("00000000-0000-4000-8000-000000000001", "knowledge_page_read",
                           {"page": "../secrets"}, "knowledge-2")


def test_knowledge_catalog_lists_safe_pages_and_rejects_external_symlink(tmp_path):
    """Discovery is deterministic and never exposes content outside the package root."""
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "z-last.md").write_text("# Last page\n\ncontent", encoding="utf-8")
    (root / "a-first.md").write_text("# First page", encoding="utf-8")
    (root / "Bad slug.md").write_text("# Invalid slug", encoding="utf-8")
    (root / "invalid-utf8.md").write_bytes(b"\xff")
    outside = tmp_path / "outside.md"
    outside.write_text("private fixture", encoding="utf-8")
    (root / "outside.md").symlink_to(outside)
    registry = ToolRegistry("postgresql://unused", knowledge_root=root)
    owner = "00000000-0000-4000-8000-000000000001"
    assert registry.dispatch(owner, "knowledge_pages_list", {}, "catalog") == {
        "pages": [{"page": "a-first", "title": "First page"}, {"page": "z-last", "title": "Last page"}],
        "untrusted": True,
    }
    for slug in ("outside", "missing", "invalid-utf8"):
        with pytest.raises(OrganizationToolError, match="knowledge_page_not_found"):
            registry.dispatch(owner, "knowledge_page_read", {"page": slug}, "read")
    with pytest.raises(OrganizationToolError, match="invalid_tool_arguments"):
        registry.dispatch(owner, "knowledge_pages_list", {"root": str(tmp_path)}, "invalid")


def test_packaged_employer_help_is_discoverable_and_matches_executable_capabilities():
    """The shipped manual is accessible without a DB/provider and distinguishes UI-only lifecycle actions."""
    registry = ToolRegistry("postgresql://unused")
    owner = "00000000-0000-4000-8000-000000000001"
    catalog = registry.catalog()
    names = {item["name"] for item in catalog}
    assert {"knowledge_pages_list", "knowledge_page_read"} <= names
    assert not {"vacancy_withdraw", "vacancy_close", "vacancy_delete"} & names
    assert "employer-ui" in next(item["description"] for item in catalog if item["name"] == "knowledge_page_read")
    pages = registry.dispatch(owner, "knowledge_pages_list", {}, "catalog")
    assert {item["page"] for item in pages["pages"]} == {"employer-ui", "organization", "vacancy"}
    for page in pages["pages"]:
        result = registry.dispatch(owner, "knowledge_page_read", {"page": page["page"]}, "help")
        assert result["untrusted"] is True
        assert result["content"].startswith("# ")
    manual = registry.dispatch(owner, "knowledge_page_read", {"page": "employer-ui"}, "ui")["content"]
    for route in ("/vacancies", "/resumes", "/showcase", "/chat", "/statistics", "/settings"):
        assert route in manual
    assert "NOT registered assistant" in manual
    organization = registry.dispatch(owner, "knowledge_page_read", {"page": "organization"}, "org")["content"]
    assert "does not yet render" not in organization


@pytest.mark.integration
def test_document_receipt_replays_atomically_and_conflicts_on_payload():
    """A durable Markdown effect and its receipt replay from a fresh registry."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(database_url)
    owner = "00000000-0000-4000-8000-000000000001"
    registry = ToolRegistry(database_url)
    first = registry.dispatch(owner, "documents_create_md", {"name": "brief.md", "content": "hello"}, "doc-replay")
    second = ToolRegistry(database_url).dispatch(owner, "documents_create_md", {"name": "brief.md", "content": "hello"}, "doc-replay")
    assert first == second
    with pytest.raises(OrganizationToolError, match="idempotency_key_conflict"):
        registry.dispatch(owner, "documents_create_md", {"name": "brief.md", "content": "changed"}, "doc-replay")


@pytest.mark.integration
def test_vacancy_create_and_edit_receipts_replay_exact_results():
    """The in-connection Store path and receipt return one durable draft result."""
    database_url = os.getenv("AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENTS_TEST_DATABASE_URL is not configured")
    migrate(database_url)
    owner = "00000000-0000-4000-8000-000000000011"
    registry = ToolRegistry(database_url, matching=Matching())
    created = registry.dispatch(owner, "vacancy_create", {"fields": {"title": "Backend"}}, "vacancy-create")
    replay = ToolRegistry(database_url, matching=Matching()).dispatch(owner, "vacancy_create", {"fields": {"title": "Backend"}}, "vacancy-create")
    assert created == replay
    vacancy_id = created["draft_id"]
    edited = registry.dispatch(owner, "vacancy_edit", {"vacancy_id": vacancy_id, "expected_revision": 1,
                                                        "fields": {"title": "Senior Backend"}}, "vacancy-edit")
    assert edited["revision"] == 2
    assert registry.dispatch(owner, "vacancy_edit", {"vacancy_id": vacancy_id, "expected_revision": 1,
                                                      "fields": {"title": "Senior Backend"}}, "vacancy-edit") == edited
