"""Durable, deterministic tail refill for immutable employer feed sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import strict_filter
from matching_engine.combine_store import CombineStore, _lock
from matching_engine.database import connect
from matching_engine.feed_models import FeedRefillStatus
from matching_engine.rerank_hash import digest
from matching_engine.store import ConflictError, NotFoundError
from matching_engine.vacancy_models import VacancyReady

POOL_SNAPSHOT_VERSION = "pool-n1-v1"
POOL_CHANGE_THRESHOLD = 0.10
FEED_LOW_WATER = 4
FEED_REFILL_BATCH = 12


class RefillNotReady(ConflictError):
    """A fresh main or rerank source is queued and the worker should retry."""


@dataclass(frozen=True)
class RefillJob:
    """Leased refill inputs; the worker never reads private profile text."""

    job_id: UUID
    lease_token: UUID
    session_id: UUID
    owner_id: UUID
    vacancy_id: UUID
    reason: str
    attempts: int


def pool_snapshot(connection: psycopg.Connection, vacancy: VacancyReady | None = None) -> list[dict[str, Any]]:
    """Return the vacancy's strict-compatible public pool identity used by N1.

    Candidate IDs, profile revisions and strict registry identities are enough to
    detect added, removed, or changed pool members.  Private profile fields never
    enter the durable snapshot.
    """
    rows = connection.execute("""
        SELECT p.candidate_id,p.revision,p.schema_version,p.schema_hash,
               p.specialization,p.grade,p.grade_rank,p.confirmed_strength
        FROM candidate_pipeline.pool p
        JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id
            AND c.revision=p.revision AND c.state='ready'
            AND (c.profile->'consents'->>'processing')::boolean IS TRUE
            AND (c.profile->'consents'->>'pool')::boolean IS TRUE
        JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
        JOIN candidate_pipeline.strict_schemas sc ON sc.version=p.schema_version
            AND sc.schema_hash=p.schema_hash
        ORDER BY p.candidate_id
    """).fetchall()
    snapshot = [{
        "candidate_id": str(row["candidate_id"]),
        "profile_revision": row["revision"],
        "schema_version": row["schema_version"],
        "schema_hash": row["schema_hash"],
        "specialization": row["specialization"],
        "grade": row["grade"],
        "grade_rank": row["grade_rank"],
        "confirmed_strength": row["confirmed_strength"],
    } for row in rows]
    if vacancy is None:
        return snapshot
    candidates = {candidate.candidate_id: candidate for candidate in CombineStore._candidates(connection)}
    return [entry for entry in snapshot if UUID(entry["candidate_id"]) in candidates and
            strict_filter(vacancy, candidates[UUID(entry["candidate_id"])]).passed]


def pool_snapshot_hash(snapshot: list[dict[str, Any]]) -> str:
    """Hash a canonical N1 snapshot so session provenance is auditable."""
    return digest({"version": POOL_SNAPSHOT_VERSION, "members": snapshot})


def pool_change_fraction(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> float:
    """Compute changed/added/removed members divided by the union size."""
    def identity(value: dict[str, Any]) -> tuple[str, int, int, str]:
        return (str(value["candidate_id"]), int(value["profile_revision"]),
                int(value["schema_version"]), str(value["schema_hash"]))

    old = {str(value["candidate_id"]): identity(value) for value in previous}
    new = {str(value["candidate_id"]): identity(value) for value in current}
    union = set(old) | set(new)
    if not union:
        return 0.0
    return sum(old.get(candidate) != new.get(candidate) for candidate in union) / len(union)


def _status(row: dict) -> FeedRefillStatus:
    """Map a database job row to its safe public status contract."""
    return FeedRefillStatus(job_id=row["job_id"], session_id=row["session_id"], vacancy_id=row["vacancy_id"],
                            reason=row["reason"], state=row["state"], attempt_count=row["attempts"],
                            batch_id=row["batch_id"], error_code=row["error_code"],
                            created_at=row["created_at"], completed_at=row["completed_at"])


class FeedRefillStore:
    """Persist deduplicated refill jobs and append immutable feed batches."""

    def __init__(self, database_url: str) -> None:
        """Use the same PostgreSQL database and lock ordering as the feed store."""
        self.database_url = database_url

    @staticmethod
    def _session(connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID, session_id: UUID) -> dict:
        row = connection.execute("""SELECT * FROM feed_pipeline.sessions
            WHERE session_id=%s AND owner_id=%s AND vacancy_id=%s FOR UPDATE""",
                                (session_id, owner_id, vacancy_id)).fetchone()
        if row is None:
            raise NotFoundError("feed session not found")
        return row

    @staticmethod
    def _remaining(connection: psycopg.Connection, session_id: UUID) -> int:
        """Count only unserved, undecided candidates still eligible for this vacancy."""
        row = connection.execute("""SELECT count(*) AS count FROM feed_pipeline.exposures e
            LEFT JOIN feed_pipeline.decisions d ON d.exposure_id=e.exposure_id
            LEFT JOIN matching_pipeline.selection_states ss ON ss.vacancy_id=e.vacancy_id
                AND ss.candidate_id=e.candidate_id
            LEFT JOIN feed_pipeline.saved s ON s.owner_id=e.owner_id AND s.vacancy_id=e.vacancy_id
                AND s.candidate_id=e.candidate_id
            WHERE e.session_id=%s AND e.served_at IS NULL AND d.exposure_id IS NULL
              AND s.candidate_id IS NULL AND (ss.state IS NULL OR ss.state IN ('eligible','visited_updated'))""",
                                     (session_id,)).fetchone()
        return int(row["count"])

    def enqueue(self, owner_id: UUID, vacancy_id: UUID, session_id: UUID,
                *, force: bool = False, reason: str | None = None) -> FeedRefillStatus | None:
        """Queue one low-water or N1 pool-change job under a row lock.

        The partial unique index is the final concurrency guard; two page requests
        can therefore safely race without creating duplicate work.
        """
        with connect(self.database_url) as connection:
            _lock(connection)
            return self.enqueue_locked(connection, owner_id, vacancy_id, session_id, force=force, reason=reason)

    def enqueue_locked(self, connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID,
                       session_id: UUID, *, force: bool = False,
                       reason: str | None = None) -> FeedRefillStatus | None:
        """Queue refill while the caller already owns the pipeline transaction lock."""
        session = self._session(connection, owner_id, vacancy_id, session_id)
        current = connection.execute("""SELECT v.business_status,v.state,v.revision,r.schema_version,r.schema_hash
            FROM vacancy_pipeline.vacancies v LEFT JOIN vacancy_pipeline.ready r
                ON r.vacancy_id=v.vacancy_id AND r.revision=v.revision
            WHERE v.vacancy_id=%s AND v.owner_id=%s""",
                                     (vacancy_id, owner_id)).fetchone()
        if current is None or current["business_status"] != "open" or current["state"] != "ready":
            raise ConflictError("vacancy_inactive")
        if (current["revision"] != session["vacancy_revision"]
                or current["schema_version"] != session["schema_version"]
                or current["schema_hash"] != session["schema_hash"]):
            raise ConflictError("feed vacancy revision is stale")
        if session["expires_at"] <= connection.execute("SELECT now() AS value").fetchone()["value"]:
            raise ConflictError("feed session expired")
        source = connection.execute("SELECT vacancy_snapshot FROM matching_pipeline.runs WHERE run_id=%s",
                                   (session["main_run_id"],)).fetchone()
        vacancy = None if source is None else VacancyReady.model_validate(source["vacancy_snapshot"])
        current = pool_snapshot(connection, vacancy)
        previous = list(session["pool_snapshot"] or [])
        fraction = pool_change_fraction(previous, current)
        low_water = self._remaining(connection, session_id) <= FEED_LOW_WATER
        pending = connection.execute("""SELECT * FROM feed_pipeline.refill_jobs
            WHERE session_id=%s AND state IN ('queued','running','retrying')
            ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
        if pending is not None:
            return _status(pending)
        if session["refill_state"] == "failed":
            failed = connection.execute("""SELECT requested_pool_snapshot FROM feed_pipeline.refill_jobs
                WHERE session_id=%s AND state='failed' ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
            if failed is not None:
                fraction = pool_change_fraction(list(failed["requested_pool_snapshot"] or []), current)
        if session["refill_state"] in ("exhausted", "failed") and fraction < POOL_CHANGE_THRESHOLD and not force:
            return None
        if not force and not low_water and fraction < POOL_CHANGE_THRESHOLD:
            return None
        if force:
            from matching_engine.rerank_store import RerankStore

            main = CombineStore(self.database_url)._queue(connection, vacancy_id)
            if main["state"] == "failed":
                connection.execute("""UPDATE matching_pipeline.runs SET state='queued',attempts=0,error_code=NULL,
                    available_at=now(),lease_token=NULL,lease_until=NULL,updated_at=now() WHERE run_id=%s""",
                                   (main["run_id"],))
            elif main["state"] == "ready":
                rerank = RerankStore.queue(connection, main)
                if rerank["state"] == "failed":
                    connection.execute("""UPDATE rerank_pipeline.runs SET state='queued',attempts=0,error_code=NULL,
                        available_at=now(),lease_token=NULL,lease_until=NULL WHERE run_id=%s""", (rerank["run_id"],))
        change = fraction >= POOL_CHANGE_THRESHOLD
        why = reason or ("low_water_pool_change" if low_water and change else "pool_change" if change else "low_water")
        if why not in ("low_water", "pool_change", "low_water_pool_change"):
            raise ValueError("invalid refill reason")
        row = connection.execute("""INSERT INTO feed_pipeline.refill_jobs
                (job_id,session_id,owner_id,vacancy_id,reason,requested_pool_snapshot_version,
                 requested_pool_snapshot,requested_pool_snapshot_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id) WHERE state IN ('queued','running','retrying') DO NOTHING
                RETURNING *""", (uuid4(), session_id, owner_id, vacancy_id, why, POOL_SNAPSHOT_VERSION,
                              Jsonb(current), pool_snapshot_hash(current))).fetchone()
        if row is None:
            row = connection.execute("""SELECT * FROM feed_pipeline.refill_jobs
                    WHERE session_id=%s AND state IN ('queued','running','retrying')
                    ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
        connection.execute("UPDATE feed_pipeline.sessions SET refill_state='queued' WHERE session_id=%s", (session_id,))
        return _status(row)

    def enqueue_vacancy_locked(self, connection: psycopg.Connection, vacancy_id: UUID) -> None:
        """React to pool events for live sessions without waiting for a page request.

        The N1/low-water guards and partial unique index remain authoritative;
        inactive, superseded and expired sessions never receive new work.
        """
        sessions = connection.execute("""SELECT s.session_id,s.owner_id FROM feed_pipeline.sessions s
            JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=s.vacancy_id AND v.owner_id=s.owner_id
                AND v.revision=s.vacancy_revision AND v.state='ready' AND v.business_status='open'
            JOIN vacancy_pipeline.ready r ON r.vacancy_id=v.vacancy_id AND r.revision=v.revision
                AND r.schema_version=s.schema_version AND r.schema_hash=s.schema_hash
            WHERE s.vacancy_id=%s AND s.expires_at>clock_timestamp()
                AND s.source<>'showcase' ORDER BY s.session_id""", (vacancy_id,)).fetchall()
        for session in sessions:
            self.enqueue_locked(connection, session["owner_id"], vacancy_id, session["session_id"])

    def status(self, owner_id: UUID, vacancy_id: UUID, session_id: UUID) -> FeedRefillStatus | None:
        """Read the latest refill job for an owned session."""
        with connect(self.database_url) as connection:
            self._session(connection, owner_id, vacancy_id, session_id)
            row = connection.execute("""SELECT * FROM feed_pipeline.refill_jobs
                WHERE session_id=%s ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
            return None if row is None else _status(row)

    def claim(self, lease_seconds: int = 300, max_attempts: int = 5) -> RefillJob | None:
        """Claim one queued or expired job with a restart-safe lease."""
        if lease_seconds < 3 or max_attempts < 1:
            raise ValueError("invalid refill worker configuration")
        with connect(self.database_url) as connection:
            _lock(connection)
            connection.execute("""UPDATE feed_pipeline.refill_jobs SET state='failed',error_code='lease_exhausted',
                lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE state='running' AND lease_until<=clock_timestamp() AND attempts>=%s""", (max_attempts,))
            connection.execute("""UPDATE feed_pipeline.sessions s SET refill_state='failed'
                WHERE EXISTS (SELECT 1 FROM feed_pipeline.refill_jobs j WHERE j.session_id=s.session_id
                    AND j.state='failed') AND NOT EXISTS (SELECT 1 FROM feed_pipeline.refill_jobs j
                    WHERE j.session_id=s.session_id AND j.state IN ('queued','running','retrying'))
                    AND s.refill_state IN ('queued','running')""")
            row = connection.execute("""SELECT * FROM feed_pipeline.refill_jobs
                WHERE ((state IN ('queued','retrying') AND available_at<=clock_timestamp())
                    OR (state='running' AND lease_until<=clock_timestamp())) AND attempts<%s
                ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1""", (max_attempts,)).fetchone()
            if row is None:
                return None
            token = uuid4()
            row = connection.execute("""UPDATE feed_pipeline.refill_jobs SET state='running',attempts=attempts+1,
                lease_token=%s,lease_until=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp()
                WHERE job_id=%s RETURNING *""", (token, lease_seconds, row["job_id"])).fetchone()
            connection.execute("UPDATE feed_pipeline.sessions SET refill_state='running' WHERE session_id=%s", (row["session_id"],))
            return RefillJob(row["job_id"], token, row["session_id"], row["owner_id"], row["vacancy_id"], row["reason"], row["attempts"])

    @staticmethod
    def _blocked(connection: psycopg.Connection, session: dict) -> set[UUID]:
        rows = connection.execute("""SELECT candidate_id FROM feed_pipeline.exposures WHERE session_id=%s
            UNION SELECT candidate_id FROM feed_pipeline.saved WHERE owner_id=%s AND vacancy_id=%s
            UNION SELECT candidate_id FROM matching_pipeline.selection_states
                WHERE vacancy_id=%s AND state IN ('visited','pending','attractive')""",
                                 (session["session_id"], session["owner_id"], session["vacancy_id"], session["vacancy_id"])).fetchall()
        return {row["candidate_id"] for row in rows}

    @staticmethod
    def _queue_fresh(connection: psycopg.Connection, vacancy_id: UUID, main: dict | None) -> None:
        """Queue the next combine/rerank pair while holding the pipeline lock."""
        from matching_engine.rerank_store import RerankStore

        if main is None or main["state"] != "ready" or not CombineStore._valid(connection, main):
            policy = None if main is None else ScoringPolicy.model_validate(main["policy"])
            CombineStore("", policy=policy)._queue(connection, vacancy_id)
            return
        RerankStore.queue(connection, main)

    @staticmethod
    def _main_snapshot_live(connection: psycopg.Connection, main: dict, session: dict) -> bool:
        """Validate an immutable session main without requiring it to be current."""
        if main["state"] not in ("ready", "obsolete") or main["vacancy_id"] != session["vacancy_id"] \
                or main["vacancy_revision"] != session["vacancy_revision"] \
                or main["schema_version"] != session["schema_version"] \
                or main["schema_hash"] != session["schema_hash"] \
                or main["policy_hash"] != session["main_policy_hash"]:
            return False
        actual = connection.execute("""SELECT p.candidate_id,p.revision FROM candidate_pipeline.pool p
            JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id AND c.revision=p.revision
                AND c.state='ready'
            JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=p.schema_version AND sc.schema_hash=p.schema_hash""").fetchall()
        expected = {(UUID(item["candidate_id"]), item["profile_revision"]) for item in main["candidates_snapshot"]}
        return expected == {(item["candidate_id"], item["revision"]) for item in actual}

    @classmethod
    def _source_runs(cls, connection: psycopg.Connection, session: dict) -> tuple[dict, dict]:
        """Return one ready, coherent main→rerank pair or enqueue its replacement.

        A rerank run is usable only when its foreign key points to the exact main
        run used for the batch.  This prevents a current rerank control row from
        being combined with an older session main snapshot.
        """
        from matching_engine.rerank_store import RerankStore

        session_main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s",
                                           (session["main_run_id"],)).fetchone()
        session_rerank = connection.execute("SELECT * FROM rerank_pipeline.runs WHERE run_id=%s",
                                            (session["rerank_run_id"],)).fetchone() if session.get("rerank_run_id") else None
        settings = connection.execute("SELECT active_artifact_hash,activation_revision FROM rerank_pipeline.settings WHERE singleton").fetchone()
        main = connection.execute("""SELECT r.* FROM matching_pipeline.controls c
            JOIN matching_pipeline.runs r ON r.run_id=c.current_run_id
            WHERE c.vacancy_id=%s""", (session["vacancy_id"],)).fetchone()
        rerank = connection.execute("""SELECT r.* FROM rerank_pipeline.controls c
            JOIN rerank_pipeline.runs r ON r.run_id=c.current_run_id
            WHERE c.vacancy_id=%s""", (session["vacancy_id"],)).fetchone()
        if (main is not None and main["state"] == "ready" and CombineStore._valid(connection, main)
                and rerank is not None and rerank["state"] == "ready"
                and rerank["main_run_id"] == main["run_id"] and RerankStore._valid(connection, rerank)):
            return main, rerank

        if (session_main is not None and session_rerank is not None
                and cls._main_snapshot_live(connection, session_main, session)
                and session_rerank["state"] == "ready" and session_rerank["main_run_id"] == session_main["run_id"]
                and session_rerank["artifact_hash"] == session.get("artifact_hash")
                and session_rerank["feature_schema_hash"] == session.get("feature_schema_hash")
                and session_rerank["tail_policy_hash"] == session.get("tail_policy_hash")
                and session_rerank["activation_revision"] == session.get("activation_revision")
                and settings is not None and settings["active_artifact_hash"] == session.get("artifact_hash")
                and settings["activation_revision"] == session.get("activation_revision")):
            return session_main, session_rerank

        cls._queue_fresh(connection, session["vacancy_id"], main)
        raise RefillNotReady("coherent feed source is not ready")

    @classmethod
    def _cards(cls, connection: psycopg.Connection, session: dict, blocked: set[UUID]) -> tuple[list[dict], dict, dict | None]:
        """Build deterministic cards from real main/rerank snapshots, without an LLM."""
        main, rerank = cls._source_runs(connection, session)
        vacancy = VacancyReady.model_validate(main["vacancy_snapshot"])
        allowed = {(entry["candidate_id"], entry["profile_revision"])
                   for entry in pool_snapshot(connection, vacancy)}
        selections = {}
        for row in connection.execute(
                "SELECT candidate_id,position,card FROM matching_pipeline.selections WHERE run_id=%s ORDER BY position",
                (main["run_id"],)):
            row["profile_revision"] = row["card"]["profile_revision"]
            selections[row["candidate_id"]] = row
        cards: list[dict] = []
        for row in connection.execute("SELECT * FROM rerank_pipeline.cards WHERE run_id=%s ORDER BY position",
                                      (rerank["run_id"],)):
            selection = selections.get(row["candidate_id"])
            if selection is None or selection["profile_revision"] != row["profile_revision"]:
                raise RefillNotReady("rerank card is not a main selection")
            card = dict(row["card"])
            if card.get("main_position") != selection["position"]:
                raise RefillNotReady("rerank card main position is stale")
            if row["candidate_id"] in blocked:
                continue
            if (str(row["candidate_id"]), row["profile_revision"]) not in allowed:
                continue
            card["position"] = row["position"]
            card["kind"] = row["kind"]
            cards.append(card)
        current_main = connection.execute("SELECT current_run_id FROM matching_pipeline.controls WHERE vacancy_id=%s",
                                          (session["vacancy_id"],)).fetchone()
        if not cards and (current_main is None or current_main["current_run_id"] != main["run_id"]):
            cls._queue_fresh(connection, session["vacancy_id"],
                             None if current_main is None else connection.execute(
                                 "SELECT * FROM matching_pipeline.runs WHERE run_id=%s",
                                 (current_main["current_run_id"],)).fetchone())
            raise RefillNotReady("current feed source has no unseen cards")
        return cards, main, rerank

    def publish(self, job: RefillJob) -> bool:
        """Append one complete tail batch iff the leased job and session remain live."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = connection.execute("SELECT * FROM feed_pipeline.refill_jobs WHERE job_id=%s FOR UPDATE", (job.job_id,)).fetchone()
            now = connection.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            if row is None or row["state"] != "running" or row["lease_token"] != job.lease_token or row["lease_until"] <= now:
                return False
            session = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE session_id=%s FOR UPDATE", (job.session_id,)).fetchone()
            if session is None or session["expires_at"] <= now:
                connection.execute("UPDATE feed_pipeline.refill_jobs SET state='stale',error_code='session_expired' WHERE job_id=%s", (job.job_id,))
                return False
            current_vacancy = connection.execute("""SELECT revision,state,business_status FROM vacancy_pipeline.vacancies
                WHERE vacancy_id=%s""", (session["vacancy_id"],)).fetchone()
            if (current_vacancy is None or current_vacancy["revision"] != session["vacancy_revision"]
                    or current_vacancy["state"] != "ready" or current_vacancy["business_status"] != "open"):
                connection.execute("UPDATE feed_pipeline.refill_jobs SET state='stale',error_code='session_source_changed',lease_token=NULL,lease_until=NULL WHERE job_id=%s", (job.job_id,))
                return False
            blocked = self._blocked(connection, session)
            try:
                cards, main, rerank = self._cards(connection, session, blocked)
            except RefillNotReady:
                # Source queue intents must survive this expected deferred result.
                # No batch/exposure has been written at this point.
                connection.commit()
                raise
            cards = cards[:FEED_REFILL_BATCH]
            current_snapshot = pool_snapshot(connection, VacancyReady.model_validate(main["vacancy_snapshot"]))
            current_hash = pool_snapshot_hash(current_snapshot)
            batch_no = connection.execute("SELECT COALESCE(max(batch_number),0)+1 AS value FROM feed_pipeline.batches WHERE session_id=%s", (job.session_id,)).fetchone()["value"]
            batch_id = uuid4()
            connection.execute("""INSERT INTO feed_pipeline.batches
                (batch_id,session_id,owner_id,vacancy_id,batch_number,reason,main_run_id,rerank_run_id,
                 pool_generation,pool_snapshot_version,pool_snapshot,pool_snapshot_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                batch_id, job.session_id, session["owner_id"], session["vacancy_id"], batch_no, job.reason,
                main["run_id"], rerank["run_id"], main["pool_generation"],
                POOL_SNAPSHOT_VERSION, Jsonb(current_snapshot), current_hash))
            next_position = connection.execute("SELECT COALESCE(max(position),0)+1 AS value FROM feed_pipeline.exposures WHERE session_id=%s", (job.session_id,)).fetchone()["value"]
            for offset, card in enumerate(cards):
                exposure_id = uuid4()
                card = {**card, "position": next_position + offset, "batch_id": str(batch_id),
                        "source_main_run_id": str(main["run_id"]), "source_rerank_run_id": str(rerank["run_id"]),
                        "source_pool_generation": main["pool_generation"]}
                connection.execute("""INSERT INTO feed_pipeline.exposures
                    (exposure_id,session_id,owner_id,vacancy_id,candidate_id,profile_revision,position,kind,card,batch_id,
                     source_main_run_id,source_rerank_run_id,source_pool_generation)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                    exposure_id, job.session_id, session["owner_id"], session["vacancy_id"], UUID(card["candidate_id"]),
                    card["profile_revision"], next_position + offset, card.get("kind", "reserve"), Jsonb(card), batch_id,
                    main["run_id"], rerank["run_id"], main["pool_generation"]))
                from matching_engine.learning_store import LearningStore
                LearningStore.event(connection, {"exposure_id": exposure_id, "owner_id": session["owner_id"], "vacancy_id": session["vacancy_id"]}, "allocated")
            state = "active" if cards else "exhausted"
            connection.execute("""UPDATE feed_pipeline.sessions SET pool_snapshot_version=%s,pool_snapshot=%s,
                pool_snapshot_hash=%s,refill_state=%s,exhausted_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                WHERE session_id=%s""", (POOL_SNAPSHOT_VERSION, Jsonb(current_snapshot), current_hash, state, not cards, job.session_id))
            connection.execute("""UPDATE feed_pipeline.refill_jobs SET state='ready',batch_id=%s,completed_at=clock_timestamp(),
                lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=%s""", (batch_id, job.job_id))
            return True

    def heartbeat(self, job: RefillJob, lease_seconds: int = 300) -> bool:
        """Renew an owned refill lease."""
        with connect(self.database_url) as connection:
            return connection.execute("""UPDATE feed_pipeline.refill_jobs SET lease_until=clock_timestamp()+(%s*interval '1 second')
                WHERE job_id=%s AND state='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING job_id""",
                                     (lease_seconds, job.job_id, job.lease_token)).fetchone() is not None

    def fail(self, job: RefillJob, error_code: str, max_attempts: int = 5, retry_base_seconds: int = 30) -> None:
        """Persist a bounded retry without exposing worker input or exception text."""
        with connect(self.database_url) as connection:
            row = connection.execute("""UPDATE feed_pipeline.refill_jobs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'retrying' END,
                error_code=%s,available_at=clock_timestamp()+(%s*power(2,least(attempts-1,10))*interval '1 second'),
                lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE job_id=%s AND state='running' AND lease_token=%s RETURNING state,session_id""",
                (max_attempts, error_code, retry_base_seconds, job.job_id, job.lease_token)).fetchone()
            if row is not None:
                connection.execute("UPDATE feed_pipeline.sessions SET refill_state=%s WHERE session_id=%s",
                                   ("failed" if row["state"] == "failed" else "queued", row["session_id"]))

    def defer(self, job: RefillJob, error_code: str, retry_base_seconds: int = 30,
              max_attempts: int = 5) -> None:
        """Bound source-readiness retries, exposing terminal failure without a busy loop."""
        self.fail(job, error_code, max_attempts, retry_base_seconds)
