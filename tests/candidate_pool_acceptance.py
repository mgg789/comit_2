"""Real HTTP + PostgreSQL + Harrier acceptance on an isolated local stack.

Never point this script at production: it submits synthetic profiles and adds
one strict coordinate. A continuously running real worker is required.
"""

import copy
import json
import math
import os
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent


def main() -> None:
    """Verify draft/ready, provenance, rank, PDFs, backfill and consent withdrawal."""
    api_url = os.environ.get("PIPELINE_ACCEPTANCE_URL", "http://127.0.0.1:38080")
    if not api_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("acceptance requires an isolated loopback stack")
    token = os.environ["CANDIDATE_PIPELINE_TOKEN"]
    fixtures = json.loads((BASE.parent / "data/seed/candidate-pipeline.json").read_text())
    with httpx.Client(base_url=api_url, headers={"x-internal-token": token}, timeout=15) as client:
        assert httpx.get(f"{api_url}/internal/pool").status_code == 401

        def get_pool() -> list[dict]:
            response = client.get("/internal/pool")
            response.raise_for_status()
            return response.json()["items"]

        def await_pool(ids: set[str], version: int) -> list[dict]:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                items = get_pool()
                if {item["candidate_id"] for item in items} == ids and all(
                    item["strict_schema_version"] == version for item in items
                ):
                    return items
                time.sleep(0.25)
            raise AssertionError("pool readiness timed out")

        schema = client.get("/internal/strict-schema").json()
        assert len(schema["coordinates"]) == 37, "use a fresh isolated database"
        for fixture in fixtures:
            response = client.put(f"/internal/candidates/{fixture['candidate_id']}/profile", json=fixture["profile"])
            response.raise_for_status()
        ids = {fixture["candidate_id"] for fixture in fixtures[:3]}
        pool = await_pool(ids, schema["version"])
        for item in pool:
            assert len(item["strict_vector"]) == 37 and len(item["semantic_vector"]) == 640
            assert math.isclose(sum(x*x for x in item["semantic_vector"]), 1, abs_tol=0.01)
            assert "email" not in item and "phone" not in item
            assert len(item["strict_evidence"]) == 37
        first = next(item for item in pool if item["candidate_id"] == fixtures[0]["candidate_id"])
        python = next(e for e in first["strict_evidence"] if e["group"] == "stack" and e["key"] == "python")
        assert python["status"] == "self_reported" and first["strict_vector"][python["index"]] == 5 / 7
        backend = sorted([item for item in pool if item["category"]["specialization"] == "backend"], key=lambda i: i["rank"])
        assert [item["category"]["grade"] for item in backend] == ["senior", "junior"]
        draft = client.get(f"/internal/candidates/{fixtures[3]['candidate_id']}").json()
        assert draft["state"] == "draft" and "pool_consent" in draft["missing_requirements"]
        artifacts = BASE / "artifacts"
        artifacts.mkdir(exist_ok=True)
        pdf = client.get(first["resume_url"])
        pdf.raise_for_status()
        assert pdf.content.startswith(b"%PDF-")
        (artifacts / "synthetic-candidate-resume.pdf").write_bytes(pdf.content)

        # Equal snapshots are idempotent; outdated snapshots cannot restore consent.
        replay = client.put(f"/internal/candidates/{fixtures[0]['candidate_id']}/profile", json=fixtures[0]["profile"])
        replay.raise_for_status()
        assert replay.json()["profile_revision"] == first["profile_revision"]
        extension = client.post("/internal/strict-schema/coordinates", json={"group": "stack", "key": "rust"})
        extension.raise_for_status()
        schema2 = extension.json()
        assert len(schema2["coordinates"]) == 38
        migrated = await_pool(ids, schema2["version"])
        for item in migrated:
            original = next(previous for previous in pool if previous["candidate_id"] == item["candidate_id"])
            assert item["strict_vector"][:37] == original["strict_vector"]
            assert item["strict_vector"][37] == 0
            assert item["semantic_vector"] == original["semantic_vector"]
        updated = copy.deepcopy(fixtures[0]["profile"])
        updated["source_revision"] += 1
        updated["stacks"].append({"key": "rust", "level": 4, "status": "partial", "provenance": ["synthetic-project"]})
        client.put(f"/internal/candidates/{fixtures[0]['candidate_id']}/profile", json=updated).raise_for_status()
        changed = await_pool(ids, schema2["version"])
        assert next(item for item in changed if item["candidate_id"] == fixtures[0]["candidate_id"])["strict_vector"][37] == 4 / 7
        withdrawn = copy.deepcopy(updated)
        withdrawn["source_revision"] += 1
        withdrawn["consents"]["pool"] = False
        client.put(f"/internal/candidates/{fixtures[0]['candidate_id']}/profile", json=withdrawn).raise_for_status()
        assert len(get_pool()) == 2
        assert client.get(first["resume_url"]).status_code == 404
        assert client.put(f"/internal/candidates/{fixtures[0]['candidate_id']}/profile", json=updated).status_code == 409
        report = {"result": "passed", "submitted": 4, "initial_ready": 3, "draft": 1,
                  "strict_dimensions_before": 37, "strict_dimensions_after": 38,
                  "semantic_dimensions": 640, "remaining_after_withdrawal": 2,
                  "checks": ["real_harrier", "public_pdf", "grade_rank", "unconfirmed_skills",
                             "idempotency", "strict_backfill", "new_coordinate", "consent_withdrawal", "stale_revision"]}
        (artifacts / "candidate-pool-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
