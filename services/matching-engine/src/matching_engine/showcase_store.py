"""Snapshot-consistent public candidate search with lexical/Harrier RRF fusion."""
import base64
import hashlib
import json
import math
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from matching_engine.combine_scoring import strict_filter
from matching_engine.combine_store import CombineStore
from matching_engine.database import connect
from matching_engine.embeddings import (
    EMBEDDING_SPACE_ID,
    MODEL_VERSION,
    EmbeddingUnavailable,
    HarrierEncoder,
)
from matching_engine.showcase_models import (
    PublicCandidateCard,
    PublicCandidateDetail,
    PublicProfile,
    SearchFactors,
    SearchPage,
    SearchRequest,
)


class QueryEncoder(Protocol):
    """Injectable real-model interface; test encoders are restricted to fixtures."""
    model_version: str
    embedding_space_id: str

    def encode(self, text: str) -> list[float]:
        """Encode one bounded employer query in the pinned semantic space."""
        ...


_SOURCE = f"""
SELECT p.*, CASE WHEN p.semantic_model_version='{MODEL_VERSION}' THEN '{EMBEDDING_SPACE_ID}' ELSE NULL END AS semantic_space_id, row_number() OVER (PARTITION BY p.specialization
 ORDER BY p.grade_rank DESC,p.confirmed_strength DESC,p.candidate_id) AS rank
FROM candidate_pipeline.pool p
JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id AND c.revision=p.revision
JOIN candidate_pipeline.settings t ON t.active_schema_version=p.schema_version
JOIN candidate_pipeline.strict_schemas s ON s.version=t.active_schema_version AND s.schema_hash=p.schema_hash
WHERE c.state='ready' AND c.profile->'consents'->'processing'='true'::jsonb
 AND c.profile->'consents'->'pool'='true'::jsonb
"""
_FTS = "jsonb_to_tsvector('russian',public_profile,'[\"string\"]'::jsonb)"


def _filter_sql(request: SearchRequest) -> tuple[str, list]:
    """Build parameterized filters before either retrieval channel is ranked."""
    filters = request.filters
    clauses, params = [], []
    for value, column in ((filters.classes, "specialization"), (filters.grades, "grade")):
        if value:
            clauses.append(f"{column}=ANY(%s)")
            params.append(value)
    if filters.cities:
        clauses.append("lower(public_profile->>'city')=ANY(%s)")
        params.append([value.strip().lower() for value in filters.cities])
    if filters.salary is not None:
        clauses.append("(public_profile->'salary'->>'from_rub')::bigint <= %s AND ((public_profile->'salary'->>'to_rub') IS NULL OR (public_profile->'salary'->>'to_rub')::bigint >= %s)")
        params.extend([filters.salary.to_rub, filters.salary.from_rub])
    if filters.work_types:
        clauses.append("COALESCE(public_profile->'work_types','[]'::jsonb) ?| %s")
        params.append(filters.work_types)
    if filters.relocation_ready is not None:
        clauses.append("public_profile->'relocation_ready'=%s::jsonb")
        params.append(Jsonb(filters.relocation_ready))
    for group in ("domains", "stacks", "soft_skills"):
        for skill in getattr(filters.groups, group):
            clauses.append(f"EXISTS (SELECT 1 FROM jsonb_array_elements(public_profile->'{group}') skill WHERE skill->>'key'=%s AND (skill->>'level')::integer >= %s AND skill->>'status'=ANY(%s))")
            params.extend([skill.key, skill.min_level, skill.statuses])
    return " AND ".join(clauses) or "true", params


def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> dict | None:
    if cursor is None:
        return None
    try:
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(value, dict) or set(value) != {"v", "query", "snapshot", "channel", "offset"}:
            raise ValueError
        if value["v"] != 1 or type(value["offset"]) is not int or not 0 <= value["offset"] <= 2**53 - 1:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(409, "search cursor is invalid; restart search") from None


