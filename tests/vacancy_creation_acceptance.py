"""Real local gateway/PG/Laya/DeepSeek/Harrier vacancy creation acceptance.

Uses synthetic inputs only. Never resets a DB, starts services, or exports secrets.
Workers run synchronously against the explicitly named isolated acceptance database.
"""

import argparse
import gc
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from agents_service.providers import DeepSeekClient
from agents_service.store import Store
from agents_service.worker import Worker
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.models import CandidateProfile
from matching_engine.store import Store as CandidateStore
from matching_engine.vacancy_store import VacancyStore
from matching_engine.vacancy_worker import VacancyWorker
from matching_engine.worker import Worker as CandidateWorker

BASE = Path(__file__).resolve().parent


def deepseek_from_file(path: Path) -> DeepSeekClient:
    """Read only the DeepSeek section from a local credentials file; never print values."""
    section = False
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().rstrip(":").strip().casefold() == "deepseek":
            section = True
            continue
        if not section:
            continue
        for key in ("URL", "Model", "Token"):
            if line.strip().startswith(key + ":"):
                values[key] = line.partition(":")[2].strip()
        if set(values) == {"URL", "Model", "Token"}:
            break
    if set(values) != {"URL", "Model", "Token"}:
        raise RuntimeError("DeepSeek settings section missing")
    return DeepSeekClient(base_url=values["URL"], model=values["Model"], token=values["Token"])


def verify_pipeline(client: httpx.Client, database_url: str, values: list[dict]) -> None:
    """Verify the existing real workers with explicit synthetic candidate work preferences."""
    # One actual candidate establishes that the new vacancy reaches card selection.
    profile = json.loads((BASE.parent / "data/seed/candidate-pipeline.json").read_text())[0]["profile"]
    profile["work_types"] = ["remote"]
    profile["relocation_ready"] = False
    candidate_id = str(uuid4())
    CandidateStore(database_url).intake(UUID(candidate_id), CandidateProfile.model_validate(profile))
    candidate_worker = CandidateWorker(CandidateStore(database_url))
    assert candidate_worker.process_once()
    del candidate_worker
    gc.collect()
    vacancy_worker = VacancyWorker(VacancyStore(database_url))
    for _ in range(20):
        if not vacancy_worker.process_once():
            break
    combine_worker = CombineWorker(CombineStore(database_url))
    for _ in range(20):
        combine_worker.process_once()
    matching_headers = {"X-Internal-Token": os.environ["CANDIDATE_PIPELINE_TOKEN"],
                        "X-Owner-ID": str(client.headers["X-Owner-ID"])}
    for value in values:
        ready = client.get("/internal/vacancies/" + value["vacancy_id"] + "/ready", headers=matching_headers)
        ready.raise_for_status()
        assert len(ready.json()["strict_vector"]) == 37
        if ready.json()["semantic_available"]:
            assert len(ready.json()["semantic_vector"]) == 640
        selections = client.get("/internal/vacancies/" + value["vacancy_id"] + "/selections", headers=matching_headers)
        selections.raise_for_status()
        assert candidate_id in {item["candidate_id"] for item in selections.json()["items"]}


