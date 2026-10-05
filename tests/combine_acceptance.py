"""HTTP acceptance on a fresh loopback PostgreSQL/Harrier stack with running real workers.

The script never starts workers or resets a database. It creates 200 synthetic
profiles and 10 vacancies, then mutates consent, registry and one vacancy.
The explicit pattern oracle checks contract eligibility, not hiring quality.
"""

import copy
import io
import json
import math
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from pypdf import PdfReader

BASE = Path(__file__).resolve().parent
CONFIDENCE = {"confirmed": 1.0, "partial": 0.8, "self_reported": 0.6, "pending": 0.5, "rejected": 0.0}


def fixtures() -> tuple[dict, list[dict], list[dict]]:
    """Expand checked-in patterns from the first candidate seed, preserving three shared narratives."""
    config = json.loads((BASE.parent / "data/seed/matching-scenarios.json").read_text(encoding="utf-8"))
    template = json.loads((BASE.parent / "data/seed/candidate-pipeline.json").read_text(encoding="utf-8"))[0]["profile"]
    candidates = []
    narratives = {
        "backend": "Разрабатываю backend API на Python и PostgreSQL, надёжные сервисы и интеграции.",
        "frontend": "Разрабатываю frontend интерфейсы на React и JavaScript, доступные веб-приложения.",
        "ml": "Разрабатываю модели машинного обучения на Python, классификацию и обработку данных.",
    }
    for pattern in config["patterns"]:
        settings = {**copy.deepcopy(config["defaults"]), **copy.deepcopy(pattern["overrides"])}
        for replica in range(config["replicas_per_pattern"]):
            serial = pattern["pattern"] * config["replicas_per_pattern"] + replica + 1
            candidate_id = str(UUID(f"61000000-0000-4000-8000-{serial:012d}"))
            profile = copy.deepcopy(template)
            for field in ("primary_direction", "city", "salary", "work_types", "relocation_ready", "relocation_cities"):
                profile[field] = settings[field]
            profile["grade_result"]["grade"] = settings["grade"]
            profile["grade_result"]["test_session_id"] = str(UUID(f"63000000-0000-4000-8000-{serial:012d}"))
            profile["display_name"] = f"Синтетический участник {serial}"
            profile["email"] = f"synthetic-combine-{serial}@example.test"
            profile["description"] = narratives[settings["primary_direction"]]
            profile["projects"] = [{"title": "Синтетический проект", "description": profile["description"],
                                     "status": "confirmed", "details": {"my_role": "Разработчик"}}]
            profile["preferences"] = ["Проектная команда"]
            for field in ("domains", "stacks"):
                profile[field] = [{"key": key, "level": level, "status": settings["evidence_status"],
                                   "provenance": ["synthetic-contract-oracle"]} for key, level in settings[field].items()]
            candidates.append({"candidate_id": candidate_id, "pattern": pattern["pattern"], "replica": replica,
                               "profile": profile})
    vacancies = [{"vacancy_id": str(UUID(f"64000000-0000-4000-8000-{item['scenario'] + 1:012d}")),
                  "scenario": item["scenario"], "name": item["name"], "eligible_patterns": item["eligible_patterns"],
                  "input": {**copy.deepcopy(config["vacancy_defaults"]), **copy.deepcopy(item["overrides"])}}
                 for item in config["vacancies"]]
    assert len(candidates) == 200 and len(vacancies) == 10
    return config, candidates, vacancies


