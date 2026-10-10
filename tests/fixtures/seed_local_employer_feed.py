"""Explicit UI-only fixture seed for the dedicated local employer database.

Run inside comit-employer-local-matching with COMIT_LOCAL_UI_FIXTURES=1 and
DATABASE_URL targeting the dedicated PostgreSQL port 41033. No network inference
is performed. UUIDs, names, descriptions and semantic artifacts are synthetic;
the normal strict selection, fallback rerank, PDF and feed storage are exercised.
--clear withdraws only this seed's candidates, retaining likes/history/processes.
"""

from datetime import UTC, datetime
import os
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from matching_engine.combine_models import CombineRequest
from matching_engine.combine_store import CombineStore
from matching_engine.combine_worker import CombineWorker
from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.feed_models import FeedRequest
from matching_engine.feed_store import FeedStore
from matching_engine.models import CandidateProfile, category_for, confirmed_strength
from matching_engine.rerank_store import RerankStore
from matching_engine.rerank_worker import RerankWorker
from matching_engine.resume import render_resume
from matching_engine.showcase_projection import public_profile
from matching_engine.store import Job, Store
from matching_engine.strict import build_strict_vector
from matching_engine.strict_evidence import build_strict_evidence

MARKER = "comit-local-employer-ui-v1"
NAMESPACE = UUID("36f7a908-960d-4e6c-a0f8-73f9a4a96442")
NAMES = ["Анна — UI-тест", "Михаил — UI-тест", "Александра — UI-тест",
         "Константин Александрович — UI-тест", "Ли — UI-тест", "Мария — UI-тест",
         "Иван — UI-тест", "Екатерина — UI-тест"]
CITIES = ["Москва", "Казань", "Санкт-Петербург", "Нижний Новгород",
          "Екатеринбург", "Новосибирск", "Томск", "Владивосток"]


def profile_for(vacancy: dict, index: int, candidate_id: UUID) -> CandidateProfile:
    """Build explicit fictitious data compatible with this local vacancy's filters."""
    low, high = vacancy["zp_min"], vacancy["zp_max"]
    center = low + (high - low) * index // max(1, len(NAMES) - 1)
    now = datetime.now(UTC)
    return CandidateProfile.model_validate({
        "source_revision": 1, "display_name": NAMES[index],
        "email": f"ui-{candidate_id}@example.invalid", "email_verified": True,
        "city": CITIES[index] if vacancy["type"] == "remote" else vacancy["city"],
        "salary": {"from_rub": max(low, center - 10000), "to_rub": min(high, center + 20000)},
        "primary_direction": vacancy["class"] or "backend",
        "work_types": [vacancy["type"]], "relocation_ready": False,
        "grade_result": {"grade": ["junior", "middle", "senior", "middle"][index % 4], "status": "self_reported"},
        "consents": {"processing": True, "pool": True, "accepted_at": now,
                     "version": "local-ui-fixture", "training": False},
        "domains": [{"key": key, "level": max(level, 4 + index % 4), "status": "self_reported"}
                    for key, level in vacancy["domens"].items()],
        "stacks": [{"key": key, "level": max(level, 4 + index % 4), "status": "self_reported"}
                   for key, level in vacancy["stacks"].items()],
        "soft_skills": [{"key": key, "level": max(level, 4 + index % 4), "status": "self_reported"}
                        for key, level in vacancy["soft_skills"].items()],
        "description": "Синтетическое резюме для проверки интерфейса. Не реальный кандидат. "
                       + ("Разрабатываю приложения, покрываю сложные сценарии и обсуждаю решения с командой. " * (index + 1)),
        "projects": [{"title": f"Учебный проект {index + 1} — UI-тест",
                      "description": "Вымышленное приложение для проверки длинного текста в резюме. " * (index + 1),
                      "status": "self_reported", "details": {"my_role": "Разработчик учебного проекта"}}],
        "preferences": ["Только синтетические данные для проверки интерфейса"],
        "metadata": {"fixture": MARKER, "fixture_owner": str(vacancy["owner_id"]),
                     "fixture_vacancy": str(vacancy["vacancy_id"]), "ui_only": True},
    })


