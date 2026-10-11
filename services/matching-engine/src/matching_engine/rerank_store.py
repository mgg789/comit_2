"""Atomic baseline-rerank jobs, immutable model registry and revision-safe card publication."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import CandidateCard, MatchCandidate, ScoringPolicy
from matching_engine.combine_store import CombineStore, _lock
from matching_engine.database import connect
from matching_engine.fsp_boost import promote, promotion_explanation, promotion_policy
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
    manual_approval: object | None = None
    approval_error: str | None = None


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
                return connection.execute("SELECT to_regclass('rerank_pipeline.runs') IS NOT NULL AND to_regclass('rerank_pipeline.approval_receipts') IS NOT NULL AS ready").fetchone()["ready"]
        except psycopg.Error:
            return False

    @staticmethod
    def queue(connection: psycopg.Connection, main: dict) -> dict:
        """Enqueue exactly once inside main publication or operator promotion's transaction."""
        from matching_engine.rerank_features import feature_schema
        from matching_engine.rerank_model import tail_policy_hash
        settings = connection.execute("SELECT * FROM rerank_pipeline.settings").fetchone()
        active = settings["active_artifact_hash"]
        artifact = None if active is None else connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (active,)).fetchone()["artifact"]
        schema = _schema(connection, main)
        features = feature_schema(schema)
        feature_hash = features.schema_hash
        tail_hash = tail_policy_hash(artifact)
        row = connection.execute("""INSERT INTO rerank_pipeline.runs(run_id,main_run_id,vacancy_id,artifact_hash,feature_schema_hash,tail_policy_hash,approval_receipt_hash,activation_revision,state)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT(main_run_id,artifact_hash,feature_schema_hash,tail_policy_hash,approval_receipt_hash,activation_revision)
            DO UPDATE SET updated_at=rerank_pipeline.runs.updated_at RETURNING *""",
            (uuid4(), main["run_id"], main["vacancy_id"], active, feature_hash, tail_hash,
             settings["active_approval_receipt_hash"], settings["activation_revision"])).fetchone()
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
        settings = connection.execute("SELECT * FROM rerank_pipeline.settings").fetchone()
        active = settings["active_artifact_hash"]
        if main is None:
            return False
        artifact = None if active is None else connection.execute(
            "SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (active,)).fetchone()["artifact"]
        current_features = feature_schema(_schema(connection, main))
        return (current is not None and current["run_id"] == row["run_id"] and active == row["artifact_hash"]
                and row["approval_receipt_hash"] == settings["active_approval_receipt_hash"]
                and row["activation_revision"] == settings["activation_revision"]
                and row["feature_schema_hash"] == current_features.schema_hash
                and row["tail_policy_hash"] == tail_policy_hash(artifact)
                and main is not None and main["state"] == "ready" and CombineStore._valid(connection, main))

    @staticmethod
    def _status(connection: psycopg.Connection, row: dict, obsolete: bool = False) -> RerankStatus:
        main = connection.execute("SELECT selected_count,passed_count FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
        return RerankStatus(run_id=row["run_id"], main_run_id=row["main_run_id"], vacancy_id=row["vacancy_id"],
            artifact_hash=row["artifact_hash"], approval_receipt_hash=row["approval_receipt_hash"],
            activation_revision=row["activation_revision"], activation_kind=RerankStore._activation_kind(row), feature_schema_hash=row["feature_schema_hash"], tail_policy_hash=row["tail_policy_hash"],
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

    @staticmethod
    def _activation_kind(row: dict) -> str:
        return "manual_override" if row["approval_receipt_hash"] else "gated" if row["artifact_hash"] else "none"

    @staticmethod
    def _activate(connection: psycopg.Connection, artifact_hash: str | None,
                  approval_hash: str | None, action: str, previous: dict, *,
                  event_receipt_hash: str | None = None) -> dict:
        settings = connection.execute("""UPDATE rerank_pipeline.settings SET active_artifact_hash=%s,
            active_approval_receipt_hash=%s,activation_revision=activation_revision+1 RETURNING *""",
            (artifact_hash, approval_hash)).fetchone()
        connection.execute("""INSERT INTO rerank_pipeline.activation_events(event_id,action,previous_artifact_hash,
            previous_approval_receipt_hash,artifact_hash,approval_receipt_hash,activation_revision)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""", (uuid4(), action, previous["active_artifact_hash"],
            previous["active_approval_receipt_hash"], artifact_hash, event_receipt_hash or approval_hash, settings["activation_revision"]))
        mains = connection.execute("""SELECT r.* FROM matching_pipeline.controls c JOIN matching_pipeline.runs r
            ON r.run_id=c.current_run_id WHERE r.state='ready'""").fetchall()
        queued = 0
        for main in mains:
            if CombineStore._valid(connection, main):
                RerankStore.queue(connection, main)
                queued += 1
        return {"artifact_hash": artifact_hash, "approval_receipt_hash": approval_hash,
                "activation_revision": settings["activation_revision"], "queued_mains": queued}

    def activate_manual(self, artifact: dict, receipt: dict, *, only_if_uninitialized: bool = False) -> dict:
        """Atomically persist an explicit sort-only exception and queue all current main batches.

        Neither artifact gate/reviews nor registry approved flags change. Bootstrap
        acts only before any activation decision; later deactivation stays durable.
        """
        from matching_engine.combine_scoring import policy_hash
        from matching_engine.embeddings import EMBEDDING_SPACE_ID
        from matching_engine.rerank_activation import (
            _approval_from_db,
            validate_receipt,
        )
        from matching_engine.rerank_features import feature_schema
        from matching_engine.rerank_model import validate_artifact
        from matching_engine.store import _schema as active_schema

        validated = validate_artifact(artifact)
        decision = validate_receipt(receipt, validated)
        if decision["action"] != "activate":
            raise ValueError("manual activation requires an activate receipt")
        with connect(self.database_url) as connection:
            _lock(connection)
            settings = connection.execute("SELECT * FROM rerank_pipeline.settings").fetchone()
            if only_if_uninitialized and (settings["activation_revision"] != 0 or settings["active_artifact_hash"] is not None):
                return {"state": "existing_operator_decision_preserved", "artifact_hash": settings["active_artifact_hash"],
                        "approval_receipt_hash": settings["active_approval_receipt_hash"],
                        "activation_revision": settings["activation_revision"], "queued_mains": 0}
            schema = active_schema(connection)
            features = feature_schema(schema)
            if (validated["strict_schema_hash"] != schema.schema_hash or validated["feature_schema_hash"] != features.schema_hash
                    or validated["feature_names"] != list(features.names) or validated["semantic_space_id"] != EMBEDDING_SPACE_ID
                    or validated["main_policy_hash"] != policy_hash(ScoringPolicy())):
                raise ConflictError("manual artifact is incompatible with current schema/policy/Harrier")
            connection.execute("""INSERT INTO rerank_pipeline.artifacts(artifact_hash,artifact,approved) VALUES(%s,%s,false)
                ON CONFLICT(artifact_hash) DO NOTHING""", (validated["content_hash"], Jsonb(validated)))
            stored = connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (validated["content_hash"],)).fetchone()
            if stored["artifact"] != validated:
                raise ConflictError("registered artifact differs from immutable manual input")
            connection.execute("""INSERT INTO rerank_pipeline.approval_receipts(receipt_hash,artifact_hash,action,receipt)
                VALUES(%s,%s,'activate',%s) ON CONFLICT(receipt_hash) DO NOTHING""",
                (decision["receipt_hash"], validated["content_hash"], Jsonb(decision)))
            approval = connection.execute("SELECT * FROM rerank_pipeline.approval_receipts WHERE receipt_hash=%s", (decision["receipt_hash"],)).fetchone()
            _approval_from_db(approval, stored["artifact"])
            if settings["active_artifact_hash"] == validated["content_hash"] and settings["active_approval_receipt_hash"] == decision["receipt_hash"]:
                return {"state": "already_active", "artifact_hash": validated["content_hash"],
                        "approval_receipt_hash": decision["receipt_hash"], "activation_revision": settings["activation_revision"], "queued_mains": 0}
            return {"state": "manual_sort_only_activated", **self._activate(connection, validated["content_hash"],
                    decision["receipt_hash"], "manual_activate", settings)}

    def deactivate(self, expected_artifact_hash: str, *, actor: str, reason: str) -> dict:
        """Audit an explicit rollback to main fallback and requeue current mains atomically."""
        from matching_engine.rerank_activation import make_receipt
        with connect(self.database_url) as connection:
            _lock(connection)
            settings = connection.execute("SELECT * FROM rerank_pipeline.settings").fetchone()
            if settings["active_artifact_hash"] != expected_artifact_hash:
                raise ConflictError("active artifact changed before requested deactivation")
            artifact = connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (expected_artifact_hash,)).fetchone()
            if artifact is None:
                raise NotFoundError("active rerank artifact not found")
            receipt = make_receipt(artifact["artifact"], action="deactivate", actor=actor, reason=reason)
            connection.execute("""INSERT INTO rerank_pipeline.approval_receipts(receipt_hash,artifact_hash,action,receipt)
                VALUES(%s,%s,'deactivate',%s) ON CONFLICT(receipt_hash) DO NOTHING""",
                (receipt["receipt_hash"], expected_artifact_hash, Jsonb(receipt)))
            stored = connection.execute("SELECT receipt FROM rerank_pipeline.approval_receipts WHERE receipt_hash=%s",
                                        (receipt["receipt_hash"],)).fetchone()
            if stored["receipt"] != receipt:
                raise ConflictError("registered deactivation receipt differs from immutable input")
            result = self._activate(connection, None, None, "manual_deactivate", settings,
                                    event_receipt_hash=receipt["receipt_hash"])
            return {"state": "deactivated_to_main", **result, "deactivation_receipt_hash": receipt["receipt_hash"]}

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
            settings = connection.execute("SELECT * FROM rerank_pipeline.settings").fetchone()
            self._activate(connection, artifact_hash, None, "gated_promote", settings)

    def claim(self, lease_seconds: int, max_attempts: int) -> RerankJob | None:
        """Recover bounded expired leases and skip obsolete snapshots without mutable input reads."""
        with connect(self.database_url) as connection:
            _lock(connection)
            # Refresh bounded current runs after a rollout or reward-week boundary,
            # even if no profile/vacancy event arrived to enqueue new work.
            from matching_engine.rerank_model import tail_policy_hash
            settings = connection.execute("SELECT active_artifact_hash FROM rerank_pipeline.settings WHERE singleton").fetchone()
            active = settings["active_artifact_hash"]
            artifact = None if active is None else connection.execute(
                "SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (active,)).fetchone()["artifact"]
            stale = connection.execute("""SELECT mr.* FROM matching_pipeline.controls mc
                JOIN matching_pipeline.runs mr ON mr.run_id=mc.current_run_id AND mr.state='ready'
                JOIN matching_pipeline.settings ms ON ms.pool_generation=mr.pool_generation
                JOIN candidate_pipeline.settings cs ON cs.active_schema_version=mr.schema_version
                JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=mr.vacancy_id
                    AND v.revision=mr.vacancy_revision AND v.state='ready' AND v.business_status='open'
                JOIN rerank_pipeline.controls rc ON rc.vacancy_id=mr.vacancy_id
                JOIN rerank_pipeline.runs rr ON rr.run_id=rc.current_run_id AND rr.main_run_id=mr.run_id
                WHERE rr.tail_policy_hash<>%s ORDER BY mr.created_at,mr.run_id LIMIT 100""",
                (tail_policy_hash(artifact),)).fetchall()
            for main in stale:
                if CombineStore._valid(connection, main):
                    self.queue(connection, main)
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
                approval, approval_error = None, None
                if row["approval_receipt_hash"]:
                    from matching_engine.rerank_activation import _approval_from_db
                    stored = connection.execute("SELECT * FROM rerank_pipeline.approval_receipts WHERE receipt_hash=%s",
                                                (row["approval_receipt_hash"],)).fetchone()
                    try:
                        approval = _approval_from_db(stored, artifact)
                    except (ValueError, TypeError, KeyError):
                        approval_error = "manual_approval_invalid"
                return RerankJob(row["run_id"], token, VacancyReady.model_validate(main["vacancy_snapshot"]), candidates, cards,
                                 ScoringPolicy.model_validate(main["policy"]), _schema(connection, main), artifact, approval, approval_error)

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
            if outcome.mode == "main_fallback":
                from matching_engine.rerank_model import RerankScore
                expected = promote([RerankScore(card.candidate_id, None, {}, "recommended")
                                    for card in job.cards], job.candidates, job.cards, promotion_policy())
                if [item.candidate_id for item in outcome.items] != [item.candidate_id for item in expected]:
                    raise ValueError("fallback must preserve the bounded main promotion order")
            inputs = {candidate.candidate_id: candidate for candidate in job.candidates}
            recommended = 0
            for position, item in enumerate(outcome.items, 1):
                original = originals[item.candidate_id]
                if outcome.mode == "main_fallback" and (item.kind != "recommended" or item.score is not None):
                    raise ValueError("fallback must preserve the complete main order")
                card = RerankCard(**{**original.model_dump(), "position": position}, main_position=original.position,
                    rerank_position=position if outcome.mode == "learned" else None, mode=outcome.mode, kind=item.kind,
                    rerank_score=item.score, rerank_contributions=item.contributions, artifact_hash=row["artifact_hash"],
                    promotion=promotion_explanation(inputs[item.candidate_id], promotion_policy()),
                    approval_receipt_hash=row["approval_receipt_hash"], activation_revision=row["activation_revision"],
                    activation_kind=self._activation_kind(row))
                recommended += item.kind == "recommended"
                connection.execute("INSERT INTO rerank_pipeline.cards VALUES(%s,%s,%s,%s,%s,%s)",
                    (job.run_id, card.candidate_id, card.profile_revision, position, card.kind, Jsonb(card.model_dump(mode="json"))))
            connection.execute("""UPDATE rerank_pipeline.runs SET state='ready',mode=%s,fallback_reason=%s,recommended_count=%s,
                reserve_count=%s,published_at=now(),updated_at=now(),lease_token=NULL,lease_until=NULL,error_code=NULL WHERE run_id=%s""",
                (outcome.mode, outcome.fallback_reason, recommended, len(originals)-recommended, job.run_id))
            from matching_engine.learning_store import LearningStore
            main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
            LearningStore.capture_main(connection, main)
            from matching_engine.recommendations import capture_rerank
            capture_rerank(connection, job, outcome)
            return True

    def fail(self, job: RerankJob, error_code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Persist safe error codes and exponential retry delay only while owning a live lease."""
        with connect(self.database_url) as connection:
            connection.execute("""UPDATE rerank_pipeline.runs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'retrying' END,
                error_code=%s,available_at=now()+(%s*power(2,least(attempts-1,10))*interval '1 second'),lease_token=NULL,lease_until=NULL
                WHERE run_id=%s AND state='processing' AND lease_token=%s AND lease_until>now()""",
                (max_attempts, error_code, retry_base_seconds, job.run_id, job.lease_token))