def main() -> None:
    """Run actual providers and assert publication/ready/selection states independently."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-file", type=Path)
    parser.add_argument("--resume-report", type=Path)
    args = parser.parse_args()
    url = os.getenv("AGENTS_ACCEPTANCE_URL", "http://127.0.0.1:39080")
    database_url = os.environ["AGENTS_ACCEPTANCE_DATABASE_URL"]
    assert urlparse(url).hostname in ("localhost", "127.0.0.1")
    assert "acceptance" in urlparse(database_url).path, "dedicated acceptance DB required"
    if args.resume_report:
        report = json.loads(args.resume_report.read_text(encoding="utf-8"))
        headers = {"X-Internal-Token": os.environ["AGENTS_SERVICE_TOKEN"], "X-Owner-ID": report["owner_id"]}
        with httpx.Client(base_url=url, headers=headers, timeout=15) as client:
            values = []
            for vacancy_id in report["vacancies"]:
                response = client.get("/internal/vacancy-drafts/" + vacancy_id)
                response.raise_for_status()
                values.append(response.json())
            verify_pipeline(client, database_url, values)
        report["checks"] = list(dict.fromkeys([*report["checks"], "real Harrier 640D/vacancy.ready/combine/cards"]))
        report["complete"] = True
        args.resume_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["checks"], "vacancies": len(report["vacancies"]), "report": str(args.resume_report)}, ensure_ascii=False))
        return
    owner = str(uuid4())
    store = Store(database_url)
    worker = Worker(store, deepseek=deepseek_from_file(args.provider_file) if args.provider_file else None)
    headers = {"X-Internal-Token": os.environ["AGENTS_SERVICE_TOKEN"], "X-Owner-ID": owner}
    report = {"synthetic": True, "owner_id": owner, "checks": []}
    with httpx.Client(base_url=url, headers=headers, timeout=15) as client:
        def call(method, path, body=None):
            response = client.request(method, path, **({"json": body} if body is not None else {}))
            response.raise_for_status()
            return response.json()

        def draft(value):
            return call("GET", "/internal/vacancy-drafts/" + value["draft_id"])

        def process(kind, job_id):
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                worker.process_once(kind)
                value = call("GET", "/internal/agents/jobs/" + job_id)
                if value["state"] == "succeeded":
                    return value
                assert value["state"] != "failed", value["error"]
                time.sleep(.2)
            raise AssertionError("bounded worker acceptance timeout")

        def create(value):
            path = "/internal/vacancy-drafts/" + value["draft_id"]
            summary = call("POST", path + "/approve", {"expected_revision": value["revision"]})
            assert summary["state"] == "summary"
            assert summary["publication"] is None
            request = {"expected_revision": summary["revision"], "summary_hash": summary["summary"]["summary_hash"]}
            first = call("POST", path + "/create", request)
            second = call("POST", path + "/create", request)
            assert first["publication"]["job_id"] == second["publication"]["job_id"]
            assert worker.process_once("publication")
            result = draft(value)
            assert result["state"] == "created"
            assert result["publication"]["status"] == "accepted"
            return result

        fields = {"title": "Python backend", "zp_min": 100000, "zp_max": 200000,
                  "daily": "5/2", "city": "Москва", "type": "remote", "class": "backend"}
        manual = call("POST", "/internal/vacancy-drafts", {"fields": fields})
        path = "/internal/vacancy-drafts/" + manual["draft_id"]
        old_summary = call("POST", path + "/approve", {"expected_revision": 1})
        edited = call("PATCH", path, {"expected_revision": 1, "fields": {"stacks": {"python": 0}}})
        assert edited["summary"] is None and edited["state"] == "editing"
        assert client.post(path + "/create", json={"expected_revision": 1,
            "summary_hash": old_summary["summary"]["summary_hash"]}).status_code == 409
        assert client.get(path, headers={**headers, "X-Owner-ID": str(uuid4())}).status_code == 404
        manual = create(edited)
        report["checks"].append("manual CAS/summary/edit/Create/idempotency/owner")

        form = call("POST", "/internal/vacancy-drafts", {"fields": {**fields,
            "bio": "Разрабатываем REST API на Python и PostgreSQL. Python уровень 5, SQL уровень 4; работа в команде уровень 3.",
            "stacks": {"python": 5}}})
        enrich = call("POST", "/internal/vacancy-drafts/" + form["draft_id"] + "/enrich", {"expected_revision": 1})
        assert worker.process_once("enrichment")
        job = call("GET", "/internal/agents/jobs/" + enrich["job_id"])
        assert job["state"] == "succeeded", job["error"]
        form = draft(form)
        assert form["fields"]["stacks"] == {"python": 5}, "manual group must survive Laya"
        assert job["result"]["provenance"]["model"] == "aac6fef/laya-multilingual-mlx"
        form = create(form)
        report["checks"].append("real Laya all coordinates/provenance/manual override")

        chat = call("POST", "/internal/vacancy-drafts", {"fields": {}})
        session = call("POST", "/internal/agents/sessions", {"draft_id": chat["draft_id"]})
        session_path = "/internal/agents/sessions/" + session["session_id"]
        request = {"expected_revision": 1, "idempotency_key": "synthetic-fill", "message":
            "Заполни вакансию без дополнительных вопросов: название Python backend, класс backend, Москва, удалённо, "
            "график 5/2, зарплата от 100000 до 200000 рублей. Описание: Разработка REST API на Python и PostgreSQL. "
            "Python 5, SQL 4, teamwork 3. Используй актуальные ключи реестра и затем approve для показа summary."}
        turn = call("POST", session_path + "/turns", request)
        process("agent_turn", turn["job_id"])
        state = call("GET", session_path)
        assert state["turn"]["status"] == "succeeded", state["turn"]["error_code"]
        chat = draft(chat)
        assert chat["state"] == "summary", "agent must approve via tool, not merely claim it"
        assert chat["publication"] is None, "agent must not publish"
        assert chat["fields"]["stacks"].get("python") == 5
        duplicate = call("POST", session_path + "/turns", request)
        assert duplicate["job_id"] == turn["job_id"]
        request = {"expected_revision": chat["revision"], "idempotency_key": "synthetic-edit", "message":
            "Измени зарплату на 120000–220000 рублей и снова approve, остальные поля сохрани."}
        edit_job = call("POST", session_path + "/turns", request)
        process("agent_turn", edit_job["job_id"])
        chat = draft(chat)
        assert chat["state"] == "summary" and chat["fields"]["zp_min"] == 120000
        chat = create(chat)
        report["checks"].append("real DeepSeek direct tools/fill/approve/edit/no auto publication")

        report["vacancies"] = [value["vacancy_id"] for value in (manual, form, chat)]
        (BASE / "artifacts/vacancy-creation-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_pipeline(client, database_url, [manual, form, chat])
        report["checks"].append("real Harrier 640D/vacancy.ready/combine/cards")
        report["vacancies"] = [value["vacancy_id"] for value in (manual, form, chat)]
    report["complete"] = True
    target = BASE / "artifacts/vacancy-creation-acceptance.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["checks"], "vacancies": len(report["vacancies"]), "report": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