def seed_candidate(store: Store, vacancy: dict, index: int) -> UUID:
    """Publish one namespaced fixture with a real PDF and explicitly fake semantic space."""
    candidate_id = uuid5(NAMESPACE, f"{vacancy['vacancy_id']}:{index}")
    with connect(store.database_url) as connection:
        existing = connection.execute("SELECT profile,state,revision FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()
    if existing is not None:
        if existing["profile"].get("metadata", {}).get("fixture") != MARKER:
            raise RuntimeError("fixture UUID collision")
        if existing["state"] == "ready":
            return candidate_id
        profile = CandidateProfile.model_validate(existing["profile"])
        revision = existing["revision"]
        if not profile.consents.processing or not profile.consents.pool:
            profile.source_revision += 1
            profile.consents.processing = True
            profile.consents.pool = True
            profile.consents.accepted_at = datetime.now(UTC)
            status = store.intake(candidate_id, profile)
            revision = status.profile_revision
    else:
        profile = profile_for(vacancy, index, candidate_id)
        status = store.intake(candidate_id, profile)
        if status.state != "queued":
            raise RuntimeError("fixture did not satisfy candidate readiness")
        with connect(store.database_url) as connection:
            revision = connection.execute("SELECT revision FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()["revision"]
    token = uuid4()
    schema = store.schema()
    with connect(store.database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
        row = connection.execute("""UPDATE candidate_pipeline.jobs SET state='processing',lease_token=%s,
            lease_until=now()+interval '5 minutes',attempts=attempts+1 WHERE candidate_id=%s
            AND revision=%s AND schema_version=%s AND state='queued' RETURNING id,attempts""",
            (token, candidate_id, revision, schema.version)).fetchone()
        if row is None:
            raise RuntimeError("fixture candidate was concurrently claimed")
        connection.execute("UPDATE candidate_pipeline.candidates SET state='processing' WHERE candidate_id=%s", (candidate_id,))
    category = category_for(profile)
    job = Job(row["id"], candidate_id, revision, schema, profile, token, row["attempts"])
    projection = public_profile(profile, candidate_id)
    projection["display_name"] = profile.display_name
    if not store.publish(job, category, confirmed_strength(profile), build_strict_vector(profile, schema),
                         [1.0] + [0.0] * 639, f"{MARKER}:no-inference", projection,
                         render_resume(profile, category, True), render_resume(profile, category, False),
                         build_strict_evidence(profile, schema)):
        raise RuntimeError("fixture publication raced with a source change")
    return candidate_id


def main() -> None:
    """Require explicit local opt-in and one published-vacancy owner before any writes."""
    database_url = os.environ.get("DATABASE_URL", "")
    address = urlsplit(database_url)
    if (os.environ.get("COMIT_LOCAL_UI_FIXTURES") != "1" or address.hostname not in
            ("127.0.0.1", "localhost", "host.docker.internal") or address.port != 41033):
        raise RuntimeError("requires explicitly opted-in dedicated local employer PostgreSQL")
    store = Store(database_url)
    if "--clear" in sys.argv:
        with connect(database_url) as connection:
            rows = connection.execute("SELECT candidate_id,profile FROM candidate_pipeline.candidates WHERE profile->'metadata'->>'fixture'=%s", (MARKER,)).fetchall()
        for row in rows:
            profile = CandidateProfile.model_validate(row["profile"])
            profile.source_revision += 1
            profile.consents.processing = False
            profile.consents.pool = False
            store.intake(row["candidate_id"], profile)
        print(f"Withdrawn {len(rows)} UI fixtures; existing likes and receipts retained")
        return
    with connect(database_url) as connection:
        rows = connection.execute("SELECT vacancy_id,owner_id,profile FROM vacancy_pipeline.vacancies WHERE business_status='open' AND state='ready' ORDER BY vacancy_id").fetchall()
        if not rows or len({row["owner_id"] for row in rows}) != 1:
            raise RuntimeError("requires a unique owner of local published vacancies")
        if not connection.execute("SELECT 1 FROM sessions WHERE account_id=%s AND role='employer' AND revoked_at IS NULL AND expires_at>now() LIMIT 1", (rows[0]["owner_id"],)).fetchone():
            raise RuntimeError("published-vacancy owner has no active local employer session")
    vacancy_ids = []
    for row in rows:
        vacancy = {**row["profile"], "vacancy_id": row["vacancy_id"], "owner_id": row["owner_id"]}
        for index in range(len(NAMES)):
            seed_candidate(store, vacancy, index)
        vacancy_ids.append(row["vacancy_id"])
    combines = CombineStore(database_url)
    combines.process_events()
    for vacancy_id in vacancy_ids:
        combines.request(vacancy_id, CombineRequest(top_k=50))
    combine_worker = CombineWorker(combines)
    for _ in range(30):
        if not combine_worker.process_once():
            break
    rerank_worker = RerankWorker(RerankStore(database_url))
    for _ in range(30):
        if not rerank_worker.process_once():
            break
    feeds = FeedStore(database_url)
    for row in rows:
        page = feeds.open(row["owner_id"], FeedRequest(include_reserve=True, limit=50), row["vacancy_id"])
        print(f"Vacancy {row['vacancy_id']}: {len(page.items)} stored UI candidate cards; synthetic semantic artifacts")


if __name__ == "__main__":
    main()