def wait_for(check: Callable[[], Any], label: str, timeout: float) -> Any:
    """Poll a real HTTP condition within the configured timeout; report the failing stage only."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value is not None:
            return value
        time.sleep(0.5)
    raise AssertionError(f"acceptance timed out: {label}")


def get_json(client: httpx.Client, path: str) -> dict:
    """Read a successful internal endpoint without printing credentials or private response bodies."""
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def eligible_ids(vacancy: dict, candidates: list[dict], removed: set[str], excluded: set[str]) -> set[str]:
    """Use the handwritten fixture oracle, independent of matching-engine filters or scoring imports."""
    return {item["candidate_id"] for item in candidates if item["pattern"] in vacancy["eligible_patterns"]
            and item["candidate_id"] not in removed | excluded}


def check_score(card: dict, vacancy: dict, profile: dict) -> None:
    """Validate observable score decomposition, strict evidence and bounded semantic influence."""
    score = card["explanation"]
    assert 0 <= score["score"] <= 1 and 0 <= score["rating_score"] <= 1
    assert math.isclose(score["score"], sum(score[field] for field in (
        "rating_contribution", "strict_contribution", "semantic_contribution")), abs_tol=1e-10)
    assert abs(score["semantic_contribution"]) <= 0.1 + 1e-10
    targets = [("stack", key, level) for key, level in vacancy["input"].get("stacks", {}).items()]
    targets += [("domain", key, level) for key, level in vacancy["input"].get("domens", {}).items()]
    features = {(item["group"], item["key"]): item for item in score["top_matched_features"]}
    expected = []
    for group, key, target in targets:
        claim = next((value for value in profile["stacks" if group == "stack" else "domains"] if value["key"] == key), None)
        confidence = CONFIDENCE[claim["status"]] if claim else 0
        agreement = (1 - abs(claim["level"] - target) / 7) if confidence else 0
        contribution = agreement * confidence
        expected.append(contribution)
        feature = features[group, key]
        assert feature["status"] == (claim["status"] if claim else None)
        assert feature["target_level"] == target
        assert math.isclose(feature["contribution"], contribution, abs_tol=1e-10)
    strict = sum(expected) / len(expected) if expected else None
    assert score["strict_score"] is None if strict is None else math.isclose(score["strict_score"], strict, abs_tol=1e-10)
    rating_weight = 1 if strict is None else 0.45 / 0.8
    assert math.isclose(score["rating_contribution"], rating_weight * score["rating_score"], abs_tol=1e-10)
    assert math.isclose(score["strict_contribution"], 0 if strict is None else 0.35 / 0.8 * strict, abs_tol=1e-10)
    if not vacancy["input"].get("bio"):
        assert score["semantic_cosine"] is None and score["semantic_contribution"] == 0
        assert score["semantic_unavailable_reason"] == "vacancy_bio_absent"
    else:
        assert -1 <= score["semantic_cosine"] <= 1 and score["semantic_unavailable_reason"] is None
    factors = set(card["filter_factors"])
    assert {"salary_overlap", "work_type_accepted", "strict_schema_current"} <= factors
    assert ("class_match" if vacancy["input"].get("class") else "class_unrestricted") in factors
    assert ("remote_location" if vacancy["input"]["type"] == "remote" else (
        "same_city" if profile["city"] == vacancy["input"]["city"] else "relocation_accepted")) in factors
    forbidden = {"email", "phone", "provenance", "description", "projects", "experience", "preferences"}
    assert not forbidden.intersection(card)
    serialized = json.dumps(card, ensure_ascii=False)
    assert profile["email"] not in serialized and profile["display_name"] not in serialized


def main() -> None:
    """Exercise real workers via HTTP and save only synthetic fixtures, PDFs and an observed report."""
    api_url = os.environ.get("PIPELINE_ACCEPTANCE_URL", "http://127.0.0.1:38080")
    parsed = urlparse(api_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password:
        raise RuntimeError("acceptance requires a fresh isolated loopback HTTP stack")
    timeout = float(os.environ.get("PIPELINE_ACCEPTANCE_TIMEOUT", "600"))
    config, candidates, vacancies = fixtures()
    by_id = {value["candidate_id"]: value for value in candidates}
    artifacts = BASE / "artifacts/combine"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "fixtures.json").write_text(json.dumps({"candidates": candidates, "vacancies": vacancies}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    headers = {"x-internal-token": os.environ["CANDIDATE_PIPELINE_TOKEN"]}
    with httpx.Client(base_url=api_url, headers=headers, timeout=30) as client:
        assert httpx.get(f"{api_url}/internal/matching/events", timeout=10).status_code == 401
        schema = get_json(client, "/internal/strict-schema")
        assert schema["version"] == 1 and len(schema["coordinates"]) == 37, "fresh database required"
        assert get_json(client, "/internal/pool?limit=500")["items"] == [], "fresh candidate pool required"
        assert get_json(client, "/internal/vacancies/ready?limit=500")["items"] == [], "fresh vacancy pool required"
        for fixture in candidates:
            client.put(f"/internal/candidates/{fixture['candidate_id']}/profile", json=fixture["profile"]).raise_for_status()
        for fixture in vacancies:
            client.put(f"/internal/vacancies/{fixture['vacancy_id']}", json=fixture["input"]).raise_for_status()

        def pool_ready(ids: set[str], version: int) -> list[dict] | None:
            pool = get_json(client, "/internal/pool?limit=500")["items"]
            return pool if {item["candidate_id"] for item in pool} == ids and all(
                item["strict_schema_version"] == version for item in pool) else None

        def vacancies_ready(count: int, version: int) -> list[dict] | None:
            items = get_json(client, "/internal/vacancies/ready?limit=500")["items"]
            return items if len(items) == count and all(item["strict_schema_version"] == version for item in items) else None

        def drained() -> dict | None:
            status = get_json(client, "/internal/matching/events")
            assert status["quarantined_count"] == 0 and status["retrying_count"] == 0
            return status if all(status[field] == 0 for field in ("candidate_pending", "vacancy_pending", "queued_count")) else None

        all_ids = set(by_id)
        initial_pool = wait_for(lambda: pool_ready(all_ids, 1), "200 candidates", timeout)
        initial_vacancies = wait_for(lambda: vacancies_ready(10, 1), "10 vacancies", timeout)
        for item in initial_pool:
            assert len(item["strict_vector"]) == len(item["strict_evidence"]) == 37
            assert len(item["semantic_vector"]) == 640
            assert math.isclose(sum(value * value for value in item["semantic_vector"]), 1, abs_tol=0.01)
            assert item["semantic_model_version"].startswith("microsoft/harrier-oss-v1-270m@")
        assert len({tuple(item["semantic_vector"]) for item in initial_pool}) == 3
        wait_for(drained, "initial durable delivery", timeout)
        for fixture in vacancies:
            client.post(f"/internal/vacancies/{fixture['vacancy_id']}/combine", json={"top_k": 10}).raise_for_status()

        def batches(scanned: int, version: int, removed: set[str], excluded_v0: set[str], old: dict | None = None) -> dict | None:
            pages = {}
            for vacancy in vacancies:
                path = f"/internal/vacancies/{vacancy['vacancy_id']}/selections?limit=10"
                response = client.get(path)
                if response.status_code in (404, 409):
                    return None
                response.raise_for_status()
                page = response.json()
                status = page["run"]
                if status["state"] != "ready" or status["scanned_count"] != scanned or status["strict_schema_version"] != version:
                    return None
                if old and status["run_id"] == old[vacancy["vacancy_id"]]["run"]["run_id"]:
                    return None
                expected_ids = eligible_ids(vacancy, candidates, removed, excluded_v0 if vacancy["scenario"] == 0 else set())
                assert status["passed_count"] == len(expected_ids), (vacancy["name"], status["passed_count"], len(expected_ids))
                assert status["selected_count"] == len(page["items"]) == min(10, len(expected_ids))
                assert {item["candidate_id"] for item in page["items"]} <= expected_ids
                assert [item["position"] for item in page["items"]] == list(range(1, len(page["items"]) + 1))
                for item in page["items"]:
                    check_score(item, vacancy, by_id[item["candidate_id"]]["profile"])
                pages[vacancy["vacancy_id"]] = page
            return pages if drained() is not None else None

        initial = wait_for(lambda: batches(200, 1, set(), set()), "initial top10 batches", timeout)
        pdf_count = 0
        for vacancy in vacancies:
            page = initial[vacancy["vacancy_id"]]
            if page["items"]:
                card = page["items"][0]
                pdf = client.get(card["resume_url"])
                pdf.raise_for_status()
                assert pdf.content.startswith(b"%PDF-")
                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf.content)).pages)
                profile = by_id[card["candidate_id"]]["profile"]
                assert profile["display_name"] not in text and not re.search(r"[\w.+-]+@[\w.-]+\.[\w-]+", text)
                assert "79991234567" not in re.sub(r"\D", "", text)
                (artifacts / f"vacancy-{vacancy['scenario']}-public.pdf").write_bytes(pdf.content)
                pdf_count += 1
        v0 = vacancies[0]
        path = f"/internal/vacancies/{v0['vacancy_id']}"
        key = str(uuid4())
        payload = {"top_k": 10, "idempotency_key": key}
        first = client.post(f"{path}/combine", json=payload)
        first.raise_for_status()
        repeat = client.post(f"{path}/combine", json=payload)
        repeat.raise_for_status()
        assert first.json()["run_id"] == repeat.json()["run_id"]
        assert client.post(f"{path}/combine", json={"top_k": 11, "idempotency_key": key}).status_code == 409
        visited, pending = [card["candidate_id"] for card in initial[v0["vacancy_id"]]["items"][1:3]]
        for candidate_id, state in ((visited, "visited"), (pending, "pending")):
            client.put(f"{path}/selection-state/{candidate_id}", json={"state": state}).raise_for_status()
        wait_for(lambda: batches(200, 1, set(), {visited, pending}), "persistent exclusions", timeout)
        assert client.put(f"{path}/selection-state/{visited}", json={"state": "eligible"}).status_code == 409
        client.put(f"{path}/selection-state/{pending}", json={"state": "eligible"}).raise_for_status()
        before_withdrawal = wait_for(lambda: batches(200, 1, set(), {visited}), "pending release", timeout)
        withdrawn = candidates[0]["candidate_id"]
        update = copy.deepcopy(candidates[0]["profile"])
        update["source_revision"] = 2
        update["consents"]["pool"] = False
        client.put(f"/internal/candidates/{withdrawn}/profile", json=update).raise_for_status()
        assert client.get(f"/internal/pool/{withdrawn}/resume.pdf").status_code == 404
        pool199 = wait_for(lambda: pool_ready(all_ids - {withdrawn}, 1), "withdrawn candidate removed", timeout)
        after_withdrawal = wait_for(lambda: batches(199, 1, {withdrawn}, {visited}, before_withdrawal), "all batches after withdrawal", timeout)
        extension = client.post("/internal/strict-schema/coordinates", json={"group": "stack", "key": "rust"})
        extension.raise_for_status()
        assert extension.json()["version"] == 2 and len(extension.json()["coordinates"]) == 38
        pool2 = wait_for(lambda: pool_ready(all_ids - {withdrawn}, 2), "candidate schema migration", timeout)
        vacancies2 = wait_for(lambda: vacancies_ready(10, 2), "vacancy schema migration", timeout)
        old_pool = {item["candidate_id"]: item for item in pool199}
        old_vacancies = {item["vacancy_id"]: item for item in initial_vacancies}
        for item in pool2:
            assert len(item["strict_vector"]) == len(item["strict_evidence"]) == 38
            assert item["strict_vector"][:37] == old_pool[item["candidate_id"]]["strict_vector"] and item["strict_vector"][37] == 0
            assert item["semantic_vector"] == old_pool[item["candidate_id"]]["semantic_vector"]
        for item in vacancies2:
            assert len(item["strict_vector"]) == len(item["requirement_mask"]) == 38
            assert item["strict_vector"][:37] == old_vacancies[item["vacancy_id"]]["strict_vector"]
            assert item["strict_vector"][37] == 0 and item["requirement_mask"][37] is False
        after_schema = wait_for(lambda: batches(199, 2, {withdrawn}, {visited}, after_withdrawal), "all schema2 main batches", timeout)
        closed = copy.deepcopy(v0["input"])
        closed.update(source_revision=2, status="closed")
        client.put(path, json=closed).raise_for_status()
        assert client.get(f"{path}/selections").status_code == 404
        assert get_json(client, f"{path}/combine")["state"] == "obsolete"
        wait_for(lambda: vacancies_ready(9, 2), "closed vacancy removed", timeout)
        final_delivery = wait_for(drained, "final durable delivery", timeout)
        report = {"result": "passed", "evaluation": "contractual_synthetic", "submitted_candidates": 200,
                  "submitted_vacancies": 10, "remaining_candidates": 199, "remaining_open_vacancies": 9,
                  "strict_dimensions": [37, 38], "semantic_dimensions": 640, "semantic_narrative_variations": 3,
                  "pdf_samples_checked": pdf_count, "canonical_note": config["canonical_note"],
                  "initial_passed_counts": {fixture["name"]: initial[fixture["vacancy_id"]]["run"]["passed_count"] for fixture in vacancies},
                  "quality_metrics": None,
                  "quality_metrics_omitted_reason": "No independently labelled human hiring relevance: eligibility fixtures validate gates, not Precision/MRR hiring quality.",
                  "checks": ["real_harrier_model_identity", "200_full_pool_scan", "handwritten_eligibility_oracle",
                             "score_decomposition", "bounded_semantic_delta", "explicit_zero_and_absent_evidence",
                             "contact_free_cards_and_pdf", "manual_idempotency", "visited_terminal", "pending_release",
                             "candidate_withdrawal_all_runs", "shared_schema_backfill", "closed_vacancy_obsolete", "outbox_drain"],
                  "final_delivery": final_delivery}
        for filename, content in (("initial-selections.json", initial), ("schema2-selections.json", after_schema), ("report.json", report)):
            (artifacts / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
