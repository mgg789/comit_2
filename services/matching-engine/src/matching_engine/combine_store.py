"""Atomic outbox delivery and immutable, leased main-selection runs in PostgreSQL."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import (
    CandidateCard,
    CombineRequest,
    CombineStatus,
    DeliveryStatus,
    MainSelectionResult,
    MatchCandidate,
    ScoringPolicy,
    SelectionPage,
    SelectionState,
    SelectionStateInput,
    project_narrative_facts,
)
from matching_engine.combine_scoring import policy_hash
from matching_engine.database import PIPELINE_LOCK, connect
from matching_engine.embeddings import EMBEDDING_SPACE_ID, MODEL_VERSION
from matching_engine.models import Category, NarrativeClaim
from matching_engine.store import ConflictError, NotFoundError
from matching_engine.vacancy_models import VacancyReady
from matching_engine.vacancy_store import VacancyStore


@dataclass(frozen=True)
class CombineJob:
    """Immutable contact-free inputs captured when a durable run was queued."""

    run_id: UUID
    lease_token: UUID
    attempts: int
    vacancy: VacancyReady
    candidates: list[MatchCandidate]
    excluded_ids: set[UUID]
    policy: ScoringPolicy
    top_k: int


def _lock(connection: psycopg.Connection) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))


class CombineStore:
    """Persist current batches and deliver events without acknowledging lost downstream work."""

    def __init__(self, database_url: str, policy: ScoringPolicy | None = None) -> None:
        """Use the shared pipeline database and snapshot the manual policy into every run."""
        self.database_url = database_url
        self.policy = (policy or ScoringPolicy()).model_copy(deep=True)

    def ready(self) -> bool:
        """Check matching migrations without changing schema or dispatching pending work."""
        try:
            with connect(self.database_url) as connection:
                return connection.execute("""SELECT to_regclass('matching_pipeline.runs') IS NOT NULL
                    AND to_regclass('matching_pipeline.inbox') IS NOT NULL
                    AND EXISTS (SELECT 1 FROM matching_pipeline.settings) AS ready""").fetchone()["ready"]
        except psycopg.Error:
            return False

    @staticmethod
    def _vacancy(connection: psycopg.Connection, vacancy_id: UUID) -> VacancyReady:
        row = connection.execute("""
            SELECT r.*,v.profile,r.semantic_vector::text AS vector FROM vacancy_pipeline.ready r
            JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=r.vacancy_id AND v.revision=r.revision
                AND v.state='ready' AND v.business_status='open'
            JOIN candidate_pipeline.settings s ON s.active_schema_version=r.schema_version
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=r.schema_version AND sc.schema_hash=r.schema_hash
            WHERE r.vacancy_id=%s
        """, (vacancy_id,)).fetchone()
        if row is None:
            raise NotFoundError("ready vacancy not found")
        return VacancyStore._entry(row)

    @staticmethod
    def _candidates(connection: psycopg.Connection) -> list[MatchCandidate]:
        # Window functions cover the entire ready class before any strict filter.
        # Preferences are individually projected; private text/contacts never enter a run.
        rows = connection.execute("""
            WITH ready AS (
                SELECT p.*,c.profile->'work_types' AS work_types,
                    jsonb_build_object('projects',c.profile->'projects',
                        'experience',c.profile->'experience','achievements',c.profile->'achievements') AS narratives,
                    c.profile->'relocation_ready' AS relocation_ready,
                    c.profile->'relocation_cities' AS relocation_cities,
                    row_number() OVER (PARTITION BY p.specialization
                        ORDER BY p.grade_rank DESC,p.confirmed_strength DESC,p.candidate_id) AS rank,
                    rank() OVER (PARTITION BY p.specialization
                        ORDER BY p.grade_rank DESC,p.confirmed_strength DESC) AS rating_rank,
                    count(*) OVER (PARTITION BY p.specialization) AS class_size
                FROM candidate_pipeline.pool p
                JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id
                    AND c.revision=p.revision AND c.state='ready'
                JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
                JOIN candidate_pipeline.strict_schemas sc ON sc.version=p.schema_version AND sc.schema_hash=p.schema_hash
            ) SELECT *,semantic_vector::text AS vector FROM ready ORDER BY specialization,rank
        """).fetchall()
        return [MatchCandidate(
            candidate_id=row["candidate_id"], profile_revision=row["revision"],
            display_name=row["public_profile"]["display_name"], city=row["public_profile"]["city"],
            salary=row["public_profile"]["salary"],
            category=Category(specialization=row["specialization"], grade=row["grade"], grade_status=row["grade_status"]),
            rank=row["rank"], class_size=row["class_size"],
            rating_score=1.0 if row["class_size"] == 1 else 1 - (row["rating_rank"] - 1) / (row["class_size"] - 1),
            confirmed_strength=row["confirmed_strength"], strict_vector=row["strict_vector"],
            strict_evidence=row["strict_evidence"], strict_schema_version=row["schema_version"],
            strict_schema_hash=row["schema_hash"], semantic_vector=json.loads(row["vector"]),
            semantic_model_version=row["semantic_model_version"],
            # Candidate schema 001 persists the role fingerprint, not a space column.
            # Only its exact known document pipeline maps to our current query space.
            semantic_space_id=EMBEDDING_SPACE_ID if row["semantic_model_version"] == MODEL_VERSION
                else f"unknown-document-space:{row['semantic_model_version']}",
            published_at=row["published_at"], resume_url=f"/internal/pool/{row['candidate_id']}/resume.pdf",
            work_types=row["work_types"], relocation_ready=row["relocation_ready"],
            relocation_cities=row["relocation_cities"] or [],
            narrative_facts=project_narrative_facts({key: [NarrativeClaim.model_validate(claim)
                for claim in (claims or [])] for key, claims in row["narratives"].items()}),
        ) for row in rows]

    @staticmethod
    def _status(row: dict, *, obsolete: bool = False) -> CombineStatus:
        return CombineStatus(
            run_id=row["run_id"], vacancy_id=row["vacancy_id"], vacancy_revision=row["vacancy_revision"],
            strict_schema_version=row["schema_version"], strict_schema_hash=row["schema_hash"],
            pool_generation=row["pool_generation"], policy_version=row["policy_version"],
            policy_hash=row["policy_hash"], state="obsolete" if obsolete else row["state"],
            top_k=row["top_k"], attempt_count=row["attempts"], error_code=row["error_code"],
            scanned_count=row["scanned_count"], passed_count=row["passed_count"],
            selected_count=row["selected_count"], filter_counts=row["filter_counts"],
            published_at=row["published_at"],
        )

    @staticmethod
    def _current(connection: psycopg.Connection, vacancy_id: UUID) -> dict | None:
        return connection.execute("""SELECT r.* FROM matching_pipeline.controls c
            JOIN matching_pipeline.runs r ON r.run_id=c.current_run_id WHERE c.vacancy_id=%s""",
            (vacancy_id,)).fetchone()

    @staticmethod
    def _valid(connection: psycopg.Connection, row: dict, policy: ScoringPolicy | None = None) -> bool:
        if row["policy_hash"] != policy_hash(policy or ScoringPolicy()):
            return False
        metadata = connection.execute("""
            SELECT 1 FROM matching_pipeline.controls c
            JOIN matching_pipeline.settings ms ON ms.pool_generation=%s
            JOIN candidate_pipeline.settings cs ON cs.active_schema_version=%s
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=cs.active_schema_version AND sc.schema_hash=%s
            JOIN vacancy_pipeline.ready vr ON vr.vacancy_id=c.vacancy_id AND vr.revision=%s
                AND vr.schema_version=sc.version AND vr.schema_hash=sc.schema_hash
            JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=vr.vacancy_id AND v.revision=vr.revision
                AND v.state='ready' AND v.business_status='open'
            WHERE c.vacancy_id=%s AND c.revision=%s AND c.current_run_id=%s AND c.top_k=%s
        """, (row["pool_generation"], row["schema_version"], row["schema_hash"], row["vacancy_revision"],
              row["vacancy_id"], row["control_revision"], row["run_id"], row["top_k"])).fetchone()
        if metadata is None:
            return False
        actual = connection.execute("""SELECT p.candidate_id,p.revision FROM candidate_pipeline.pool p
            JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id
                AND c.revision=p.revision AND c.state='ready'
            JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=p.schema_version AND sc.schema_hash=p.schema_hash""").fetchall()
        expected = {(UUID(item["candidate_id"]), item["profile_revision"]) for item in row["candidates_snapshot"]}
        return expected == {(item["candidate_id"], item["revision"]) for item in actual}

    def _queue(self, connection: psycopg.Connection, vacancy_id: UUID) -> dict:
        vacancy = self._vacancy(connection, vacancy_id)
        connection.execute("INSERT INTO matching_pipeline.controls(vacancy_id) VALUES (%s) ON CONFLICT DO NOTHING",
                           (vacancy_id,))
        control = connection.execute("SELECT * FROM matching_pipeline.controls WHERE vacancy_id=%s", (vacancy_id,)).fetchone()
        generation = connection.execute("SELECT pool_generation FROM matching_pipeline.settings").fetchone()["pool_generation"]
        fingerprint = policy_hash(self.policy)
        existing = connection.execute("""SELECT * FROM matching_pipeline.runs WHERE vacancy_id=%s
            AND vacancy_revision=%s AND schema_version=%s AND pool_generation=%s AND policy_hash=%s
            AND top_k=%s AND control_revision=%s""", (vacancy_id, vacancy.revision, vacancy.strict_schema_version,
                generation, fingerprint, control["top_k"], control["revision"])).fetchone()
        if existing:
            connection.execute("UPDATE matching_pipeline.controls SET current_run_id=%s WHERE vacancy_id=%s",
                               (existing["run_id"], vacancy_id))
            return existing
        candidates = self._candidates(connection)
        excluded = connection.execute("""SELECT candidate_id FROM matching_pipeline.selection_states
            WHERE vacancy_id=%s AND state IN ('visited','pending','attractive')""", (vacancy_id,)).fetchall()
        row = connection.execute("""INSERT INTO matching_pipeline.runs
            (run_id,vacancy_id,vacancy_revision,schema_version,schema_hash,pool_generation,
             policy_version,policy_hash,policy,top_k,control_revision,vacancy_snapshot,candidates_snapshot,excluded_ids,state)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING *""",
            (uuid4(), vacancy_id, vacancy.revision, vacancy.strict_schema_version, vacancy.strict_schema_hash,
             generation, self.policy.version, fingerprint, Jsonb(self.policy.model_dump(mode="json")),
             control["top_k"], control["revision"], Jsonb(vacancy.model_dump(mode="json", by_alias=True)),
             Jsonb([candidate.model_dump(mode="json") for candidate in candidates]),
             Jsonb([str(item["candidate_id"]) for item in excluded]))).fetchone()
        connection.execute("""UPDATE matching_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL,
            updated_at=now() WHERE vacancy_id=%s AND run_id<>%s AND state<>'obsolete'""", (vacancy_id, row["run_id"]))
        connection.execute("UPDATE matching_pipeline.controls SET current_run_id=%s WHERE vacancy_id=%s",
                           (row["run_id"], vacancy_id))
        return row

    def request(self, vacancy_id: UUID, request: CombineRequest) -> CombineStatus:
        """Persist top-k and queue one exact snapshot; caller keys remain stable across retries/restarts."""
        with connect(self.database_url) as connection:
            _lock(connection)
            if request.idempotency_key is not None:
                old = connection.execute("SELECT * FROM matching_pipeline.manual_requests WHERE idempotency_key=%s",
                                         (request.idempotency_key,)).fetchone()
                if old:
                    if old["vacancy_id"] != vacancy_id or old["top_k"] != request.top_k:
                        raise ConflictError("idempotency key was used with a different request")
                    row = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (old["run_id"],)).fetchone()
                    return self._status(row, obsolete=not self._valid(connection, row, self.policy))
            self._vacancy(connection, vacancy_id)
            connection.execute("INSERT INTO matching_pipeline.controls(vacancy_id) VALUES (%s) ON CONFLICT DO NOTHING", (vacancy_id,))
            connection.execute("""UPDATE matching_pipeline.controls SET top_k=%s,revision=revision+1
                WHERE vacancy_id=%s AND top_k<>%s""", (request.top_k, vacancy_id, request.top_k))
            row = self._queue(connection, vacancy_id)
            if request.idempotency_key is not None:
                connection.execute("""INSERT INTO matching_pipeline.manual_requests(idempotency_key,vacancy_id,top_k,run_id)
                    VALUES (%s,%s,%s,%s)""", (request.idempotency_key, vacancy_id, request.top_k, row["run_id"]))
            return self._status(row)

    def status(self, vacancy_id: UUID) -> CombineStatus:
        """Report obsolete when current inputs differ, even before outbox delivery completes."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = self._current(connection, vacancy_id)
            if row is None:
                raise NotFoundError("combine run not found")
            return self._status(row, obsolete=not self._valid(connection, row, self.policy))

    def selections(self, vacancy_id: UUID, limit: int, offset: int) -> SelectionPage:
        """Read one valid ready batch atomically; never serve partial or stale cards."""
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid selection pagination")
        with connect(self.database_url) as connection:
            _lock(connection)
            self._vacancy(connection, vacancy_id)
            row = self._current(connection, vacancy_id)
            if row is None or row["state"] != "ready" or not self._valid(connection, row, self.policy):
                raise ConflictError("current selection is not ready")
            cards = connection.execute("""SELECT card FROM matching_pipeline.selections WHERE run_id=%s
                ORDER BY position LIMIT %s OFFSET %s""", (row["run_id"], limit, offset)).fetchall()
            return SelectionPage(run=self._status(row), items=[CandidateCard.model_validate(item["card"]) for item in cards],
                                 limit=limit, offset=offset)

    def set_selection_state(self, vacancy_id: UUID, candidate_id: UUID,
                            selection: SelectionStateInput) -> SelectionState:
        """Persist trusted exclusion handoff; structural feed evaluation alone can release visited."""
        with connect(self.database_url) as connection:
            _lock(connection)
            self._vacancy(connection, vacancy_id)
            if not connection.execute("SELECT 1 FROM candidate_pipeline.candidates WHERE candidate_id=%s", (candidate_id,)).fetchone():
                raise NotFoundError("candidate not found")
            old = connection.execute("SELECT state FROM matching_pipeline.selection_states WHERE vacancy_id=%s AND candidate_id=%s",
                                     (vacancy_id, candidate_id)).fetchone()
            if old and old["state"] == "visited" and selection.state != "visited":
                raise ConflictError("visited candidate cannot be released")
            if old is None or old["state"] != selection.state:
                connection.execute("""INSERT INTO matching_pipeline.selection_states(vacancy_id,candidate_id,state)
                    VALUES (%s,%s,%s) ON CONFLICT (vacancy_id,candidate_id) DO UPDATE SET state=EXCLUDED.state,updated_at=now()""",
                    (vacancy_id, candidate_id, selection.state))
                if selection.state == "visited":
                    from matching_engine.feed_store import FeedStore
                    FeedStore(self.database_url).seed_legacy_manualvisited(connection)
                connection.execute("INSERT INTO matching_pipeline.controls(vacancy_id) VALUES (%s) ON CONFLICT DO NOTHING", (vacancy_id,))
                connection.execute("UPDATE matching_pipeline.controls SET revision=revision+1 WHERE vacancy_id=%s", (vacancy_id,))
                self._queue(connection, vacancy_id)
            return SelectionState(vacancy_id=vacancy_id, candidate_id=candidate_id, state=selection.state)

    def retry(self, vacancy_id: UUID) -> CombineStatus:
        """Explicitly reset terminal attempts, or replace obsolete inputs with a fresh run."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = self._queue(connection, vacancy_id)
            if row["state"] == "failed":
                row = connection.execute("""UPDATE matching_pipeline.runs SET state='queued',attempts=0,error_code=NULL,
                    available_at=now(),lease_token=NULL,lease_until=NULL,updated_at=now() WHERE run_id=%s RETURNING *""",
                    (row["run_id"],)).fetchone()
            return self._status(row)

    def claim(self, lease_seconds: int, max_attempts: int) -> CombineJob | None:
        """Claim or recover one available run; final expired attempts become visible failures."""
        with connect(self.database_url) as connection:
            _lock(connection)
            connection.execute("""UPDATE matching_pipeline.runs SET state='failed',error_code='lease_exhausted',
                lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE state='processing' AND lease_until<=now() AND attempts>=%s""", (max_attempts,))
            while True:
                row = connection.execute("""SELECT * FROM matching_pipeline.runs
                    WHERE ((state IN ('queued','retrying') AND available_at<=now())
                        OR (state='processing' AND lease_until<=now())) AND attempts<%s
                    ORDER BY created_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1""", (max_attempts,)).fetchone()
                if row is None:
                    return None
                if not self._valid(connection, row, self.policy):
                    connection.execute("UPDATE matching_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL WHERE run_id=%s", (row["run_id"],))
                    try:
                        self._queue(connection, row["vacancy_id"])
                    except NotFoundError:
                        pass
                    continue
                token = uuid4()
                connection.execute("""UPDATE matching_pipeline.runs SET state='processing',attempts=attempts+1,
                    lease_token=%s,lease_until=now()+(%s*interval '1 second'),updated_at=now() WHERE run_id=%s""",
                    (token, lease_seconds, row["run_id"]))
                return CombineJob(row["run_id"], token, row["attempts"] + 1,
                    VacancyReady.model_validate(row["vacancy_snapshot"]),
                    [MatchCandidate.model_validate(value) for value in row["candidates_snapshot"]],
                    {UUID(value) for value in row["excluded_ids"]}, ScoringPolicy.model_validate(row["policy"]), row["top_k"])

    def heartbeat(self, job: CombineJob, lease_seconds: int) -> bool:
        """Renew an unexpired owned lease; stolen or obsolete jobs remain invalid."""
        with connect(self.database_url) as connection:
            return connection.execute("""UPDATE matching_pipeline.runs SET lease_until=now()+(%s*interval '1 second')
                WHERE run_id=%s AND state='processing' AND lease_token=%s AND lease_until>now() RETURNING run_id""",
                (lease_seconds, job.run_id, job.lease_token)).fetchone() is not None

    def publish(self, job: CombineJob, result: MainSelectionResult) -> bool:
        """Publish an entire contact-free batch iff lease and all live snapshot identities still match."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = connection.execute("""SELECT *,lease_until>now() AS lease_live FROM matching_pipeline.runs WHERE run_id=%s""", (job.run_id,)).fetchone()
            if row is None or row["state"] != "processing" or row["lease_token"] != job.lease_token or not row["lease_live"]:
                return False
            if not self._valid(connection, row, self.policy):
                connection.execute("""UPDATE matching_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL,
                    updated_at=now() WHERE run_id=%s""", (job.run_id,))
                try:
                    self._queue(connection, row["vacancy_id"])
                except NotFoundError:
                    pass
                return False
            expected = {candidate.candidate_id: candidate.profile_revision for candidate in job.candidates}
            if result.scanned_count != len(expected) or len(result.items) > job.top_k:
                raise ValueError("selection result does not match snapshot")
            for position, card in enumerate(result.items, start=1):
                if card.position != position or expected.get(card.candidate_id) != card.profile_revision or card.candidate_id in job.excluded_ids:
                    raise ValueError("selection card does not match snapshot")
                connection.execute("INSERT INTO matching_pipeline.selections(run_id,candidate_id,position,card) VALUES (%s,%s,%s,%s)",
                    (job.run_id, card.candidate_id, card.position, Jsonb(card.model_dump(mode="json"))))
            connection.execute("""UPDATE matching_pipeline.runs SET state='ready',scanned_count=%s,passed_count=%s,
                selected_count=%s,filter_counts=%s,published_at=now(),updated_at=now(),lease_token=NULL,lease_until=NULL,error_code=NULL
                WHERE run_id=%s""", (result.scanned_count, result.passed_count, len(result.items), Jsonb(result.filter_counts), job.run_id))
            from matching_engine.rerank_store import RerankStore
            published = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (job.run_id,)).fetchone()
            from matching_engine.learning_store import LearningStore
            LearningStore.capture_main(connection, published)
            RerankStore.queue(connection, published)
            return True

    def fail(self, job: CombineJob, error_code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Persist bounded retry/backoff under the still-owned lease; error codes contain no input data."""
        with connect(self.database_url) as connection:
            connection.execute("""UPDATE matching_pipeline.runs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'retrying' END,
                error_code=%s,available_at=now()+(%s*power(2,least(attempts-1,10))*interval '1 second'),
                lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE run_id=%s AND state='processing' AND lease_token=%s AND lease_until>now()""",
                (max_attempts, error_code, retry_base_seconds, job.run_id, job.lease_token))

    def _deliver(self, connection: psycopg.Connection, source: str, event: dict) -> None:
        if event["event_type"] not in (f"{source}.ready", f"{source}.withdrawn"):
            raise ValueError("unsupported event")
        if connection.execute("SELECT 1 FROM matching_pipeline.inbox WHERE source=%s AND event_id=%s", (source, event["id"])).fetchone():
            return
        if source == "candidate":
            if event["event_type"] == "candidate.ready":
                from matching_engine.feed_store import FeedStore
                FeedStore(self.database_url).reconsider(connection, event["candidate_id"])
            vacancies = connection.execute("""SELECT r.vacancy_id FROM vacancy_pipeline.ready r
                JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=r.vacancy_id AND v.revision=r.revision
                    AND v.state='ready' AND v.business_status='open'
                JOIN candidate_pipeline.settings s ON s.active_schema_version=r.schema_version
                ORDER BY r.vacancy_id""").fetchall()
            for vacancy in vacancies:
                self._queue(connection, vacancy["vacancy_id"])
        else:
            try:
                self._queue(connection, event["vacancy_id"])
            except NotFoundError:
                connection.execute("""UPDATE matching_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL
                    WHERE vacancy_id=%s AND state<>'obsolete'""", (event["vacancy_id"],))
        connection.execute("""INSERT INTO matching_pipeline.inbox(source,event_id,received_at)
            VALUES (%s,%s,clock_timestamp())""", (source, event["id"]))

    def process_events(self, batch_limit: int = 100, *, max_attempts: int = 5,
                       retry_base_seconds: int = 30) -> int:
        """Acknowledge only with durable inbox/queue; isolate poisoned rows and leave storage outages unacked."""
        if not 1 <= batch_limit <= 1000 or max_attempts < 1 or retry_base_seconds < 0:
            raise ValueError("invalid delivery configuration")
        acknowledged = 0
        with connect(self.database_url) as connection:
            _lock(connection)
            # Both streams reserve capacity. A one-slot batch alternates after the
            # last durable acknowledgement, including across worker restarts.
            last = connection.execute("""SELECT source FROM matching_pipeline.inbox
                ORDER BY received_at DESC,source,event_id DESC LIMIT 1""").fetchone()
            first = "vacancy" if last and last["source"] == "candidate" else "candidate"
            second = "candidate" if first == "vacancy" else "vacancy"
            remaining = batch_limit
            attempted: dict[str, list[int]] = {"candidate": [], "vacancy": []}
            for index, source in enumerate((first, second, first)):
                quota = max(1, batch_limit // 2) if index == 0 else remaining
                if remaining == 0:
                    break
                # Sources are fixed local literals, never interpolated request text.
                rows = connection.execute(f"""SELECT o.* FROM {source}_pipeline.outbox o
                    LEFT JOIN matching_pipeline.delivery_errors e ON e.source=%s AND e.event_id=o.id
                    WHERE o.delivered_at IS NULL AND (e.event_id IS NULL OR (NOT e.quarantined AND e.retry_at<=now()))
                        AND NOT (o.id=ANY(%s::bigint[]))
                    ORDER BY o.id FOR UPDATE OF o SKIP LOCKED LIMIT %s""",
                    (source, attempted[source], min(quota, remaining))).fetchall()
                attempted[source].extend(event["id"] for event in rows)
                for event in rows:
                    try:
                        with connection.transaction():  # SAVEPOINT: a poison row must not block later events.
                            self._deliver(connection, source, event)
                            connection.execute(f"UPDATE {source}_pipeline.outbox SET delivered_at=now() WHERE id=%s", (event["id"],))
                            connection.execute("DELETE FROM matching_pipeline.delivery_errors WHERE source=%s AND event_id=%s", (source, event["id"]))
                    except (ValueError, KeyError, TypeError):
                        connection.execute("""INSERT INTO matching_pipeline.delivery_errors(source,event_id,attempts,retry_at,quarantined,error_code)
                            VALUES (%s,%s,1,now()+(%s*interval '1 second'),%s,'event_snapshot_invalid')
                            ON CONFLICT (source,event_id) DO UPDATE SET attempts=matching_pipeline.delivery_errors.attempts+1,
                                retry_at=now()+(%s*power(2,least(matching_pipeline.delivery_errors.attempts,10))*interval '1 second'),
                                quarantined=matching_pipeline.delivery_errors.attempts+1>=%s,error_code='event_snapshot_invalid'""",
                            (source, event["id"], retry_base_seconds, max_attempts == 1, retry_base_seconds, max_attempts))
                    else:
                        acknowledged += 1
                remaining -= len(rows)
                if remaining == 0:
                    break
        return acknowledged

    def delivery_status(self) -> DeliveryStatus:
        """Expose safe acknowledgement/queue totals and persisted retry/quarantine counts."""
        with connect(self.database_url) as connection:
            row = connection.execute("""SELECT
                (SELECT count(*) FROM candidate_pipeline.outbox WHERE delivered_at IS NULL) AS candidate_pending,
                (SELECT count(*) FROM vacancy_pipeline.outbox WHERE delivered_at IS NULL) AS vacancy_pending,
                (SELECT count(*) FROM matching_pipeline.inbox) AS acknowledged_count,
                (SELECT count(*) FROM matching_pipeline.runs WHERE state IN ('queued','processing','retrying')) AS queued_count,
                (SELECT count(*) FROM matching_pipeline.delivery_errors WHERE quarantined) AS quarantined_count,
                (SELECT count(*) FROM matching_pipeline.delivery_errors WHERE NOT quarantined) AS retrying_count""").fetchone()
            return DeliveryStatus.model_validate(row)

    def retry_event(self, source: str, event_id: int) -> DeliveryStatus:
        """Explicitly release one failed delivery; successfully delivered events never need replay."""
        if source not in ("candidate", "vacancy") or event_id < 1:
            raise ValueError("invalid event identity")
        with connect(self.database_url) as connection:
            _lock(connection)
            row = connection.execute("""DELETE FROM matching_pipeline.delivery_errors WHERE source=%s AND event_id=%s
                RETURNING event_id""", (source, event_id)).fetchone()
            if row is None:
                raise NotFoundError("failed event delivery not found")
        return self.delivery_status()
