"""Atomic profile revisions, durable leases and generation-safe pool publication."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.models import (
    GRADE_RANK,
    CandidateProfile,
    CandidateStatus,
    Category,
    Coordinate,
    CoordinateAddition,
    PoolEntry,
    PoolPage,
    StrictSchema,
    missing_requirements,
)
from matching_engine.strict import schema_hash, validate_coordinates


class ConflictError(Exception):
    """An upstream source revision or immutable registry coordinate conflicts."""


class NotFoundError(Exception):
    """The requested candidate or currently published artifact does not exist."""


@dataclass(frozen=True)
class Job:
    """Claimed work snapshot; token guards completion after lease recovery."""

    id: int
    candidate_id: UUID
    revision: int
    schema: StrictSchema
    profile: CandidateProfile
    lease_token: UUID
    attempts: int


def _schema(connection) -> StrictSchema:
    row = connection.execute("""
        SELECT s.* FROM candidate_pipeline.strict_schemas s
        JOIN candidate_pipeline.settings t ON t.active_schema_version = s.version
    """).fetchone()
    if row is None:
        raise RuntimeError("strict registry is not initialized; run migrate")
    return StrictSchema(version=row["version"], schema_hash=row["schema_hash"],
                        coordinates=row["coordinates"])


def _event(connection, kind: str, candidate_id: UUID, revision: int,
           schema_version: int, reason: str) -> None:
    connection.execute("""
        INSERT INTO candidate_pipeline.outbox
            (event_type,candidate_id,revision,schema_version,payload)
        VALUES (%s,%s,%s,%s,%s)
    """, (kind, candidate_id, revision, schema_version, Jsonb({"reason": reason})))


class Store:
    """Persist matching-owned snapshots using PostgreSQL as the only state authority."""

    def __init__(self, database_url: str) -> None:
        """Keep the DSN private; requests create independent short transactions."""
        self.database_url = database_url

    def ready(self) -> bool:
        """Check connectivity, completed migrations and active registry availability."""
        try:
            with connect(self.database_url) as connection:
                _schema(connection)
                connection.execute("SELECT 1 FROM candidate_pipeline.jobs LIMIT 1")
            return True
        except (psycopg.Error, RuntimeError):
            return False

    def schema(self) -> StrictSchema:
        """Read the active strict generation without modifying it."""
        with connect(self.database_url) as connection:
            return _schema(connection)

    def intake(self, candidate_id: UUID, profile: CandidateProfile) -> CandidateStatus:
        """Replace an upstream snapshot atomically; stale revisions cannot restore consent."""
        serialized = profile.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        missing = missing_requirements(profile)
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            schema = _schema(connection)
            validate_coordinates(profile, schema)
            current = connection.execute(
                "SELECT * FROM candidate_pipeline.candidates WHERE candidate_id=%s FOR UPDATE",
                (candidate_id,),
            ).fetchone()
            if current is not None:
                if profile.source_revision < current["source_revision"]:
                    raise ConflictError("stale source_revision")
                if profile.source_revision == current["source_revision"]:
                    if digest != current["profile_hash"]:
                        raise ConflictError("source_revision reused with different profile")
                    return self._status(connection, candidate_id)
            revision = current["revision"] + 1 if current else 1
            published = connection.execute(
                "DELETE FROM candidate_pipeline.pool WHERE candidate_id=%s RETURNING *",
                (candidate_id,),
            ).fetchone()
            state = "draft" if missing else "queued"
            connection.execute("""
                INSERT INTO candidate_pipeline.candidates
                    (candidate_id,revision,source_revision,profile_hash,profile,state,missing_requirements)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    revision=EXCLUDED.revision,source_revision=EXCLUDED.source_revision,
                    profile_hash=EXCLUDED.profile_hash,profile=EXCLUDED.profile,
                    state=EXCLUDED.state,missing_requirements=EXCLUDED.missing_requirements,
                    updated_at=now()
            """, (candidate_id, revision, profile.source_revision, digest,
                  Jsonb(serialized), state, Jsonb(missing)))
            connection.execute("""
                UPDATE candidate_pipeline.jobs SET state='obsolete',lease_token=NULL,lease_until=NULL
                WHERE candidate_id=%s AND state IN ('queued','processing','retrying','failed')
            """, (candidate_id,))
            if published:
                _event(connection, "candidate.withdrawn", candidate_id, published["revision"],
                       published["schema_version"], "profile_changed" if not missing else "ineligible")
            if not missing:
                self._enqueue(connection, candidate_id, revision, schema.version)
            return self._status(connection, candidate_id)

    @staticmethod
    def _enqueue(connection, candidate_id: UUID, revision: int, schema_version: int) -> None:
        connection.execute("""
            INSERT INTO candidate_pipeline.jobs(candidate_id,revision,schema_version,state)
            VALUES (%s,%s,%s,'queued') ON CONFLICT DO NOTHING
        """, (candidate_id, revision, schema_version))

    def extend_schema(self, addition: CoordinateAddition) -> StrictSchema:
        """Append one coordinate and queue all snapshots, immediately hiding old vectors."""
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            old = _schema(connection)
            if any(c.group == addition.group and c.key == addition.key for c in old.coordinates):
                raise ConflictError("coordinate already exists")
            coordinates = old.coordinates + [
                Coordinate(index=len(old.coordinates), group=addition.group, key=addition.key)
            ]
            new = StrictSchema(version=old.version + 1, schema_hash=schema_hash(coordinates),
                               coordinates=coordinates)
            connection.execute("""
                INSERT INTO candidate_pipeline.strict_schemas(version,schema_hash,coordinates)
                VALUES (%s,%s,%s)
            """, (new.version, new.schema_hash, Jsonb([c.model_dump() for c in coordinates])))
            connection.execute("""
                INSERT INTO candidate_pipeline.outbox
                    (event_type,candidate_id,revision,schema_version,payload)
                SELECT 'candidate.withdrawn',candidate_id,revision,schema_version,%s
                FROM candidate_pipeline.pool
            """, (Jsonb({"reason": "strict_schema_changed"}),))
            connection.execute("DELETE FROM candidate_pipeline.pool")
            connection.execute(
                "UPDATE candidate_pipeline.settings SET active_schema_version=%s", (new.version,)
            )
            connection.execute("""
                UPDATE candidate_pipeline.jobs SET state='obsolete',lease_token=NULL,lease_until=NULL
                WHERE state IN ('queued','processing','retrying','failed')
            """)
            connection.execute("""
                INSERT INTO candidate_pipeline.jobs(candidate_id,revision,schema_version,state)
                SELECT candidate_id,revision,%s,'queued' FROM candidate_pipeline.candidates
            """, (new.version,))
            connection.execute("""
                UPDATE candidate_pipeline.candidates SET state=CASE
                    WHEN missing_requirements='[]'::jsonb THEN 'queued' ELSE 'draft' END,
                    updated_at=now()
            """)
            return new

    def claim(self, lease_seconds: int, max_attempts: int) -> Job | None:
        """Claim one available job with SKIP LOCKED, reclaiming expired process leases."""
        token = uuid4()
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            # A final attempt killed by process termination needs a terminal state too.
            exhausted = connection.execute("""
                UPDATE candidate_pipeline.jobs SET state='failed',error_code='lease_exhausted',
                    lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE state='processing' AND lease_until<now() AND attempts >= %s
                RETURNING candidate_id,revision,schema_version
            """, (max_attempts,)).fetchall()
            for row in exhausted:
                connection.execute("""
                    UPDATE candidate_pipeline.candidates SET state='failed',updated_at=now()
                    WHERE candidate_id=%s AND revision=%s AND EXISTS (
                        SELECT 1 FROM candidate_pipeline.settings WHERE active_schema_version=%s)
                """, (row["candidate_id"], row["revision"], row["schema_version"]))
            row = connection.execute("""
                WITH next AS (
                    SELECT id FROM candidate_pipeline.jobs
                    WHERE attempts < %s AND (
                        (state IN ('queued','retrying') AND available_at <= now()) OR
                        (state='processing' AND lease_until < now()))
                    ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE candidate_pipeline.jobs j SET
                    state='processing',attempts=attempts+1,lease_token=%s,
                    lease_until=now()+make_interval(secs=>%s),updated_at=now()
                FROM next WHERE j.id=next.id RETURNING j.*
            """, (max_attempts, token, lease_seconds)).fetchone()
            if row is None:
                return None
            candidate = connection.execute(
                "SELECT * FROM candidate_pipeline.candidates WHERE candidate_id=%s FOR UPDATE",
                (row["candidate_id"],),
            ).fetchone()
            schema = _schema(connection)
            if candidate["revision"] != row["revision"] or schema.version != row["schema_version"]:
                connection.execute(
                    "UPDATE candidate_pipeline.jobs SET state='obsolete',lease_token=NULL WHERE id=%s",
                    (row["id"],),
                )
                return None
            profile = CandidateProfile.model_validate(candidate["profile"])
            if missing_requirements(profile):
                connection.execute("""
                    UPDATE candidate_pipeline.jobs SET state='completed',lease_token=NULL,
                        lease_until=NULL WHERE id=%s
                """, (row["id"],))
                connection.execute("""
                    UPDATE candidate_pipeline.candidates SET state='draft' WHERE candidate_id=%s
                """, (row["candidate_id"],))
                return None
            connection.execute("""
                UPDATE candidate_pipeline.candidates SET state='processing',updated_at=now()
                WHERE candidate_id=%s
            """, (row["candidate_id"],))
            return Job(id=row["id"], candidate_id=row["candidate_id"], revision=row["revision"],
                       schema=schema, profile=profile, lease_token=token, attempts=row["attempts"])

    def heartbeat(self, job: Job, lease_seconds: int) -> bool:
        """Renew only the current token; a reclaimed or invalidated lease stays invalid."""
        with connect(self.database_url) as connection:
            cursor = connection.execute("""
                UPDATE candidate_pipeline.jobs SET lease_until=now()+make_interval(secs=>%s)
                WHERE id=%s AND lease_token=%s AND state='processing' AND lease_until>now()
            """, (lease_seconds, job.id, job.lease_token))
            return cursor.rowcount == 1

    def cached_embedding(self, cache_key: str, model_version: str) -> list[float] | None:
        """Retrieve the exact model/text cache, without retaining private semantic text."""
        with connect(self.database_url) as connection:
            row = connection.execute("""
                SELECT semantic_vector::text AS vector FROM candidate_pipeline.embedding_cache
                WHERE cache_key=%s AND model_version=%s
            """, (cache_key, model_version)).fetchone()
            return json.loads(row["vector"]) if row else None

    def cache_embedding(self, cache_key: str, model_version: str, vector: list[float]) -> None:
        """Persist a real validated inference result for retries and schema backfills."""
        with connect(self.database_url) as connection:
            connection.execute("""
                INSERT INTO candidate_pipeline.embedding_cache(cache_key,model_version,semantic_vector)
                VALUES (%s,%s,%s::vector) ON CONFLICT DO NOTHING
            """, (cache_key, model_version, json.dumps(vector)))

    def publish(self, job: Job, category: Category, strength: int, strict_vector: list[float],
                semantic_vector: list[float], model_version: str, public_profile: dict,
                public_pdf: bytes, owner_pdf: bytes, strict_evidence: list[dict]) -> bool:
        """Commit both vectors, PDFs, readiness and outbox iff revision/schema/lease still match."""
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            current = connection.execute(
                "SELECT * FROM candidate_pipeline.candidates WHERE candidate_id=%s FOR UPDATE",
                (job.candidate_id,),
            ).fetchone()
            lease = connection.execute("""
                SELECT * FROM candidate_pipeline.jobs
                WHERE id=%s AND lease_token=%s AND state='processing' AND lease_until>now()
                FOR UPDATE
            """, (job.id, job.lease_token)).fetchone()
            if lease is None:
                return False
            active = _schema(connection)
            if (current["revision"] != job.revision or active.version != job.schema.version
                    or missing_requirements(CandidateProfile.model_validate(current["profile"]))):
                connection.execute("""
                    UPDATE candidate_pipeline.jobs SET state='obsolete',lease_token=NULL,
                        lease_until=NULL WHERE id=%s
                """, (job.id,))
                return False
            if len(strict_vector) != len(active.coordinates):
                raise ValueError("strict vector dimensions do not match active registry")
            connection.execute("""
                INSERT INTO candidate_pipeline.pool
                    (candidate_id,revision,schema_version,schema_hash,specialization,grade,grade_rank,
                     confirmed_strength,strict_vector,strict_evidence,semantic_vector,semantic_model_version,
                     public_profile,public_pdf,owner_pdf)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    revision=EXCLUDED.revision,schema_version=EXCLUDED.schema_version,
                    schema_hash=EXCLUDED.schema_hash,specialization=EXCLUDED.specialization,
                    grade=EXCLUDED.grade,grade_rank=EXCLUDED.grade_rank,
                    confirmed_strength=EXCLUDED.confirmed_strength,
                    strict_vector=EXCLUDED.strict_vector,strict_evidence=EXCLUDED.strict_evidence,
                    semantic_vector=EXCLUDED.semantic_vector,
                    semantic_model_version=EXCLUDED.semantic_model_version,
                    public_profile=EXCLUDED.public_profile,public_pdf=EXCLUDED.public_pdf,
                    owner_pdf=EXCLUDED.owner_pdf,published_at=now()
            """, (job.candidate_id, job.revision, active.version, active.schema_hash,
                  category.specialization, category.grade, GRADE_RANK[category.grade], strength,
                  strict_vector, Jsonb(strict_evidence), json.dumps(semantic_vector), model_version,
                  Jsonb(public_profile),
                  public_pdf, owner_pdf))
            connection.execute("""
                UPDATE candidate_pipeline.candidates SET state='ready',updated_at=now()
                WHERE candidate_id=%s
            """, (job.candidate_id,))
            connection.execute("""
                UPDATE candidate_pipeline.jobs SET state='completed',lease_token=NULL,
                    lease_until=NULL,error_code=NULL,updated_at=now() WHERE id=%s
            """, (job.id,))
            _event(connection, "candidate.ready", job.candidate_id, job.revision,
                   active.version, "artifacts_ready")
            return True

    def fail(self, job: Job, error_code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Save a safe code and bounded backoff; stale workers cannot mutate new snapshots."""
        state = "failed" if job.attempts >= max_attempts else "retrying"
        delay = min(retry_base_seconds * 2 ** (job.attempts - 1), 3600)
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            row = connection.execute("""
                UPDATE candidate_pipeline.jobs SET state=%s,error_code=%s,
                    available_at=now()+make_interval(secs=>%s),lease_token=NULL,lease_until=NULL,
                    updated_at=now()
                WHERE id=%s AND lease_token=%s AND state='processing' RETURNING id
            """, (state, error_code, delay, job.id, job.lease_token)).fetchone()
            if row:
                connection.execute("""
                    UPDATE candidate_pipeline.candidates SET state=%s,updated_at=now()
                    WHERE candidate_id=%s AND revision=%s AND EXISTS (
                        SELECT 1 FROM candidate_pipeline.settings WHERE active_schema_version=%s)
                """, (state, job.candidate_id, job.revision, job.schema.version))

    def retry(self, candidate_id: UUID) -> CandidateStatus:
        """Explicitly requeue a terminal failure; do not interrupt a live worker."""
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            status = self._status(connection, candidate_id)
            if status.state not in ("failed", "retrying"):
                raise ConflictError("only failed or retrying candidates can be retried")
            schema = _schema(connection)
            connection.execute("""
                UPDATE candidate_pipeline.jobs SET state='queued',attempts=0,available_at=now(),
                    error_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE candidate_id=%s AND revision=%s AND schema_version=%s
                    AND state IN ('failed','retrying')
            """, (candidate_id, status.profile_revision, schema.version))
            connection.execute("""
                UPDATE candidate_pipeline.candidates SET state='queued',updated_at=now()
                WHERE candidate_id=%s
            """, (candidate_id,))
            return self._status(connection, candidate_id)

    @staticmethod
    def _status(connection, candidate_id: UUID) -> CandidateStatus:
        row = connection.execute(
            "SELECT * FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("candidate not found")
        schema = _schema(connection)
        job = connection.execute("""
            SELECT * FROM candidate_pipeline.jobs
            WHERE candidate_id=%s AND revision=%s AND schema_version=%s
        """, (candidate_id, row["revision"], schema.version)).fetchone()
        profile = CandidateProfile.model_validate(row["profile"])
        category = Category(specialization=profile.primary_direction, grade=profile.grade_result.grade) \
            if not missing_requirements(profile) else None
        return CandidateStatus(
            candidate_id=candidate_id, profile_revision=row["revision"], state=row["state"],
            missing_requirements=row["missing_requirements"], category=category,
            strict_schema_version=schema.version if job else None,
            attempt_count=job["attempts"] if job else 0,
            error_code=job["error_code"] if job else None,
        )

    def status(self, candidate_id: UUID) -> CandidateStatus:
        """Read one current lifecycle snapshot without exposing private data."""
        with connect(self.database_url) as connection:
            return self._status(connection, candidate_id)

    def pool(self, limit: int, offset: int) -> PoolPage:
        """List active-generation ready candidates ranked grade, strength, UUID per direction."""
        with connect(self.database_url) as connection:
            rows = connection.execute("""
                WITH ready AS (
                    SELECT p.*,row_number() OVER (PARTITION BY p.specialization
                        ORDER BY p.grade_rank DESC,p.confirmed_strength DESC,p.candidate_id) AS rank
                    FROM candidate_pipeline.pool p
                    JOIN candidate_pipeline.candidates c
                        ON c.candidate_id=p.candidate_id AND c.revision=p.revision AND c.state='ready'
                    JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
                ) SELECT *,semantic_vector::text AS vector FROM ready
                ORDER BY specialization,rank LIMIT %s OFFSET %s
            """, (limit, offset)).fetchall()
            return PoolPage(items=[PoolEntry(
                candidate_id=row["candidate_id"], profile_revision=row["revision"],
                display_name=row["public_profile"]["display_name"], city=row["public_profile"]["city"],
                salary=row["public_profile"]["salary"],
                category=Category(specialization=row["specialization"], grade=row["grade"]),
                rank=row["rank"], confirmed_strength=row["confirmed_strength"],
                strict_vector=row["strict_vector"], strict_evidence=row["strict_evidence"],
                strict_schema_version=row["schema_version"],
                strict_schema_hash=row["schema_hash"], semantic_vector=json.loads(row["vector"]),
                semantic_model_version=row["semantic_model_version"], published_at=row["published_at"],
                resume_url=f"/internal/pool/{row['candidate_id']}/resume.pdf",
            ) for row in rows], limit=limit, offset=offset)

    def resume(self, candidate_id: UUID, public: bool = True) -> bytes:
        """Return a PDF only while its exact revision and vector generation remain published."""
        column = "public_pdf" if public else "owner_pdf"
        with connect(self.database_url) as connection:
            row = connection.execute(f"""
                SELECT p.{column} AS pdf FROM candidate_pipeline.pool p
                JOIN candidate_pipeline.candidates c
                    ON c.candidate_id=p.candidate_id AND c.revision=p.revision AND c.state='ready'
                JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
                WHERE p.candidate_id=%s
            """, (candidate_id,)).fetchone()
            if row is None:
                raise NotFoundError("published resume not found")
            return bytes(row["pdf"])
