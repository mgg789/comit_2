"""Real loopback FSP demo authorization, PostgreSQL projection and Harrier PDF.

Uses synthetic identities and the real encrypted OTP mailbox. Never prints
authorization codes, sessions, fixture names or provider payloads.
"""

import json
from uuid import uuid4

import candidate_profile_acceptance as helpers
import httpx


def call(client: httpx.Client, account: dict, path: str, body: dict,
         expected: int = 200) -> dict:
    """Call the authenticated candidate boundary with sanitized failure output."""
    response = client.post(path, headers={"Authorization": account["authorization"]}, json=body)
    assert response.status_code == expected, f"FSP demo boundary failed: {path} ({response.status_code})"
    return response.json()


def main() -> None:
    """Exercise fixture login, bulk import, replay protection and real projections."""
    helpers.assert_isolated()
    helpers.migrate()
    checks = []
    with httpx.Client(base_url=helpers.API_URL, timeout=20) as api:
        candidate = helpers.login(api, "candidate", f"fsp-demo-{uuid4()}@example.test")
        other = helpers.login(api, "candidate", f"fsp-other-{uuid4()}@example.test")
        employer = helpers.login(api, "employer", f"fsp-employer-{uuid4()}@example.test")
        source = helpers.put_profile(api, candidate, helpers.profile("Synthetic FSP candidate", "Москва", (100000, 180000)))
        started = call(api, candidate, "/candidate/profile/fsp/demo/authorize", {
            "expected_revision": source["revision"], "demo_fixture_id": "fsp-001"})
        authorize_url = httpx.URL(started["authorize_url"])
        assert authorize_url.host in {"127.0.0.1", "localhost"}
        with httpx.Client(timeout=10) as registry:
            provider = registry.get(str(authorize_url))
        assert provider.status_code == 200, "demo registry authorization failed"
        receipt = provider.json()
        callback = {"expected_revision": source["revision"], "state": started["state"], "code": receipt["code"]}
        call(api, other, "/candidate/profile/fsp/demo/callback", callback, 409)
        linked = call(api, candidate, "/candidate/profile/fsp/demo/callback", callback)
        call(api, candidate, "/candidate/profile/fsp/demo/callback", callback, 409)
        assert linked["fields"]["fsp_id"] == "fsp-001"
        claims = linked["fields"]["achievements"]
        assert len(claims) == 2 and len({item["id"] for item in claims}) == 2
        assert all(linked["evidence"][f"achievements:{item['id']}"]["status"] == "confirmed" for item in claims)
        checks.append("real_demo_registry_state_exchange_bulk_import_wrong_owner_and_replay")

        repeated = call(api, candidate, "/candidate/profile/fsp/demo/authorize", {
            "expected_revision": linked["revision"], "demo_fixture_id": "fsp-001"})
        with httpx.Client(timeout=10) as registry:
            again = registry.get(repeated["authorize_url"]).json()
        linked = call(api, candidate, "/candidate/profile/fsp/demo/callback", {
            "expected_revision": linked["revision"], "state": repeated["state"], "code": again["code"]})
        assert {item["id"] for item in linked["fields"]["achievements"]} == {item["id"] for item in claims}
        checks.append("repeat_import_preserves_stable_claim_ids_without_duplicates")

        for _ in range(20):
            helpers.run_profile_projection()
            view = api.get("/candidate/profile", headers={"Authorization": candidate["authorization"]}).json()
            if view["projection"]["state"] == "delivered":
                break
        else:
            raise AssertionError("FSP source projection did not drain")
        for _ in range(20):
            helpers.run_matching_worker("worker")
            with httpx.Client(base_url=helpers.MATCHING_URL, headers={"X-Internal-Token": helpers.TOKEN}) as matching:
                state = matching.get(f"/internal/candidates/{candidate['id']}").json()
            if state.get("state") == "ready":
                break
        else:
            raise AssertionError("FSP candidate did not reach pool")
        detail = api.get(f"/employer/candidates/{candidate['id']}", headers={"Authorization": employer["authorization"]})
        assert detail.status_code == 200, "FSP public card unavailable"
        public = detail.json()["public_profile"]
        assert len(public["achievements"]) == 2
        assert all(item["status"] == "confirmed" for item in public["achievements"])
        assert any(item["kind"] == "fsp" and item["verified_value"]["demo_registry"] is True for item in public["external_evidence"])
        pdf = api.get(detail.json()["resume_url"].removeprefix("/api"), headers={"Authorization": employer["authorization"]})
        assert pdf.status_code == 200
        text = helpers.pdf_text(pdf.content)
        assert "Демо-реестр ФСП" in text and "подтверждено" in text
        assert "Synthetic FSP candidate" not in text and "+79990000001" not in text
        checks.append("real_harrier640_pool_public_card_and_demo_badged_redacted_pdf")
    print(json.dumps({"result": "passed", "checks": checks, "demo": True, "real_fsp": False,
                      "achievements": 2, "sanitized": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
