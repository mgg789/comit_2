"""Private decision instrumentation; only thresholded counts cross the API boundary."""

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from matching_engine.combine_scoring import strict_filter
from matching_engine.database import connect
from matching_engine.store import Store

if TYPE_CHECKING:
    from matching_engine.combine_models import MainSelectionResult
    from matching_engine.combine_store import CombineJob
    from matching_engine.rerank_model import RerankOutcome
    from matching_engine.rerank_store import RerankJob


class EvidenceCount(BaseModel):
    """A category observed in at least four distinct vacancies, with no source IDs."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["matching"] = "matching"
    category: str
    count: int = Field(ge=4, le=1000000)


class EvidenceResponse(BaseModel):
    """Only current consented candidate's bounded, aggregate matching evidence."""

    evidence: list[EvidenceCount]


def consenting(connection: psycopg.Connection, candidate_id: UUID) -> bool:
    """Check current matching consent and, when present, canonical API consent."""
    if connection.execute("SELECT to_regclass('public.candidate_profiles') AS present").fetchone()["present"]:
        source = connection.execute("""SELECT 1 FROM public.candidate_profiles WHERE candidate_id=%s
            AND processing_consent AND pool_consent AND training_consent AND training_consent_version='training-v2' FOR SHARE""", (candidate_id,)).fetchone()
        if source is None:
            return False
    row = connection.execute("""SELECT 1 FROM candidate_pipeline.candidates
        WHERE candidate_id=%s AND (profile->'consents'->>'processing')::boolean IS TRUE
        AND (profile->'consents'->>'pool')::boolean IS TRUE
        AND (profile->'consents'->>'training')::boolean IS TRUE FOR SHARE""", (candidate_id,)).fetchone()
    return row is not None


def capture_main(connection: psycopg.Connection, job: "CombineJob", result: "MainSelectionResult") -> None:
    """Record real strict reasons and eligible tail at successful publication only."""
    selected = {card.candidate_id for card in result.items}
    for candidate in job.candidates:
        if not consenting(connection, candidate.candidate_id):
            continue
        decision = strict_filter(job.vacancy, candidate, excluded=candidate.candidate_id in job.excluded_ids)
        categories = decision.reasons or (["main_top_k_omission"] if candidate.candidate_id not in selected else [])
        for category in categories:
            connection.execute("""INSERT INTO recommendation_evidence.observations
                (candidate_id,vacancy_id,run_id,category) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (candidate.candidate_id, job.vacancy.vacancy_id, job.run_id, category))
    connection.execute("DELETE FROM recommendation_evidence.observations WHERE recorded_at<now()-interval '90 days'")


def capture_rerank(connection: psycopg.Connection, job: "RerankJob", outcome: "RerankOutcome") -> None:
    """Reserve is ranking context; fallback is explicitly a system condition."""
    # Only a successful rerank proves the eligible main tail was not evaluated by reranker.
    tail = connection.execute("""SELECT o.candidate_id FROM recommendation_evidence.observations o
        JOIN rerank_pipeline.runs r ON r.main_run_id=o.run_id
        WHERE r.run_id=%s AND o.category='main_top_k_omission'""", (job.run_id,)).fetchall()
    for observation in tail:
        candidate_id = observation["candidate_id"]
        if consenting(connection, candidate_id):
            connection.execute("""INSERT INTO recommendation_evidence.observations
                (candidate_id,vacancy_id,run_id,category) VALUES(%s,%s,%s,'unranked_by_reranker') ON CONFLICT DO NOTHING""",
                (candidate_id, job.vacancy.vacancy_id, job.run_id))
    for item in outcome.items:
        category = "system_fallback" if outcome.mode == "main_fallback" else ("reranker_reserve" if item.kind == "reserve" else None)
        if category is not None and consenting(connection, item.candidate_id):
            connection.execute("""INSERT INTO recommendation_evidence.observations
                (candidate_id,vacancy_id,run_id,category) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (item.candidate_id, job.vacancy.vacancy_id, job.run_id, category))


def withdraw(connection: psycopg.Connection, candidate_id: UUID) -> None:
    """Erase candidate-associated decision observations on consent withdrawal."""
    connection.execute("DELETE FROM recommendation_evidence.observations WHERE candidate_id=%s", (candidate_id,))


def recommendation_router(repository: Store | None, require_internal: Callable[..., Store]) -> APIRouter:
    """Expose internal counts to a trusted gateway asserting the same candidate owner."""
    router = APIRouter()

    @router.get("/internal/candidates/{candidate_id}/recommendation-evidence", response_model=EvidenceResponse,
                dependencies=[Depends(require_internal)])
    def evidence(candidate_id: UUID, after: datetime, before: datetime,
                 x_owner_id: Annotated[str | None, Header()] = None) -> EvidenceResponse:
        if x_owner_id != str(candidate_id):
            raise HTTPException(404, "candidate_evidence_not_found")
        if after.tzinfo is None or before.tzinfo is None or not 0 < (before-after).total_seconds() <= 90*86400:
            raise HTTPException(422, "invalid_evidence_period")
        if repository is None:
            raise HTTPException(503, "matching_database_unavailable")
        with connect(repository.database_url) as connection:
            if not consenting(connection, candidate_id):
                withdraw(connection, candidate_id)
                return EvidenceResponse(evidence=[])
            rows = connection.execute("""SELECT category,count(DISTINCT vacancy_id) AS count
                FROM recommendation_evidence.observations WHERE candidate_id=%s AND recorded_at>=%s AND recorded_at<%s
                GROUP BY category HAVING count(DISTINCT vacancy_id)>3 ORDER BY category""",
                (candidate_id, after, before)).fetchall()
            return EvidenceResponse(evidence=[EvidenceCount(**row) for row in rows])

    return router
