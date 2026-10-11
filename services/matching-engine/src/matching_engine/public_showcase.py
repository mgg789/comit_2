"""Read-only global catalogue projections; no employer identity or detail capability."""
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from matching_engine.database import connect
from matching_engine.models import Category, Contract, Direction, Grade, SalaryRange
from matching_engine.page_cursor import decode_cursor, encode_cursor
from matching_engine.showcase_models import SearchRequest
from matching_engine.showcase_store import _SOURCE, _filter_sql
from matching_engine.vacancy_models import VacancyReadyPage
from matching_engine.vacancy_store import VacancyStore

# Search exactly card-visible values. Full public_profile includes employer-only
# biography, projects and verification data that must not become a search oracle.
_CATALOGUE_FTS = """to_tsvector('russian', concat_ws(' ',
    public_profile->>'display_name', public_profile->>'city', specialization, grade,
    (SELECT string_agg(skill->>'key', ' ') FROM jsonb_array_elements(
        COALESCE(public_profile->'stacks','[]'::jsonb)) skill
        WHERE skill->>'status' IS DISTINCT FROM 'rejected')))"""


class CatalogueCandidate(Contract):
    """Minimal trusted projection plus revision for the API source-consent recheck."""
    candidate_id: UUID
    profile_revision: int = Field(ge=1)
    display_name: str
    city: str
    salary: SalaryRange
    category: Category
    stacks: list[str]
    published_at: datetime


class CatalogueCandidatePage(Contract):
    """Bounded snapshot-bound page without protected PDF or private profile fields."""
    items: list[CatalogueCandidate]
    limit: int
    has_more: bool
    next_cursor: str | None = None


class CatalogueQuery(Contract):
    """Read-only exact classification/skill filters and publication-date ordering."""
    q: str = Field(default="", max_length=500)
    specialization: Direction | None = None
    grade: Grade | None = None
    stack: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    sort: Literal["newest", "oldest"] = "newest"
    limit: int = Field(default=24, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=1024)


