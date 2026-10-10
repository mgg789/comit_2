"""Real PostgreSQL compatibility for new required cities and explicit work preferences."""

import hashlib
import json
from importlib.resources import files
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_contracts import eligible_profile
from test_pipeline_integration import store as store  # noqa: PLC0414
from test_vacancy_contracts import eligible_vacancy
from test_vacancy_integration import run

from matching_engine.database import connect
from matching_engine.models import CandidateProfile
from matching_engine.vacancy_models import VacancyInput
from matching_engine.vacancy_store import VacancyStore

pytestmark = pytest.mark.integration


def legacy_hash(profile: dict) -> str:
    """Reproduce the canonical snapshot hash written before additive contract fields."""
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_old_cityless_ready_vacancy_is_withdrawn_without_losing_raw_snapshot(store) -> None:
    """Apply the actual upgrade SQL to a completed legacy snapshot and its live job."""
    vacancies = VacancyStore(store.database_url)
    vacancy_id = uuid4()
    vacancies.intake(vacancy_id, eligible_vacancy())
    run(vacancies).process_once()
    with connect(store.database_url) as connection:
        connection.execute("""UPDATE vacancy_pipeline.vacancies SET profile=profile-'city',raw_input=raw_input-'city'
                              WHERE vacancy_id=%s""", (vacancy_id,))
        old = connection.execute("SELECT profile FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                 (vacancy_id,)).fetchone()["profile"]
        connection.execute("UPDATE vacancy_pipeline.vacancies SET profile_hash=%s WHERE vacancy_id=%s",
                           (legacy_hash(old), vacancy_id))
        sql = files("matching_engine").joinpath("migrations/003_vacancy_city_required.sql").read_text()
        connection.execute(sql)
    status = vacancies.status(vacancy_id)
    assert status.state == "draft" and status.missing_requirements == ["city"]
    assert vacancies.page(100, 0).items == []
    with connect(store.database_url) as connection:
        event = connection.execute("SELECT event_type,payload FROM vacancy_pipeline.outbox ORDER BY id DESC LIMIT 1").fetchone()
        assert event["event_type"] == "vacancy.withdrawn" and event["payload"]["reason"] == "city_required"
        raw = connection.execute("SELECT raw_input FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s",
                                 (vacancy_id,)).fetchone()["raw_input"]
        assert "city" not in raw
    assert vacancies.intake(vacancy_id, VacancyInput.model_validate(old)).revision == 1


def test_legacy_candidate_replay_accepts_new_unknown_preference_defaults(store) -> None:
    """Adding optional typed preferences cannot break same-source-revision replay."""
    candidate_id, profile = uuid4(), eligible_profile()
    store.intake(candidate_id, profile)
    old = profile.model_dump(mode="json")
    for key in ("work_types", "relocation_ready", "relocation_cities"):
        old.pop(key)
    with connect(store.database_url) as connection:
        connection.execute("UPDATE candidate_pipeline.candidates SET profile=%s,profile_hash=%s WHERE candidate_id=%s",
                           (Jsonb(old), legacy_hash(old), candidate_id))
    assert store.intake(candidate_id, CandidateProfile.model_validate(old)).profile_revision == 1
