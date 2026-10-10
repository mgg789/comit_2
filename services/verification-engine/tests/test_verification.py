import pytest
from fastapi.testclient import TestClient
from verification_engine import connectors
from verification_engine.connectors import RepositoryProbe
from verification_engine.main import create_app
from verification_engine.service import VerificationService


async def fsp_fetcher(fsp_id: str) -> dict:
    assert fsp_id == "fsp-001"
    return {"fsp_id": fsp_id, "rank": "I", "rating": 1876, "events": [], "certificates": []}


def repository_fetcher(url: str) -> RepositoryProbe:
    return RepositoryProbe(url=url, status_code=200, nonempty=True, bytes_observed=42,
                           canonical_url="https://github.com/acme/project", tree_entries=1, content_bytes=42)


def client() -> TestClient:
    return TestClient(create_app(VerificationService(fsp_fetcher=fsp_fetcher, repository_fetcher=repository_fetcher), internal_token="synthetic-token"))


def test_fsp_mock_confirms_without_account_identity() -> None:
    response = client().post("/verify/fsp", headers={"X-Internal-Token": "synthetic-token"}, json={"fsp_id": "fsp-001", "claimed_rating": 1876, "origin": "mock"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert "candidate_id" not in body["verified_value"]


def test_live_fsp_is_explicitly_unbound() -> None:
    response = client().post("/verify/fsp", headers={"X-Internal-Token": "synthetic-token"}, json={"fsp_id": "fsp-001", "origin": "live_unbound"})
    assert response.json()["error_code"] == "fsp_origin_unbound"
    assert response.json()["status"] == "pending"


def test_repository_confirms_nonempty_public_fact_but_not_authorship() -> None:
    response = client().post("/verify/repository", headers={"X-Internal-Token": "synthetic-token"}, json={
        "url": "https://github.com/acme/project", "contribution_narrative": "Implemented the API adapter.",
    })
    assert response.status_code == 200
    value = response.json()["verified_value"]
    assert value["repository_nonempty"] is True
    assert value["authorship_proven"] is False


def test_hh_without_token_is_safe_503() -> None:
    response = client().post("/verify/hh", headers={"X-Internal-Token": "synthetic-token"}, json={"resume_id": "resume-1"})
    assert response.status_code == 503
    assert response.json()["detail"] == "hh_token_unavailable"


def test_internal_auth_fails_closed() -> None:
    response = TestClient(create_app(VerificationService(fsp_fetcher=fsp_fetcher))).post(
        "/verify/fsp", json={"fsp_id": "fsp-001", "origin": "mock"})
    assert response.status_code == 503


def test_repository_rejects_noncanonical_url() -> None:
    response = client().post("/verify/repository", headers={"X-Internal-Token": "synthetic-token"}, json={
        "url": "https://github.com:443/acme/project?x=1", "contribution_narrative": "x",
    })
    assert response.status_code == 422


def test_git_probe_parses_nul_delimited_tree_and_uses_safe_options(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(arguments, *, root=None, timeout=15, size_root=None):
        calls.append((arguments, root))
        if arguments[0] == "clone":
            return 0, b"", b""
        return 0, b"100644 blob deadbeef 7\tline\nname.txt\0", b""

    monkeypatch.setattr(connectors, "_run_git", fake_run)
    result = connectors._probe_git_sync("https://github.com/acme/project")
    assert result.nonempty is True
    assert result.tree_entries == 1
    assert result.content_bytes == 7
    assert "--filter=blob:none" not in connectors.GIT_OPTIONS
    assert "-c" in connectors.GIT_OPTIONS and "protocol.allow=never" in connectors.GIT_OPTIONS


def test_git_probe_rejects_zero_size_tree(monkeypatch) -> None:
    def fake_run(arguments, *, root=None, timeout=15, size_root=None):
        if arguments[0] == "clone":
            return 0, b"", b""
        return 0, b"100644 blob deadbeef 0\tempty.txt\0", b""

    monkeypatch.setattr(connectors, "_run_git", fake_run)
    result = connectors._probe_git_sync("https://github.com/acme/project")
    assert result.nonempty is False


def test_gitlab_group_paths_are_canonicalized_without_broadening_other_hosts() -> None:
    canonical, host, owner, repository = connectors.canonical_repository_url(
        "https://gitlab.com/group/subgroup/project.git",
    )
    assert canonical == "https://gitlab.com/group/subgroup/project"
    assert host == "gitlab.com"
    assert owner == "group/subgroup"
    assert repository == "project"
    with pytest.raises(connectors.ProviderRejected):
        connectors.canonical_repository_url("https://github.com/group/subgroup/project")
