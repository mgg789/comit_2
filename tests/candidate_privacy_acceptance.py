"""Extend candidate_profile_acceptance on its isolated synthetic database.

Run after that harness with identical PROFILE_ACCEPTANCE_* environment. Exercises
owner PDF, optional training, source-only revocation/export and retained top-k.
No email, token, OTP, PDF text or private payload is written to the report.
"""

import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import candidate_profile_acceptance as helpers
import httpx
import psycopg
from matching_engine.learning_store import LearningStore


def main() -> None:
    """Verify separate ML consent on the completed real-HTTP candidate fixture."""
    helpers.assert_isolated()
    with psycopg.connect(helpers.DATABASE_URL) as connection:
        row = connection.execute("""SELECT a.email,p.revision FROM accounts a
            JOIN candidate_profiles p ON p.candidate_id=a.id
            WHERE p.fields->>'display_name'='Candidate One'""").fetchone()
        employer_email = connection.execute("SELECT email FROM accounts WHERE role='employer' AND email LIKE '%-employer@example.test'").fetchone()[0]
        assert row is not None and row[0].endswith("@example.test")
        assert connection.execute("SELECT count(*) FROM candidate_profiles").fetchone()[0] == 5
    checks = []
    with httpx.Client(base_url=helpers.API_URL, timeout=30) as api:
        candidate = helpers.login(api, "candidate", row[0])
        employer = helpers.login(api, "employer", employer_email)
        headers = {"Authorization": candidate["authorization"]}
        own_pdf = api.get("/candidate/resume.pdf", headers=headers)
        assert own_pdf.status_code == 200 and row[0] in helpers.pdf_text(own_pdf.content)
        assert api.get("/candidate/resume.pdf", headers={"Authorization": employer["authorization"]}).status_code == 401
        checks.append("owner_real_pdf_and_foreign_role_denied")
        current = api.get("/candidate/profile", headers=headers).json()
        assert current["consents"]["training"] is False
        assert current["consents"]["pool"] is True
        with psycopg.connect(helpers.DATABASE_URL) as connection:
            assert connection.execute("SELECT count(*) FROM learning_pipeline.samples WHERE features IS NOT NULL").fetchone()[0] == 0
        checks.append("profile_pool_without_training_opt_in_or_learning_features")
        grant = api.patch("/candidate/profile", headers=headers, json={
            "expected_revision": current["revision"], "fields": {}, "consent": {
                "processing": True, "pool": True, "version": "profile-v1",
                "training": True, "training_version": "training-v1"}})
        assert grant.status_code == 200 and grant.json()["consents"]["training_accepted_at"]
        helpers.run_profile_projection()
        helpers.run_matching_worker("worker")
        helpers.run_matching_worker("combine-worker")
        with psycopg.connect(helpers.DATABASE_URL) as connection:
            draft_id = connection.execute("SELECT draft_id FROM agents_service.drafts WHERE owner_id=%s", (employer["id"],)).fetchone()[0]
        path = f"/employer/vacancy-drafts/{draft_id}"
        employer_headers = {"Authorization": employer["authorization"]}
        assert api.post(path + "/combine", headers=employer_headers, json={"top_k": 2}).status_code == 200
        helpers.run_matching_worker("combine-worker")
        selected = helpers.await_json(api, path + "/selections?limit=2", lambda page: page["run"]["state"] == "ready", headers=employer_headers)
        assert len(selected["items"]) == 2
        with tempfile.TemporaryDirectory(prefix="comit-privacy-export-") as directory:
            learning = LearningStore(helpers.DATABASE_URL)
            key = b"synthetic-privacy-test-pseudonym-key-32"
            assert learning.export(Path(directory) / "granted", key)["rows"] >= 1
            with psycopg.connect(helpers.DATABASE_URL) as connection:
                stamp = connection.execute("SELECT consent_version,consent_accepted_at FROM learning_pipeline.samples WHERE candidate_id=%s AND features IS NOT NULL LIMIT 1", (candidate["id"],)).fetchone()
                assert stamp[0] == "training-v1" and stamp[1] is not None
            checks.append("separate_server_stamped_opt_in_allows_export")
            revoke = api.patch("/candidate/profile", headers=headers, json={
                "expected_revision": grant.json()["revision"], "fields": {}, "consent": {
                    "processing": True, "pool": True, "version": "profile-v1", "training": False}})
            assert revoke.status_code == 200 and revoke.json()["consents"]["training_accepted_at"] is None
            with psycopg.connect(helpers.DATABASE_URL) as connection:
                snapshot = connection.execute("SELECT profile FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate["id"],)).fetchone()[0]
                assert snapshot["consents"]["training"] is True
            assert learning.export(Path(directory) / "revoked", key)["rows"] == 0
            checks.append("source_only_training_revocation_blocks_old_matching_snapshot_export")
        helpers.run_profile_projection()
        helpers.run_matching_worker("worker")
        helpers.run_matching_worker("combine-worker")
        assert api.post(path + "/combine", headers=employer_headers, json={"top_k": 2}).status_code == 200
        helpers.run_matching_worker("combine-worker")
        selected = helpers.await_json(api, path + "/selections?limit=2", lambda page: page["run"]["state"] == "ready", headers=employer_headers)
        assert len(selected["items"]) == 2 and candidate["id"] in {item["candidate_id"] for item in selected["items"]}
        checks.append("training_revoked_candidate_still_in_pool_and_top2")
    report = {"result": "passed", "checks": checks, "sanitized": True,
              "database": urlparse(helpers.DATABASE_URL).path.lstrip("/"), "smtp_used": False}
    (helpers.BASE / "artifacts/candidate-privacy-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
