"""Operator-only deterministic demo seed. Dry-run is the default; existing records are never rewritten."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from catalog import VERSION, build_catalog

ROOT = Path(__file__).resolve().parents[2]


def validate_target(database_url: str, target: str) -> None:
    """Refuse remote or unnamed databases and accidental reuse of the production target."""
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}:
        raise ValueError("demo seed requires a local PostgreSQL target")
    if not target.startswith("comit_demo_") or parsed.path != "/" + target or not target.replace("_", "").isalnum():
        raise ValueError("--target must exactly match a dedicated comit_demo_* database")


def seed_vacancies(database_url: str, catalog: dict) -> list[str]:
    """Use actual editorial create/approve/submit; a per-catalog DB lock makes retry IDs stable."""
    from agents_service.database import connect
    from agents_service.models import SchemaSnapshot
    from agents_service.store import Store
    from matching_engine.store import Store as MatchingStore
    store = Store(database_url)
    schema = SchemaSnapshot.model_validate(MatchingStore(database_url).schema().model_dump())
    ids = []
    for item in catalog["vacancies"]:
        with connect(database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(813726404)")
            previous = connection.execute("SELECT draft_id FROM agents_service.drafts WHERE owner_id=%s AND fields->'metadata'->>'demo_catalog'=%s AND fields->'metadata'->>'demo_key'=%s", (item["owner_id"], VERSION, item["key"])).fetchall()
            if len(previous) > 1:
                raise RuntimeError("demo vacancy key collision")
            if previous:
                draft = store.get(item["owner_id"], previous[0]["draft_id"])
            else:
                draft = store.create_in_connection(connection, item["owner_id"], item["fields"], schema)
            if draft.state == "editing" and draft.revision == 1 and draft.publication is None:
                draft = store.approve_in_connection(connection, item["owner_id"], draft.draft_id, draft.revision, schema)
            if draft.state == "summary" and draft.summary:
                draft = store.submit_in_connection(connection, item["owner_id"], draft.draft_id,
                    {"expected_revision": draft.revision, "summary_hash": draft.summary.summary_hash}, schema)
            ids.append(str(draft.draft_id))
    return ids


def process_pipeline(database_url: str, count: int) -> None:
    """Drain the real local transports and Harrier workers; unavailable models remain errors, never fake vectors."""
    from agents_service.store import Store as AgentsStore
    from agents_service.worker import Worker as AgentsWorker
    from matching_engine.embeddings import HarrierEncoder
    from matching_engine.store import Store
    from matching_engine.vacancy_store import VacancyStore
    from matching_engine.vacancy_worker import VacancyWorker
    from matching_engine.worker import Worker
    endpoint = urlsplit(os.environ.get("MATCHING_ENGINE_URL", ""))
    if endpoint.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"} or endpoint.scheme != "http" or not os.environ.get("CANDIDATE_PIPELINE_TOKEN"):
        raise ValueError("--process requires a local MATCHING_ENGINE_URL and configured internal token")
    if not HarrierEncoder().is_available:
        raise RuntimeError("real pinned Harrier runtime is unavailable; queued records were preserved")
    subprocess.run(["pnpm", "--filter", "@comit/api", "exec", "tsx", "scripts/seed-demo-profiles.ts", "--project", str(count)], cwd=ROOT, check=True, timeout=120)
    agents = AgentsWorker(AgentsStore(database_url))
    for _ in range(count):
        if not agents.process_once(kind="publication"):
            break
    for worker in (Worker(Store(database_url)), VacancyWorker(VacancyStore(database_url))):
        for _ in range(count):
            if not worker.process_once():
                break


def status(database_url: str, catalog: dict, vacancy_ids: list[str]) -> dict:
    """Count actual source, delivery and ready-pool rows without claiming queued documents are published."""
    from agents_service.database import connect
    with connect(database_url) as connection:
        candidates = connection.execute("SELECT state,count(*) AS count FROM candidate_pipeline.candidates WHERE candidate_id=ANY(%s::uuid[]) GROUP BY state", ([item["id"] for item in catalog["candidates"]],)).fetchall()
        vacancies = connection.execute("SELECT state,count(*) AS count FROM vacancy_pipeline.vacancies WHERE vacancy_id=ANY(%s::uuid[]) GROUP BY state", (vacancy_ids,)).fetchall()
        pool = connection.execute("SELECT count(*) AS count FROM candidate_pipeline.pool WHERE candidate_id=ANY(%s::uuid[])", ([item["id"] for item in catalog["candidates"]],)).fetchone()["count"]
    return {"candidate_pipeline": {item["state"]: item["count"] for item in candidates}, "vacancy_pipeline": {item["state"]: item["count"] for item in vacancies}, "published_candidates": pool}


def main() -> None:
    """Preview counts offline, or explicitly write only a named isolated demo database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--target", default="")
    parser.add_argument("--process", action="store_true")
    parser.add_argument("--sample", type=int, default=240, help="Candidates to seed (1..240), with proportional vacancies")
    args = parser.parse_args()
    if not 1 <= args.sample <= 240:
        parser.error("--sample must be 1..240")
    catalog = build_catalog()
    catalog["candidates"] = catalog["candidates"][:args.sample]
    catalog["vacancies"] = catalog["vacancies"][:max(1, (args.sample + 4) // 5)]
    preview = {"version": VERSION, "synthetic": True, "candidates": len(catalog["candidates"]), "employers": len(catalog["employers"]), "vacancies": len(catalog["vacancies"])}
    if not args.apply:
        print(json.dumps({**preview, "mode": "dry-run", "writes": 0}, ensure_ascii=False))
        return
    database_url = os.environ.get("DATABASE_URL", "")
    validate_target(database_url, args.target)
    os.environ["COMIT_DEMO_TARGET"] = args.target
    env = dict(os.environ)
    subprocess.run(["pnpm", "--filter", "@comit/api", "exec", "tsx", "scripts/seed-demo-profiles.ts"], input=json.dumps(catalog), text=True, cwd=ROOT, env=env, check=True, timeout=120)
    vacancy_ids = seed_vacancies(database_url, catalog)
    if args.process:
        process_pipeline(database_url, len(catalog["candidates"]) + len(catalog["vacancies"]) + 10)
    receipt = status(database_url, catalog, vacancy_ids)
    print(json.dumps({**preview, "mode": "applied", **receipt}, ensure_ascii=False))
    if args.process and (receipt["published_candidates"] != len(catalog["candidates"]) or receipt["vacancy_pipeline"].get("ready", 0) != len(vacancy_ids)):
        raise RuntimeError("some demo records are not ready; inspect durable pipeline errors")


if __name__ == "__main__":
    main()
