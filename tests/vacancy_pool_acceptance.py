"""Real local vacancy→combine-readiness acceptance, including candidate compatibility.

This mutates only a dedicated fresh local database through HTTP: registry expands
once, and a vacancy is paused/reopened with new requirements.
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
    """Verify optional requirement combinations with real workers, PostgreSQL and Harrier."""
    api_url = os.environ.get("PIPELINE_ACCEPTANCE_URL", "http://127.0.0.1:38080")
    if not api_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("acceptance requires an isolated loopback stack")
    fixtures = json.loads((BASE.parent / "data/seed/vacancy-pipeline.json").read_text())
    headers = {"x-internal-token": os.environ["CANDIDATE_PIPELINE_TOKEN"]}
    with httpx.Client(base_url=api_url, headers=headers, timeout=15) as client:
        assert httpx.get(f"{api_url}/internal/vacancies/ready").status_code == 401
        initial = client.get("/internal/strict-schema")
        initial.raise_for_status()
        assert len(initial.json()["coordinates"]) == 37, "use a fresh dedicated database"
        for fixture in fixtures:
            response = client.put(f"/internal/vacancies/{fixture['vacancy_id']}", json=fixture["input"])
            response.raise_for_status()

        def ready_list() -> list[dict]:
            response = client.get("/internal/vacancies/ready")
            response.raise_for_status()
            return response.json()["items"]

        def await_ready(ids: set[str], version: int) -> list[dict]:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                items = ready_list()
                if {item["vacancy_id"] for item in items} == ids and all(
                    item["strict_schema_version"] == version for item in items
                ):
                    return items
                time.sleep(0.25)
            raise AssertionError("vacancy readiness timed out")

        ids = {fixture["vacancy_id"] for fixture in fixtures if fixture["expected_ready"]}
        initial_ready = await_ready(ids, 1)
        lookup = {item["vacancy_id"]: item for item in initial_ready}
        class_only = lookup[fixtures[0]["vacancy_id"]]
        assert class_only["class"] == "backend" and class_only["build_mode"] == "class_only"
        assert class_only["requirement_mask"] == [False] * 37
        assert class_only["strict_vector"] == [0.0] * 37
        assert not class_only["semantic_available"] and class_only["semantic_vector"] is None
        domain_only = lookup[fixtures[1]["vacancy_id"]]
        assert domain_only["class"] is None and domain_only["build_mode"] == "requirements_only"
        assert sum(domain_only["requirement_mask"]) == 1
        zero = lookup[fixtures[4]["vacancy_id"]]
        assert zero["strict_vector"][15] == 0 and zero["requirement_mask"][15]
        assert not zero["requirement_mask"][16]
        mixed = lookup[fixtures[3]["vacancy_id"]]
        assert mixed["class"] == "ml" and mixed["build_mode"] == "mixed"
        assert mixed["semantic_available"] and len(mixed["semantic_vector"]) == 640
        assert math.isclose(sum(x*x for x in mixed["semantic_vector"]), 1, abs_tol=0.01)
        for fixture in fixtures:
            if not fixture["expected_ready"]:
                status = client.get(f"/internal/vacancies/{fixture['vacancy_id']}")
                status.raise_for_status()
                assert status.json()["state"] == "draft"
                assert client.get(f"/internal/vacancies/{fixture['vacancy_id']}/ready").status_code == 404
        # Candidate document and vacancy query must share dimensions and model space.
        candidate = json.loads((BASE.parent / "data/seed/candidate-pipeline.json").read_text())[0]
        client.put(f"/internal/candidates/{candidate['candidate_id']}/profile", json=candidate["profile"]).raise_for_status()
        deadline = time.monotonic() + 180
        while True:
            pool = client.get("/internal/pool").json()["items"]
            if pool:
                break
            assert time.monotonic() < deadline, "candidate readiness timed out"
            time.sleep(0.25)
        assert mixed["semantic_space_id"] == pool[0]["semantic_space_id"]
        assert mixed["semantic_model_version"] != pool[0]["semantic_model_version"]
        replay = client.put(f"/internal/vacancies/{fixtures[0]['vacancy_id']}", json=fixtures[0]["input"])
        assert replay.json()["revision"] == class_only["revision"]
        extension = client.post("/internal/strict-schema/coordinates", json={"group": "stack", "key": "rust"})
        extension.raise_for_status()
        assert extension.json()["version"] == 2
        migrated = await_ready(ids, 2)
        deadline = time.monotonic() + 180
        while True:
            response = client.get("/internal/pool")
            response.raise_for_status()
            migrated_candidates = response.json()["items"]
            if migrated_candidates and all(item["strict_schema_version"] == 2 for item in migrated_candidates):
                assert len(migrated_candidates[0]["strict_vector"]) == 38
                break
            assert time.monotonic() < deadline, "candidate schema backfill timed out"
            time.sleep(0.25)
        for item in migrated:
            original = lookup[item["vacancy_id"]]
            assert item["strict_vector"][:37] == original["strict_vector"]
            assert item["requirement_mask"][:37] == original["requirement_mask"]
            assert item["strict_vector"][37] == 0 and not item["requirement_mask"][37]
            assert item["semantic_vector"] == original["semantic_vector"]
        status_path = f"/internal/vacancies/{fixtures[0]['vacancy_id']}"
        paused = copy.deepcopy(fixtures[0]["input"])
        paused.update(source_revision=2, status="pending")
        client.put(status_path, json=paused).raise_for_status()
        assert client.get(f"{status_path}/ready").status_code == 404
        assert len(ready_list()) == len(ids) - 1
        reopened = copy.deepcopy(paused)
        reopened.update(source_revision=3, status="open")
        reopened["stacks"] = {"rust": 4}
        client.put(status_path, json=reopened).raise_for_status()
        again = await_ready(ids, 2)
        resumed = next(item for item in again if item["vacancy_id"] == fixtures[0]["vacancy_id"])
        assert resumed["revision"] == 3 and resumed["strict_vector"][37] == 4 / 7
        assert resumed["requirement_mask"][37]
        assert client.put(status_path, json=paused).status_code == 409
        report = {"result": "passed", "submitted": len(fixtures), "ready": len(ids),
                  "not_ready": len(fixtures) - len(ids), "strict_before": 37, "strict_after": 38,
                  "semantic_dimensions": 640, "checks": ["class_only", "domain_only", "stack_only",
                  "mixed", "explicit_zero_mask", "soft_only_not_ready", "bio_only_not_ready",
                  "real_harrier_query_document_space", "shared_schema_backfill", "idempotency",
                  "pending_withdrawal", "reopen_new_coordinate", "stale_revision"]}
        artifact = BASE / "artifacts"
        artifact.mkdir(exist_ok=True)
        (artifact / "vacancy-pool-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
