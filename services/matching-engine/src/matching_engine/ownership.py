"""Optional employer context is meaningful only behind the existing service-token boundary."""

from uuid import UUID

from matching_engine.database import connect
from matching_engine.store import NotFoundError


def assert_vacancy_owner(database_url: str, vacancy_id: UUID, owner_id: UUID) -> None:
    """Return uniform not-found for absent/foreign vacancies, without revealing another owner."""
    with connect(database_url) as connection:
        row = connection.execute(
            "SELECT owner_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s", (vacancy_id,)
        ).fetchone()
        if row is None or row["owner_id"] != owner_id:
            raise NotFoundError("owner_record_not_found")