class ShowcaseStore:
    """Search only live public pool revisions; never require or create a vacancy."""

    def __init__(self, database_url: str, encoder: QueryEncoder | None = None) -> None:
        """Select a lazy pinned query encoder or an explicit fixture implementation."""
        self.database_url = database_url
        self.encoder = encoder if encoder is not None else HarrierEncoder(role="vacancy_query")

    def ready(self) -> bool:
        """Check migration 011 storage/index without loading the optional query model."""
        import psycopg
        try:
            with connect(self.database_url) as connection:
                return bool(connection.execute("""SELECT
                    to_regclass('candidate_pipeline.search_embeddings') IS NOT NULL
                    AND to_regclass('candidate_pipeline.candidate_showcase_lexical') IS NOT NULL AS ready""").fetchone()["ready"])
        except psycopg.Error:
            return False

    def _query_vector(self, query: str) -> tuple[list[float] | None, str | None]:
        identity = f"{self.encoder.model_version}:{self.encoder.embedding_space_id}"
        if self.encoder.embedding_space_id != EMBEDDING_SPACE_ID:
            return None, "embedding_space_mismatch"
        key = hashlib.sha256(f"{identity}:{query}".encode()).hexdigest()
        with connect(self.database_url) as connection:
            cached = connection.execute("""SELECT semantic_vector::text AS vector FROM candidate_pipeline.search_embeddings
                WHERE cache_key=%s AND model_version=%s AND semantic_space_id=%s
                AND created_at > now()-interval '24 hours'""", (key, self.encoder.model_version, EMBEDDING_SPACE_ID)).fetchone()
            if cached:
                return json.loads(cached["vector"]), None
        try:
            vector = self.encoder.encode(query)
            if len(vector) != 640 or not all(math.isfinite(value) for value in vector) or sum(value * value for value in vector) == 0:
                raise EmbeddingUnavailable("invalid_embedding")
        except EmbeddingUnavailable:
            return None, "harrier_unavailable"
        with connect(self.database_url) as connection:
            # Serialize cache pruning, keeping the bounded cache safe under concurrent searches.
            connection.execute("SELECT pg_advisory_xact_lock(1440130178)")
            connection.execute("DELETE FROM candidate_pipeline.search_embeddings WHERE created_at <= now()-interval '24 hours'")
            connection.execute("""INSERT INTO candidate_pipeline.search_embeddings(cache_key,model_version,semantic_space_id,semantic_vector)
                VALUES (%s,%s,%s,%s::vector) ON CONFLICT(cache_key) DO NOTHING""",
                (key, self.encoder.model_version, EMBEDDING_SPACE_ID, json.dumps(vector)))
            connection.execute("""DELETE FROM candidate_pipeline.search_embeddings WHERE cache_key IN (
                SELECT cache_key FROM candidate_pipeline.search_embeddings ORDER BY created_at DESC,cache_key OFFSET 1000)""")
            # A racing encoder may already have pinned this query; use the persisted winner.
            row = connection.execute("SELECT semantic_vector::text AS vector FROM candidate_pipeline.search_embeddings WHERE cache_key=%s", (key,)).fetchone()
            if row:
                vector = json.loads(row["vector"])
        return vector, None

    @staticmethod
    def _card(row: dict) -> PublicCandidateCard:
        profile = PublicProfile.model_validate(row["public_profile"])
        return PublicCandidateCard(candidate_id=row["candidate_id"], profile_revision=row["revision"],
            display_name=profile.display_name, city=profile.city, salary=profile.salary,
            category={"specialization": row["specialization"], "grade": row["grade"], "grade_status": row["grade_status"]},
            rank=row["rank"], confirmed_strength=row["confirmed_strength"],
            resume_url=f"/internal/pool/{row['candidate_id']}/resume.pdf?profile_revision={row['revision']}",
            search_factors=SearchFactors(lexical_rank=row.get("lexical_rank"), semantic_rank=row.get("semantic_rank"), rrf_score=row.get("rrf_score", 0)),
            external_evidence=profile.external_evidence)

    @staticmethod
    def _neighbors(connection, where: str, params: list, vector: list[float]) -> list[UUID]:
        """Retrieve bounded cosine neighbors; exact fallback fills selective filters.

        Eligibility is authoritative before retrieval. HNSW approximation affects
        only ranking recall, never consent, ownership or strict compatibility.
        """
        eligible = connection.execute(f"WITH source AS ({_SOURCE}) SELECT candidate_id FROM source WHERE {where} AND semantic_model_version=%s AND semantic_space_id=%s",
                                      (*params, MODEL_VERSION, EMBEDDING_SPACE_ID)).fetchall()
        ids = [row["candidate_id"] for row in eligible]
        if not ids:
            return []
        target = min(len(ids), 200)
        payload = json.dumps(vector)
        # Candidate-ID ANY filters distort ANN cost estimates and are checked
        # after bounded overretrieval; selective filters fall back to exact KNN.
        ordering = "(semantic_vector <=> %s::vector) + 0,candidate_id"
        if len(ids) <= 1000:
            sql = f"SELECT candidate_id FROM candidate_pipeline.pool WHERE candidate_id=ANY(%s) ORDER BY {ordering} LIMIT %s"
            rows = connection.execute(sql, (ids, payload, target)).fetchall()
        else:
            # This branch explicitly chooses approximate retrieval. PostgreSQL
            # may otherwise choose exact sorting on wide PDF-backed pool rows.
            # Scope the planner preference to this single bounded KNN statement.
            previous = connection.execute("SELECT current_setting('enable_seqscan') AS value").fetchone()["value"]
            connection.execute("SELECT set_config('enable_seqscan','off',true)")
            try:
                possible = connection.execute("SELECT candidate_id FROM candidate_pipeline.pool ORDER BY semantic_vector <=> %s::vector LIMIT 400", (payload,)).fetchall()
            finally:
                connection.execute("SELECT set_config('enable_seqscan',%s,true)", (previous,))
            allowed = set(ids)
            rows = [row for row in possible if row["candidate_id"] in allowed][:target]
        if len(rows) < target:
            rows = connection.execute("SELECT candidate_id FROM candidate_pipeline.pool WHERE candidate_id=ANY(%s) ORDER BY (semantic_vector <=> %s::vector)+0,candidate_id LIMIT %s", (ids, payload, target)).fetchall()
        return [row["candidate_id"] for row in rows]

    def search(self, owner_id: UUID, request: SearchRequest) -> SearchPage:
        """Fuse prefiltered channels in repeatable read; reject any continuation drift."""
        if "\x00" in request.q:
            raise HTTPException(400, "search query must contain no NUL")
        query = request.q.strip()
        prior = _decode_cursor(request.cursor)
        query_hash = hashlib.sha256(json.dumps({"owner": str(owner_id), "q": query, "filters": request.filters.model_dump(mode="json")}, sort_keys=True).encode()).hexdigest()
        if prior and prior["query"] != query_hash:
            raise HTTPException(409, "search query changed; restart search")
        vector, fallback = self._query_vector(query) if query else (None, None)
        where, params = _filter_sql(request)
        with connect(self.database_url) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL hnsw.ef_search = 400")
            vacancy_revision = None
            if request.filters.vacancy_id is not None:
                vacancy = CombineStore._vacancy(connection, request.filters.vacancy_id)
                if vacancy.owner_id != owner_id:
                    raise HTTPException(404, "vacancy not found")
                vacancy_revision = vacancy.source_revision
                eligible = [candidate.candidate_id for candidate in CombineStore._candidates(connection)
                            if strict_filter(vacancy, candidate).passed]
                where += " AND candidate_id=ANY(%s)"
                params.append(eligible)
            source = connection.execute(f"""WITH source AS ({_SOURCE}) SELECT
                COALESCE(md5(string_agg(candidate_id::text || ':' || revision::text || ':' ||
                  schema_hash || ':' || semantic_model_version || ':' || COALESCE(semantic_space_id,'') || ':' ||
                  published_at::text || ':' || md5(public_profile::text) || ':' || md5(semantic_vector::text), ',' ORDER BY candidate_id)),md5('')) AS fingerprint,
                EXISTS(SELECT 1 FROM source WHERE {where} AND semantic_model_version=%s AND semantic_space_id=%s) AS compatible,
                (SELECT pool_generation FROM matching_pipeline.settings WHERE singleton) AS generation,
                (SELECT s.schema_hash FROM candidate_pipeline.strict_schemas s JOIN candidate_pipeline.settings t ON t.active_schema_version=s.version) AS active_schema
                FROM source""", (*params, MODEL_VERSION, EMBEDDING_SPACE_ID)).fetchone()
            mode = "browse" if not query else "hybrid" if vector is not None and source["compatible"] else "lexical"
            if query and vector is not None and not source["compatible"]:
                fallback = "no_compatible_embeddings"
            channel = hashlib.sha256(json.dumps({"mode": mode, "fallback": fallback, "model": MODEL_VERSION,
                "space": EMBEDDING_SPACE_ID, "query_model": self.encoder.model_version,
                "vector": vector if mode == "hybrid" else None, "index": "russian-jsonb-string-v1:rrf60"}, sort_keys=True).encode()).hexdigest()
            snapshot = hashlib.sha256(json.dumps({**dict(source), "vacancy_revision": vacancy_revision}, sort_keys=True).encode()).hexdigest()
            if prior and (prior["snapshot"] != snapshot or prior["channel"] != channel):
                raise HTTPException(409, "search sources or retrieval mode changed; restart search")
            offset = prior["offset"] if prior else 0
            if mode == "browse":
                result_sql = "SELECT *,NULL::bigint AS lexical_rank,NULL::bigint AS semantic_rank,0::float8 AS rrf_score FROM filtered"
                result_params = []
                order = "specialization,rank,candidate_id"
            else:
                semantic = "SELECT candidate_id, NULL::bigint AS semantic_rank FROM filtered WHERE false"
                result_params = [query, query]
                if mode == "hybrid":
                    # Keep KNN ordering on the indexed base relation, outside the
                    # ranking window/CTE. Exact recheck prevents filter leakage.
                    # Small or highly selective pools retain exact recall; larger
                    # searches use a bounded ANN channel (200 nearest neighbors).
                    neighbors = self._neighbors(connection, where, params, vector)
                    semantic = "SELECT candidate_id,row_number() OVER (ORDER BY semantic_vector <=> %s::vector,candidate_id) AS semantic_rank FROM filtered WHERE candidate_id=ANY(%s)"
                    result_params.extend([json.dumps(vector), neighbors])
                result_sql = f"""WITH lexical AS (SELECT candidate_id,row_number() OVER (ORDER BY ts_rank_cd({_FTS},websearch_to_tsquery('russian',%s)) DESC,candidate_id) AS lexical_rank
                    FROM filtered WHERE {_FTS} @@ websearch_to_tsquery('russian',%s)), semantic AS ({semantic}),
                    channels AS (SELECT COALESCE(l.candidate_id,s.candidate_id) AS candidate_id,l.lexical_rank,s.semantic_rank FROM lexical l FULL JOIN semantic s USING(candidate_id))
                    SELECT f.*,c.lexical_rank,c.semantic_rank,
                      COALESCE(1.0/(60+c.lexical_rank),0)+COALESCE(1.0/(60+c.semantic_rank),0) AS rrf_score FROM filtered f JOIN channels c USING(candidate_id)"""
                order = "rrf_score DESC,candidate_id"
            # Count and page come from the identical snapshot and retrieval channel.
            rows = connection.execute(f"""WITH source AS ({_SOURCE}),filtered AS (SELECT * FROM source WHERE {where}),
                results AS ({result_sql}), page AS (SELECT * FROM results ORDER BY {order} LIMIT %s OFFSET %s)
                SELECT (SELECT count(*) FROM results) AS total, COALESCE(jsonb_agg(to_jsonb(page) ORDER BY {order}), '[]'::jsonb) AS items FROM page""",
                (*params, *result_params, request.limit, offset)).fetchone()
            items = [self._card(row) for row in rows["items"]]
            total = rows["total"]
            next_offset = offset + len(items)
            has_more = next_offset < total
            cursor = _encode_cursor({"v": 1, "query": query_hash, "snapshot": snapshot, "channel": channel, "offset": next_offset}) if has_more else None
            return SearchPage(items=items, total=total, limit=request.limit, has_more=has_more, next_cursor=cursor, mode=mode, fallback_reason=fallback)

    def profile(self, owner_id: UUID, candidate_id: UUID) -> PublicCandidateDetail:
        """Return the current consented public revision, without private profile reads."""
        with connect(self.database_url) as connection:
            row = connection.execute(f"WITH source AS ({_SOURCE}) SELECT * FROM source WHERE candidate_id=%s", (candidate_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "candidate is not in the public pool")
            card = self._card(row)
            return PublicCandidateDetail(**card.model_dump(), public_profile=PublicProfile.model_validate(row["public_profile"]))
