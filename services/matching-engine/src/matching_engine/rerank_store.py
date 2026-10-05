"""Atomic baseline-rerank jobs, immutable model registry and revision-safe card publication."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import CandidateCard, MatchCandidate, ScoringPolicy
from matching_engine.combine_store import CombineStore, _lock
from matching_engine.database import connect
from matching_engine.models import StrictSchema
from matching_engine.rerank_models import (
    RerankCard,
    RerankPage,
    RerankRequest,
    RerankStatus,
)
from matching_engine.store import ConflictError, NotFoundError
from matching_engine.vacancy_models import VacancyReady

if TYPE_CHECKING:
    from matching_engine.rerank_model import RerankOutcome


@dataclass(frozen=True)
class RerankJob:
    """Leased immutable top-k inputs; no private profile reads are needed at compute time."""

    run_id: UUID
    lease_token: UUID
    vacancy: VacancyReady
    candidates: list[MatchCandidate]
    cards: list[CandidateCard]
    policy: ScoringPolicy
    schema: StrictSchema
    artifact: dict | None


def _schema(connection: psycopg.Connection, main: dict) -> StrictSchema:
    row = connection.execute("SELECT version,schema_hash,coordinates FROM candidate_pipeline.strict_schemas WHERE version=%s AND schema_hash=%s",
                             (main["schema_version"], main["schema_hash"])).fetchone()
    if row is None:
        raise ConflictError("main strict registry no longer exists")
    return StrictSchema(version=row["version"], schema_hash=row["schema_hash"], coordinates=row["coordinates"])


class RerankStore:
    """Persist learned or explicit fallback results with live main and active-model guards."""

    def __init__(self, database_url: str) -> None:
        """Use the same database and transaction lock as the main selection pipeline."""
        self.database_url = database_url

    def ready(self) -> bool:
        """Check explicit migration availability without installing schema or loading models."""
        try:
            with connect(self.database_url) as connection:
                return connection.execute("SELECT to_regclass('rerank_pipeline.runs') IS NOT NULL AS ready").fetchone()["ready"]
        except psycopg.Error:
            return False

    @staticmethod
    def queue(connection: psycopg.Connection, main: dict) -> dict:
        """Enqueue exactly once inside main publication or operator promotion's transaction."""
        from matching_engine.rerank_features import feature_schema
        from matching_engine.rerank_model import tail_policy_hash
        active = connection.execute("SELECT active_artifact_hash FROM rerank_pipeline.settings").fetchone()["active_artifact_hash"]
        artifact = None if active is None else connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (active,)).fetchone()["artifact"]
        schema = _schema(connection, main)
        features = feature_schema(schema)
        feature_hash = features.schema_hash
        tail_hash = tail_policy_hash(artifact)
        row = connection.execute("""INSERT INTO rerank_pipeline.runs(run_id,main_run_id,vacancy_id,artifact_hash,feature_schema_hash,tail_policy_hash,state)
            VALUES(%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT(main_run_id,artifact_hash,feature_schema_hash,tail_policy_hash)
            DO UPDATE SET updated_at=rerank_pipeline.runs.updated_at RETURNING *""",
            (uuid4(), main["run_id"], main["vacancy_id"], active, feature_hash, tail_hash)).fetchone()
        connection.execute("""UPDATE rerank_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL
            WHERE vacancy_id=%s AND run_id<>%s AND state<>'obsolete'""", (main["vacancy_id"], row["run_id"]))
        connection.execute("""INSERT INTO rerank_pipeline.controls(vacancy_id,current_run_id) VALUES(%s,%s)
            ON CONFLICT(vacancy_id) DO UPDATE SET current_run_id=EXCLUDED.current_run_id""", (main["vacancy_id"], row["run_id"]))
        # A previously obsolete run can become current again after rollback to a known model.
        if row["state"] == "obsolete":
            row = connection.execute("""UPDATE rerank_pipeline.runs SET state='queued',attempts=0,error_code=NULL,
                lease_token=NULL,lease_until=NULL,available_at=now(),mode=NULL,fallback_reason=NULL,
                recommended_count=0,reserve_count=0,published_at=NULL WHERE run_id=%s RETURNING *""", (row["run_id"],)).fetchone()
            connection.execute("DELETE FROM rerank_pipeline.cards WHERE run_id=%s", (row["run_id"],))
        return row

    @staticmethod
    def _main(connection: psycopg.Connection, vacancy_id: UUID) -> dict:
        CombineStore._vacancy(connection, vacancy_id)
        main = CombineStore._current(connection, vacancy_id)
        if main is None or main["state"] != "ready" or not CombineStore._valid(connection, main):
            raise ConflictError("current main selection is not ready")
        return main

    @staticmethod
    def _current(connection: psycopg.Connection, vacancy_id: UUID) -> dict | None:
        return connection.execute("""SELECT r.* FROM rerank_pipeline.controls c JOIN rerank_pipeline.runs r ON r.run_id=c.current_run_id
            WHERE c.vacancy_id=%s""", (vacancy_id,)).fetchone()

    @staticmethod
    def _valid(connection: psycopg.Connection, row: dict) -> bool:
        from matching_engine.rerank_features import feature_schema
        from matching_engine.rerank_model import tail_policy_hash
        main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
        current = RerankStore._current(connection, row["vacancy_id"])
        active = connection.execute("SELECT active_artifact_hash FROM rerank_pipeline.settings").fetchone()["active_artifact_hash"]
        if main is None:
            return False
        artifact = None if active is None else connection.execute(
            "SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (active,)).fetchone()["artifact"]
        current_features = feature_schema(_schema(connection, main))
        return (current is not None and current["run_id"] == row["run_id"] and active == row["artifact_hash"]
                and row["feature_schema_hash"] == current_features.schema_hash
                and row["tail_policy_hash"] == tail_policy_hash(artifact)
                and main is not None and main["state"] == "ready" and CombineStore._valid(connection, main))

    @staticmethod
    def _status(connection: psycopg.Connection, row: dict, obsolete: bool = False) -> RerankStatus:
        main = connection.execute("SELECT selected_count,passed_count FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
        return RerankStatus(run_id=row["run_id"], main_run_id=row["main_run_id"], vacancy_id=row["vacancy_id"],
            artifact_hash=row["artifact_hash"], feature_schema_hash=row["feature_schema_hash"], tail_policy_hash=row["tail_policy_hash"],
            state="obsolete" if obsolete else row["state"], mode=row["mode"], fallback_reason=row["fallback_reason"],
            attempt_count=row["attempts"], error_code=row["error_code"], selected_count=main["selected_count"],
            recommended_count=row["recommended_count"], reserve_count=row["reserve_count"],
            unranked_by_reranker_count=main["passed_count"]-main["selected_count"], published_at=row["published_at"])

    def request(self, vacancy_id: UUID, request: RerankRequest) -> RerankStatus:
        """Queue current ready main inputs; a caller key always refers to its original run."""
        with connect(self.database_url) as connection:
            _lock(connection)
            if request.idempotency_key:
                old = connection.execute("SELECT * FROM rerank_pipeline.manual_requests WHERE idempotency_key=%s", (request.idempotency_key,)).fetchone()
                if old:
                    if old["vacancy_id"] != vacancy_id:
                        raise ConflictError("idempotency key belongs to another vacancy")
                    row = connection.execute("SELECT * FROM rerank_pipeline.runs WHERE run_id=%s", (old["run_id"],)).fetchone()
                    return self._status(connection, row, not self._valid(connection, row))
            row = self.queue(connection, self._main(connection, vacancy_id))
            if request.idempotency_key:
                connection.execute("INSERT INTO rerank_pipeline.manual_requests VALUES(%s,%s,%s)", (request.idempotency_key, vacancy_id, row["run_id"]))
            return self._status(connection, row)

    def status(self, vacancy_id: UUID) -> RerankStatus:
        """Report obsolete immediately even before the next main or rerank worker tick."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = self._current(connection, vacancy_id)
            if row is None:
                raise NotFoundError("rerank run not found")
            return self._status(connection, row, not self._valid(connection, row))

    def selections(self, vacancy_id: UUID, kind: Literal["recommended", "reserve"], limit: int, offset: int) -> RerankPage:
        """Serve one valid batch and one disposition only, never a mixture of generations."""
        if kind not in ("recommended", "reserve") or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid rerank pagination")
        with connect(self.database_url) as connection:
            _lock(connection)
            self._main(connection, vacancy_id)
            row = self._current(connection, vacancy_id)
            if row is None or row["state"] != "ready" or not self._valid(connection, row):
                raise ConflictError("current rerank selection is not ready")
            cards = connection.execute("SELECT card FROM rerank_pipeline.cards WHERE run_id=%s AND kind=%s ORDER BY position LIMIT %s OFFSET %s",
                                       (row["run_id"], kind, limit, offset)).fetchall()
            return RerankPage(run=self._status(connection, row), kind=kind, items=[RerankCard.model_validate(c["card"]) for c in cards], limit=limit, offset=offset)

    def retry(self, vacancy_id: UUID) -> RerankStatus:
        """Refresh obsolete snapshots or explicitly reset terminal failures."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = self.queue(connection, self._main(connection, vacancy_id))
            if row["state"] == "failed":
                row = connection.execute("""UPDATE rerank_pipeline.runs SET state='queued',attempts=0,error_code=NULL,
                    available_at=now(),lease_token=NULL,lease_until=NULL WHERE run_id=%s RETURNING *""", (row["run_id"],)).fetchone()
            return self._status(connection, row)

    def register(self, artifact: dict) -> str:
        """Validate and immutably store an experimental artifact; registration never activates it."""
        from matching_engine.rerank_model import (
            artifact_hash,
            promotion_eligible,
            validate_artifact,
        )
        validated = validate_artifact(artifact)
        identity = artifact_hash(validated)
        with connect(self.database_url) as connection:
            _lock(connection)
            connection.execute("""INSERT INTO rerank_pipeline.artifacts(artifact_hash,artifact,approved) VALUES(%s,%s,%s)
                ON CONFLICT(artifact_hash) DO NOTHING""", (identity, Jsonb(validated), promotion_eligible(validated)))
        return identity

    def promote(self, artifact_hash: str) -> None:
        """Activate only a fully gated/reviewed artifact and atomically queue every current main batch."""
        from matching_engine.rerank_model import promotion_eligible
        with connect(self.database_url) as connection:
            _lock(connection)
            artifact = connection.execute("SELECT * FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (artifact_hash,)).fetchone()
            if artifact is None:
                raise NotFoundError("rerank artifact not found")
            if not artifact["approved"] or not promotion_eligible(artifact["artifact"]):
                raise ConflictError("rerank artifact has not passed quality and review gates")
            connection.execute("UPDATE rerank_pipeline.settings SET active_artifact_hash=%s", (artifact_hash,))
            mains = connection.execute("""SELECT r.* FROM matching_pipeline.controls c JOIN matching_pipeline.runs r ON r.run_id=c.current_run_id WHERE r.state='ready'""").fetchall()
            for main in mains:
                if CombineStore._valid(connection, main):
                    self.queue(connection, main)

    def claim(self, lease_seconds: int, max_attempts: int) -> RerankJob | None:
        """Recover bounded expired leases and skip obsolete snapshots without mutable input reads."""
        with connect(self.database_url) as connection:
            _lock(connection)
            connection.execute("""UPDATE rerank_pipeline.runs SET state='failed',error_code='lease_exhausted',lease_token=NULL,lease_until=NULL
                WHERE state='processing' AND lease_until<=now() AND attempts>=%s""", (max_attempts,))
            while True:
                row = connection.execute("""SELECT * FROM rerank_pipeline.runs WHERE ((state IN ('queued','retrying') AND available_at<=now())
                    OR (state='processing' AND lease_until<=now())) AND attempts<%s ORDER BY created_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1""", (max_attempts,)).fetchone()
                if row is None:
                    return None
                if not self._valid(connection, row):
                    connection.execute("UPDATE rerank_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL WHERE run_id=%s", (row["run_id"],))
                    continue
                token = uuid4()
                connection.execute("""UPDATE rerank_pipeline.runs SET state='processing',attempts=attempts+1,lease_token=%s,
                    lease_until=now()+(%s*interval '1 second') WHERE run_id=%s""", (token, lease_seconds, row["run_id"]))
                main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
                cards = [CandidateCard.model_validate(item["card"]) for item in connection.execute("SELECT card FROM matching_pipeline.selections WHERE run_id=%s ORDER BY position", (main["run_id"],)).fetchall()]
                selected_ids = {card.candidate_id for card in cards}
                candidates = [MatchCandidate.model_validate(item) for item in main["candidates_snapshot"] if UUID(item["candidate_id"]) in selected_ids]
                artifact = None if row["artifact_hash"] is None else connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (row["artifact_hash"],)).fetchone()["artifact"]
                return RerankJob(row["run_id"], token, VacancyReady.model_validate(main["vacancy_snapshot"]), candidates, cards,
                                 ScoringPolicy.model_validate(main["policy"]), _schema(connection, main), artifact)

    def heartbeat(self, job: RerankJob, lease_seconds: int) -> bool:
        """Renew only the live lease still owned by this process."""
        with connect(self.database_url) as connection:
            return connection.execute("""UPDATE rerank_pipeline.runs SET lease_until=now()+(%s*interval '1 second')
                WHERE run_id=%s AND state='processing' AND lease_token=%s AND lease_until>now() RETURNING run_id""",
                (lease_seconds, job.run_id, job.lease_token)).fetchone() is not None

    def publish(self, job: RerankJob, outcome: "RerankOutcome") -> bool:
        """Atomically publish a complete permutation of main top-k under live input and model guards."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = connection.execute("SELECT *,lease_until>now() AS live FROM rerank_pipeline.runs WHERE run_id=%s", (job.run_id,)).fetchone()
            if row is None or row["state"] != "processing" or row["lease_token"] != job.lease_token or not row["live"]:
                return False
            if not self._valid(connection, row):
                connection.execute("UPDATE rerank_pipeline.runs SET state='obsolete',lease_token=NULL,lease_until=NULL WHERE run_id=%s", (job.run_id,))
                return False
            originals = {card.candidate_id: card for card in job.cards}
            if len(outcome.items) != len(originals) or {item.candidate_id for item in outcome.items} != set(originals):
                raise ValueError("rerank result is not a permutation of main top-k")
            if outcome.feature_schema_hash != row["feature_schema_hash"] or outcome.tail_policy_hash != row["tail_policy_hash"]:
                raise ValueError("rerank result schema or tail policy differs from queued identity")
            recommended = 0
            for position, item in enumerate(outcome.items, 1):
                original = originals[item.candidate_id]
                if outcome.mode == "main_fallback" and (item.kind != "recommended" or item.score is not None or original.position != position):
                    raise ValueError("fallback must preserve the complete main order")
                card = RerankCard(**{**original.model_dump(), "position": position}, main_position=original.position,
                    rerank_position=position if outcome.mode == "learned" else None, mode=outcome.mode, kind=item.kind,
                    rerank_score=item.score, rerank_contributions=item.contributions, artifact_hash=row["artifact_hash"])
                recommended += item.kind == "recommended"
                connection.execute("INSERT INTO rerank_pipeline.cards VALUES(%s,%s,%s,%s,%s,%s)",
                    (job.run_id, card.candidate_id, card.profile_revision, position, card.kind, Jsonb(card.model_dump(mode="json"))))
            connection.execute("""UPDATE rerank_pipeline.runs SET state='ready',mode=%s,fallback_reason=%s,recommended_count=%s,
                reserve_count=%s,published_at=now(),updated_at=now(),lease_token=NULL,lease_until=NULL,error_code=NULL WHERE run_id=%s""",
                (outcome.mode, outcome.fallback_reason, recommended, len(originals)-recommended, job.run_id))
            return True

    def fail(self, job: RerankJob, error_code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Persist safe error codes and exponential retry delay only while owning a live lease."""
        with connect(self.database_url) as connection:
            connection.execute("""UPDATE rerank_pipeline.runs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'retrying' END,
                error_code=%s,available_at=now()+(%s*power(2,least(attempts-1,10))*interval '1 second'),lease_token=NULL,lease_until=NULL
                WHERE run_id=%s AND state='processing' AND lease_token=%s AND lease_until>now()""",
                (max_attempts, error_code, retry_base_seconds, job.run_id, job.lease_token))