class PublicShowcaseStore:
    """Reuse eligible pool sources in read-only repeatable snapshots, without LLM/model work."""
    def __init__(self, database_url: str) -> None:
        """Use the existing matching PostgreSQL projection without loading an encoder."""
        self.database_url = database_url

    def page(self, query: CatalogueQuery, *, candidates: bool) -> CatalogueCandidatePage | VacancyReadyPage:
        """Page only current consented candidates or current open matching vacancies."""
        if "\x00" in query.q:
            raise HTTPException(400, "query must contain no NUL")
        query_text = query.q.strip()
        params: list = []
        if candidates:
            source = _SOURCE
            filters: dict = {"classes": [query.specialization] if query.specialization else [],
                             "grades": [query.grade] if query.grade else []}
            if query.stack:
                filters["groups"] = {"stacks": [{"key": query.stack}]}
            where, params = _filter_sql(SearchRequest(filters=filters))
            if query_text:
                where += f" AND {_CATALOGUE_FTS} @@ websearch_to_tsquery('russian',%s)"
                params.append(query_text)
            id_column = "candidate_id"
        else:
            source = """SELECT r.*,v.profile,r.semantic_vector::text AS vector
              FROM vacancy_pipeline.ready r JOIN vacancy_pipeline.vacancies v
              ON v.vacancy_id=r.vacancy_id AND v.revision=r.revision
              AND v.state='ready' AND v.business_status='open'
              JOIN candidate_pipeline.settings s ON s.active_schema_version=r.schema_version"""
            clauses = ["true"]
            for value, expression in ((query.specialization, "profile->>'class'"),
                                      (query.grade, "profile->'metadata'->>'grade'")):
                if value:
                    clauses.append(f"{expression}=%s")
                    params.append(value)
            if query.stack:
                clauses.append("profile->'stacks' ? %s")
                params.append(query.stack)
            if query_text:
                clauses.append("concat_ws(' ',profile->'metadata'->>'title',profile->'metadata'->>'company_name',profile->>'city') ILIKE %s ESCAPE '\\'")
                params.append("%" + query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            where = " AND ".join(clauses)
            id_column = "vacancy_id"
        scope = "catalogue-candidates" if candidates else "catalogue-vacancies"
        with connect(self.database_url) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout='5s'")
            fingerprint = connection.execute(f"""WITH source AS ({source}) SELECT
                COALESCE(md5(string_agg({id_column}::text||':'||revision::text||':'||published_at::text,',' ORDER BY {id_column})),md5('')) AS value
                FROM source""").fetchone()["value"]
            snapshot = hashlib.sha256(json.dumps({"source": fingerprint, "query": query.model_dump(exclude={"cursor", "limit"})}, sort_keys=True).encode()).hexdigest()
            key = decode_cursor(query.cursor, scope, snapshot)
            if key is not None:
                try:
                    if len(key) != 2 or not isinstance(key[0], str):
                        raise ValueError
                    datetime.fromisoformat(key[0])
                    UUID(key[1])
                except (TypeError, ValueError, AttributeError):
                    raise HTTPException(409, "catalogue cursor invalid; restart listing") from None
                where += f" AND (published_at,{id_column}) {'<' if query.sort == 'newest' else '>'} (%s::timestamptz,%s::uuid)"
                params.extend(key)
            order = "DESC" if query.sort == "newest" else "ASC"
            # Candidates never fetch vector/PDF/evidence columns for anonymous browsing.
            projection = "candidate_id,revision,specialization,grade,grade_status,public_profile,published_at" if candidates else "*"
            rows = connection.execute(f"""WITH source AS ({source}) SELECT {projection} FROM source WHERE {where}
              ORDER BY published_at {order},{id_column} {order} LIMIT %s""", (*params, query.limit + 1)).fetchall()
            has_more = len(rows) > query.limit
            rows = rows[:query.limit]
            cursor = encode_cursor(scope, snapshot, [rows[-1]["published_at"].isoformat(), str(rows[-1][id_column])]) if has_more else None
            if not candidates:
                return VacancyReadyPage(items=[VacancyStore._entry(row) for row in rows], limit=query.limit, offset=0,
                                        has_more=has_more, next_cursor=cursor)
            cards = [CatalogueCandidate(candidate_id=row["candidate_id"], profile_revision=row["revision"],
                       display_name=row["public_profile"]["display_name"], city=row["public_profile"]["city"],
                       salary=row["public_profile"]["salary"], stacks=[skill["key"] for skill in row["public_profile"].get("stacks", []) if skill.get("status") != "rejected"],
                       category={"specialization": row["specialization"], "grade": row["grade"], "grade_status": row["grade_status"]},
                       published_at=row["published_at"]) for row in rows]
            return CatalogueCandidatePage(items=cards, limit=query.limit, has_more=has_more, next_cursor=cursor)


def public_showcase_router(repository: PublicShowcaseStore | None, require_internal: Callable) -> APIRouter:
    """Token-only read routes deliberately require no asserted/fabricated owner identity."""
    router = APIRouter(prefix="/internal/public/showcase", tags=["public-showcase"])
    def storage(_authenticated: Annotated[object, Depends(require_internal)]) -> PublicShowcaseStore:
        if repository is None:
            raise HTTPException(503, "catalogue database is not configured")
        return repository
    @router.get("/candidates", response_model=CatalogueCandidatePage)
    def candidates(query: Annotated[CatalogueQuery, Query()], store: Annotated[PublicShowcaseStore, Depends(storage)]) -> CatalogueCandidatePage | VacancyReadyPage:
        """Expose anonymous consented cards only to the trusted API gateway."""
        return store.page(query, candidates=True)
    @router.get("/vacancies", response_model=VacancyReadyPage)
    def vacancies(query: Annotated[CatalogueQuery, Query()], store: Annotated[PublicShowcaseStore, Depends(storage)]) -> CatalogueCandidatePage | VacancyReadyPage:
        """Expose ready input to the gateway for an additional editorial publication check."""
        return store.page(query, candidates=False)
    return router
