"""Atomic profile revisions, durable leases and generation-safe pool publication."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.models import StrictSchema
from matching_engine.store import ConflictError, NotFoundError, Store, _schema
from matching_engine.vacancy_features import build_strict_features, semantic_text
from matching_engine.vacancy_models import (
    VacancyInput,
    VacancyPipelineStatus,
    VacancyReady,
    VacancyReadyPage,
    build_mode,
    missing_requirements,
)
from matching_engine.worker import validate_embedding


@dataclass(frozen=True)
class VacancyJob:
    """Claimed work snapshot; token guards completion after lease recovery."""

    id: int
    vacancy_id: UUID
    revision: int
    schema: StrictSchema
    profile: VacancyInput
    lease_token: UUID
    attempts: int


def _event(connection, kind: str, vacancy_id: UUID, revision: int,
           schema_version: int, reason: str) -> None:
    connection.execute("""
        INSERT INTO vacancy_pipeline.outbox
            (event_type,vacancy_id,revision,schema_version,payload)
        VALUES (%s,%s,%s,%s,%s)
    """, (kind, vacancy_id, revision, schema_version, Jsonb({"reason": reason})))


class VacancyStore:
    """Persist matching-owned snapshots using PostgreSQL as the only state authority."""

    def __init__(self, database_url: str) -> None:
        """Keep the DSN private; requests create independent short transactions."""
        self.database_url = database_url

    def ready(self) -> bool:
        """Check connectivity, completed migrations and active registry availability."""
        try:
            with connect(self.database_url) as connection:
                _schema(connection)
                connection.execute("SELECT 1 FROM vacancy_pipeline.jobs LIMIT 1")
            return True
        except (psycopg.Error, RuntimeError):
            return False

    def schema(self) -> StrictSchema:
        """Read the active strict generation without modifying it."""
        with connect(self.database_url) as connection:
            return _schema(connection)

    def intake(self, vacancy_id: UUID, profile: VacancyInput, raw_input: dict | None = None) -> VacancyPipelineStatus:
        """Persist a monotonic snapshot; an assigned owner and stale revisions cannot change it."""
        serialized = profile.model_dump(mode="json", by_alias=True)
        digest = hashlib.sha256(
            json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        missing = missing_requirements(profile)
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            schema = _schema(connection)
            build_strict_features(profile, schema)
            current = connection.execute(
                "SELECT * FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s FOR UPDATE",
                (vacancy_id,),
            ).fetchone()
            if current is not None:
                if current["owner_id"] is not None and profile.owner_id != current["owner_id"]:
                    raise ConflictError("owner_id cannot be changed once assigned")
                if profile.source_revision < current["source_revision"]:
                    raise ConflictError("stale source_revision")
                if profile.source_revision == current["source_revision"]:
                    if digest != current["profile_hash"] and serialized != VacancyInput.model_validate(
                        current["profile"]
                    ).model_dump(mode="json", by_alias=True):
                        raise ConflictError("source_revision reused with different profile")
                    return self._status(connection, vacancy_id)
            revision = current["revision"] + 1 if current else 1
            published = connection.execute(
                "DELETE FROM vacancy_pipeline.ready WHERE vacancy_id=%s RETURNING *",
                (vacancy_id,),
            ).fetchone()
            state = "draft" if missing else "queued"
            connection.execute("""
                INSERT INTO vacancy_pipeline.vacancies
                    (vacancy_id,owner_id,revision,source_revision,profile_hash,profile,raw_input,business_status,state,missing_requirements)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (vacancy_id) DO UPDATE SET
                    revision=EXCLUDED.revision,source_revision=EXCLUDED.source_revision,
                    profile_hash=EXCLUDED.profile_hash,profile=EXCLUDED.profile,
                    raw_input=EXCLUDED.raw_input,owner_id=EXCLUDED.owner_id,business_status=EXCLUDED.business_status,
                    state=EXCLUDED.state,missing_requirements=EXCLUDED.missing_requirements,
                    updated_at=now()
            """, (vacancy_id, profile.owner_id, revision, profile.source_revision, digest,
                  Jsonb(serialized), Jsonb(raw_input if raw_input is not None else serialized),
                  profile.status, state, Jsonb(missing)))
            connection.execute("""
                UPDATE vacancy_pipeline.jobs SET state='obsolete',lease_token=NULL,lease_until=NULL
                WHERE vacancy_id=%s AND state IN ('queued','processing','retrying','failed')
            """, (vacancy_id,))
            if published:
                _event(connection, "vacancy.withdrawn", vacancy_id, published["revision"],
                       published["schema_version"], "profile_changed" if not missing else "ineligible")
            if not missing:
                self._enqueue(connection, vacancy_id, revision, schema.version)
            return self._status(connection, vacancy_id)

    @staticmethod
    def _enqueue(connection, vacancy_id: UUID, revision: int, schema_version: int) -> None:
        connection.execute("""
            INSERT INTO vacancy_pipeline.jobs(vacancy_id,revision,schema_version,state)
            VALUES (%s,%s,%s,'queued') ON CONFLICT DO NOTHING
        """, (vacancy_id, revision, schema_version))

    def claim(self, lease_seconds: int, max_attempts: int) -> VacancyJob | None:
        """Claim one available job with SKIP LOCKED, reclaiming expired process leases."""
        token = uuid4()
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            # A final attempt killed by process termination needs a terminal state too.
            exhausted = connection.execute("""
                UPDATE vacancy_pipeline.jobs SET state='failed',error_code='lease_exhausted',
                    lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE state='processing' AND lease_until<now() AND attempts >= %s
                RETURNING vacancy_id,revision,schema_version
            """, (max_attempts,)).fetchall()
            for row in exhausted:
                connection.execute("""
                    UPDATE vacancy_pipeline.vacancies SET state='failed',updated_at=now()
                    WHERE vacancy_id=%s AND revision=%s AND EXISTS (
                        SELECT 1 FROM candidate_pipeline.settings WHERE active_schema_version=%s)
                """, (row["vacancy_id"], row["revision"], row["schema_version"]))
            row = connection.execute("""
                WITH next AS (
                    SELECT id FROM vacancy_pipeline.jobs
                    WHERE attempts < %s AND (
                        (state IN ('queued','retrying') AND available_at <= now()) OR
                        (state='processing' AND lease_until < now()))
                    ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1
                ) UPDATE vacancy_pipeline.jobs j SET
                    state='processing',attempts=attempts+1,lease_token=%s,
                    lease_until=now()+make_interval(secs=>%s),updated_at=now()
                FROM next WHERE j.id=next.id RETURNING j.*
            """, (max_attempts, token, lease_seconds)).fetchone()
            if row is None:
                return None
            vacancy = connection.execute(
                "SELECT * FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s FOR UPDATE",
                (row["vacancy_id"],),
            ).fetchone()
            schema = _schema(connection)
            if vacancy["revision"] != row["revision"] or schema.version != row["schema_version"]:
                connection.execute(
                    "UPDATE vacancy_pipeline.jobs SET state='obsolete',lease_token=NULL WHERE id=%s",
                    (row["id"],),
                )
                return None
            profile = VacancyInput.model_validate(vacancy["profile"])
            if missing_requirements(profile):
                connection.execute("""
                    UPDATE vacancy_pipeline.jobs SET state='completed',lease_token=NULL,
                        lease_until=NULL WHERE id=%s
                """, (row["id"],))
                connection.execute("""
                    UPDATE vacancy_pipeline.vacancies SET state='draft' WHERE vacancy_id=%s
                """, (row["vacancy_id"],))
                return None
            connection.execute("""
                UPDATE vacancy_pipeline.vacancies SET state='processing',updated_at=now()
                WHERE vacancy_id=%s
            """, (row["vacancy_id"],))
            return VacancyJob(id=row["id"], vacancy_id=row["vacancy_id"], revision=row["revision"],
                       schema=schema, profile=profile, lease_token=token, attempts=row["attempts"])

    def heartbeat(self, job: VacancyJob, lease_seconds: int) -> bool:
        """Renew only the current token; a reclaimed or invalidated lease stays invalid."""
        with connect(self.database_url) as connection:
            cursor = connection.execute("""
                UPDATE vacancy_pipeline.jobs SET lease_until=now()+make_interval(secs=>%s)
                WHERE id=%s AND lease_token=%s AND state='processing' AND lease_until>now()
            """, (lease_seconds, job.id, job.lease_token))
            return cursor.rowcount == 1

    def cached_embedding(self, cache_key: str, model_version: str) -> list[float] | None:
        """Read the shared model-and-role-specific inference cache."""
        return Store(self.database_url).cached_embedding(cache_key, model_version)

    def cache_embedding(self, cache_key: str, model_version: str, vector: list[float]) -> None:
        """Reuse candidate cache storage after validating actual inference output."""
        validate_embedding(vector)
        Store(self.database_url).cache_embedding(cache_key, model_version, vector)

    def publish(self, job: VacancyJob, strict_vector: list[float], requirement_mask: list[bool],
                semantic_vector: list[float] | None, model_version: str | None,
                semantic_space_id: str | None) -> bool:
        """Publish atomically only for the current open snapshot, schema and unexpired lease."""
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            current = connection.execute(
                "SELECT * FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s FOR UPDATE",
                (job.vacancy_id,),
            ).fetchone()
            lease = connection.execute("""
                SELECT * FROM vacancy_pipeline.jobs
                WHERE id=%s AND lease_token=%s AND state='processing' AND lease_until>now()
                FOR UPDATE
            """, (job.id, job.lease_token)).fetchone()
            if lease is None:
                return False
            active = _schema(connection)
            vacancy = VacancyInput.model_validate(current["profile"])
            if current["revision"] != job.revision or active.version != job.schema.version or missing_requirements(vacancy):
                connection.execute("""UPDATE vacancy_pipeline.jobs SET state='obsolete',lease_token=NULL,
                    lease_until=NULL WHERE id=%s""", (job.id,))
                return False
            expected_vector, expected_mask = build_strict_features(vacancy, active)
            if strict_vector != expected_vector or requirement_mask != expected_mask:
                raise ValueError("vacancy strict artifacts do not match the active snapshot")
            if semantic_text(vacancy) is None:
                if semantic_vector is not None or model_version is not None or semantic_space_id is not None:
                    raise ValueError("empty bio must have absent semantic artifacts")
            else:
                if semantic_vector is None or not model_version or not semantic_space_id:
                    raise ValueError("bio requires a complete semantic artifact")
                validate_embedding(semantic_vector)
            connection.execute("""
                INSERT INTO vacancy_pipeline.ready
                    (vacancy_id,revision,schema_version,schema_hash,build_mode,strict_vector,
                     requirement_mask,semantic_vector,semantic_model_version,semantic_space_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s)
                ON CONFLICT (vacancy_id) DO UPDATE SET
                    revision=EXCLUDED.revision,schema_version=EXCLUDED.schema_version,
                    schema_hash=EXCLUDED.schema_hash,build_mode=EXCLUDED.build_mode,
                    strict_vector=EXCLUDED.strict_vector,requirement_mask=EXCLUDED.requirement_mask,
                    semantic_vector=EXCLUDED.semantic_vector,semantic_model_version=EXCLUDED.semantic_model_version,
                    semantic_space_id=EXCLUDED.semantic_space_id,published_at=now()
            """, (job.vacancy_id, job.revision, active.version, active.schema_hash, build_mode(vacancy),
                  strict_vector, requirement_mask, json.dumps(semantic_vector) if semantic_vector is not None else None,
                  model_version, semantic_space_id))
            connection.execute("UPDATE vacancy_pipeline.vacancies SET state='ready',updated_at=now() WHERE vacancy_id=%s", (job.vacancy_id,))
            connection.execute("""UPDATE vacancy_pipeline.jobs SET state='completed',lease_token=NULL,
                lease_until=NULL,error_code=NULL,updated_at=now() WHERE id=%s""", (job.id,))
            _event(connection, "vacancy.ready", job.vacancy_id, job.revision, active.version, "artifacts_ready")
            return True

    def fail(self, job: VacancyJob, error_code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Save a safe code and bounded backoff; stale workers cannot mutate new snapshots."""
        state = "failed" if job.attempts >= max_attempts else "retrying"
        delay = min(retry_base_seconds * 2 ** (job.attempts - 1), 3600)
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            row = connection.execute("""
                UPDATE vacancy_pipeline.jobs SET state=%s,error_code=%s,
                    available_at=now()+make_interval(secs=>%s),lease_token=NULL,lease_until=NULL,
                    updated_at=now()
                WHERE id=%s AND lease_token=%s AND state='processing' RETURNING id
            """, (state, error_code, delay, job.id, job.lease_token)).fetchone()
            if row:
                connection.execute("""
                    UPDATE vacancy_pipeline.vacancies SET state=%s,updated_at=now()
                    WHERE vacancy_id=%s AND revision=%s AND EXISTS (
                        SELECT 1 FROM candidate_pipeline.settings WHERE active_schema_version=%s)
                """, (state, job.vacancy_id, job.revision, job.schema.version))

    def retry(self, vacancy_id: UUID) -> VacancyPipelineStatus:
        """Explicitly requeue a terminal failure; do not interrupt a live worker."""
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            status = self._status(connection, vacancy_id)
            if status.state not in ("failed", "retrying"):
                raise ConflictError("only failed or retrying vacancies can be retried")
            schema = _schema(connection)
            connection.execute("""
                UPDATE vacancy_pipeline.jobs SET state='queued',attempts=0,available_at=now(),
                    error_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE vacancy_id=%s AND revision=%s AND schema_version=%s
                    AND state IN ('failed','retrying')
            """, (vacancy_id, status.revision, schema.version))
            connection.execute("""
                UPDATE vacancy_pipeline.vacancies SET state='queued',updated_at=now()
                WHERE vacancy_id=%s
            """, (vacancy_id,))
            return self._status(connection, vacancy_id)

    @staticmethod
    def _status(connection, vacancy_id: UUID) -> VacancyPipelineStatus:
        row = connection.execute(
            "SELECT * FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s", (vacancy_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("vacancy not found")
        schema = _schema(connection)
        job = connection.execute("""
            SELECT * FROM vacancy_pipeline.jobs
            WHERE vacancy_id=%s AND revision=%s AND schema_version=%s
        """, (vacancy_id, row["revision"], schema.version)).fetchone()
        return VacancyPipelineStatus(
            vacancy_id=vacancy_id, revision=row["revision"], source_revision=row["source_revision"],
            status=row["business_status"], state=row["state"], missing_requirements=row["missing_requirements"],
            strict_schema_version=schema.version if job else None,
            attempt_count=job["attempts"] if job else 0,
            error_code=job["error_code"] if job else None,
        )

    def status(self, vacancy_id: UUID) -> VacancyPipelineStatus:
        """Read one current lifecycle snapshot without exposing private data."""
        with connect(self.database_url) as connection:
            return self._status(connection, vacancy_id)

    @staticmethod
    def _entry(row: dict) -> VacancyReady:
        return VacancyReady.model_validate({
            **row["profile"], "vacancy_id": row["vacancy_id"], "revision": row["revision"],
            "build_mode": row["build_mode"], "strict_vector": row["strict_vector"],
            "requirement_mask": row["requirement_mask"], "strict_schema_version": row["schema_version"],
            "strict_schema_hash": row["schema_hash"],
            "semantic_vector": json.loads(row["vector"]) if row["vector"] is not None else None,
            "semantic_model_version": row["semantic_model_version"],
            "semantic_space_id": row["semantic_space_id"], "semantic_available": row["vector"] is not None,
            "published_at": row["published_at"],
        })

    def page(self, limit: int, offset: int) -> VacancyReadyPage:
        """Return bounded current-generation ready input for the future combiner."""
        with connect(self.database_url) as connection:
            rows = connection.execute("""
                SELECT r.*,v.profile,r.semantic_vector::text AS vector FROM vacancy_pipeline.ready r
                JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=r.vacancy_id AND v.revision=r.revision
                    AND v.state='ready' AND v.business_status='open'
                JOIN candidate_pipeline.settings s ON s.active_schema_version=r.schema_version
                ORDER BY r.vacancy_id LIMIT %s OFFSET %s
            """, (limit, offset)).fetchall()
            return VacancyReadyPage(items=[self._entry(row) for row in rows], limit=limit, offset=offset)

    def ready_entry(self, vacancy_id: UUID) -> VacancyReady:
        """Read one eligible artifact; obsolete, incomplete and inactive vacancies return 404."""
        with connect(self.database_url) as connection:
            row = connection.execute("""
                SELECT r.*,v.profile,r.semantic_vector::text AS vector FROM vacancy_pipeline.ready r
                JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=r.vacancy_id AND v.revision=r.revision
                    AND v.state='ready' AND v.business_status='open'
                JOIN candidate_pipeline.settings s ON s.active_schema_version=r.schema_version
                WHERE r.vacancy_id=%s
            """, (vacancy_id,)).fetchone()
            if row is None:
                raise NotFoundError("ready vacancy not found")
            return self._entry(row)


def rebuild_for_schema(connection: psycopg.Connection, schema_version: int) -> None:
    """Atomically withdraw old vacancies and backfill every persisted snapshot on registry extension."""
    connection.execute("""INSERT INTO vacancy_pipeline.outbox
        (event_type,vacancy_id,revision,schema_version,payload)
        SELECT 'vacancy.withdrawn',vacancy_id,revision,schema_version,%s FROM vacancy_pipeline.ready
    """, (Jsonb({"reason": "strict_schema_changed"}),))
    connection.execute("DELETE FROM vacancy_pipeline.ready")
    connection.execute("""UPDATE vacancy_pipeline.jobs SET state='obsolete',lease_token=NULL,lease_until=NULL
        WHERE state IN ('queued','processing','retrying','failed')""")
    connection.execute("""INSERT INTO vacancy_pipeline.jobs(vacancy_id,revision,schema_version,state)
        SELECT vacancy_id,revision,%s,'queued' FROM vacancy_pipeline.vacancies""", (schema_version,))
    connection.execute("""UPDATE vacancy_pipeline.vacancies SET state=CASE
        WHEN missing_requirements='[]'::jsonb THEN 'queued' ELSE 'draft' END,updated_at=now()""")
