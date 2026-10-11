"""Durable employer feed sessions, exposures and vacancy-scoped feedback."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine import feed_policy
from matching_engine.combine_models import CandidateCard, ScoringPolicy
from matching_engine.combine_scoring import policy_hash as scoring_policy_hash
from matching_engine.combine_scoring import score_candidate, strict_filter
from matching_engine.combine_store import CombineStore, _lock
from matching_engine.fsp_boost import weekly_activity_deadline
from matching_engine.models import CandidateProfile
from matching_engine.database import connect
from matching_engine.feed_models import (
    FeedCard,
    FeedDecision,
    FeedDecisionRequest,
    FeedImpression,
    FeedImpressionRequest,
    FeedListPage,
    FeedPage,
    FeedRefillStatus,
    FeedRequest,
    SavedCard,
    ShowcaseLikeReceipt,
    ShowcaseLikeRequest,
    ShowcaseMismatchWarning,
    VisitedCard,
)
from matching_engine.feed_refill import (
    FeedRefillStore,
    pool_change_fraction,
    pool_snapshot,
    pool_snapshot_hash,
)
from matching_engine.learning_store import LearningStore
from matching_engine.preference_model import apply as apply_preference
from matching_engine.preference_store import PreferenceStore
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import tail_policy_hash
from matching_engine.rerank_store import _schema
from matching_engine.store import ConflictError, NotFoundError
from matching_engine.vacancy_models import VacancyReady


class ShowcaseMismatchError(ConflictError):
    """An overrideable suitability warning, distinct from mandatory source guards."""

    def __init__(self, warning: ShowcaseMismatchWarning) -> None:
        """Retain only public machine-readable reasons and source-bound acknowledgement."""
        super().__init__("strict vacancy filters require explicit acknowledgement")
        self.warning = warning


class FeedPolicyWorker(Protocol):
    """Policy-worker boundary; live policy decisions never belong to SQL or an LLM."""

    def snapshot(self, profile: dict[str, Any]) -> dict[str, Any]: ...

    def default_policy(self) -> dict[str, Any]: ...

    def policy_hash(self, snapshot: dict[str, Any]) -> str: ...

    def assess(self, baseline: dict[str, Any], current: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]: ...


def _encode_cursor(position: int) -> str:
    """Encode only the immutable position; cursors cannot change page ordering."""
    return base64.urlsafe_b64encode(str(position).encode()).decode().rstrip("=")


def _decode_cursor(value: str | None) -> int:
    """Decode a position cursor and reject malformed or negative offsets."""
    if value is None or value == "":
        return 0
    try:
        result = int(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode())
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("invalid feed cursor") from exc
    if result < 0:
        raise ValueError("invalid feed cursor")
    return result


def _encode_list_cursor(kind: str, snapshot_at: datetime, position_at: datetime,
                        candidate_id: UUID, source_revision: int, owner_id: UUID, vacancy_id: UUID,
                        candidate_filter: UUID | None = None) -> str:
    """Encode a bounded keyset cursor bound to owner, vacancy, list kind and filters."""
    payload = json.dumps({"v": 1, "kind": kind, "at": snapshot_at.isoformat(),
                          "last_at": position_at.isoformat(),
                          "id": str(candidate_id), "source_revision": source_revision,
                          "owner_id": str(owner_id), "vacancy_id": str(vacancy_id),
                          "candidate_filter": None if candidate_filter is None else str(candidate_filter)},
                         separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_list_cursor(value: str, kind: str, owner_id: UUID, vacancy_id: UUID,
                        candidate_filter: UUID | None = None) -> tuple[datetime, datetime, UUID, int]:
    """Decode a keyset cursor and reject cross-owner, vacancy or filter replay."""
    if not value or len(value) > 1024 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in value):
        raise ValueError("invalid list cursor")
    try:
        raw = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode())
        if raw.get("v") != 1 or raw.get("kind") != kind:
            raise ValueError("invalid list cursor")
        if raw.get("owner_id") != str(owner_id) or raw.get("vacancy_id") != str(vacancy_id):
            raise ConflictError("list cursor scope changed")
        expected_filter = None if candidate_filter is None else str(candidate_filter)
        if raw.get("candidate_filter") != expected_filter:
            raise ConflictError("list cursor filter changed")
        timestamp = datetime.fromisoformat(raw["at"])
        position_at = datetime.fromisoformat(raw["last_at"])
        if timestamp.tzinfo is None or position_at.tzinfo is None:
            raise ValueError("invalid list cursor")
        revision = raw["source_revision"]
        if isinstance(revision, bool):
            raise TypeError("invalid list cursor")
        return timestamp, position_at, UUID(raw["id"]), int(revision)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("invalid list cursor") from exc


class FeedStore:
    """Persist immutable 24-hour feed sessions and apply owner-checked feedback atomically."""

    def __init__(self, database_url: str, policy_worker: FeedPolicyWorker | None = None) -> None:
        """Use a shared matching database and an optional policy-worker implementation."""
        self.database_url = database_url
        self.policy_worker = policy_worker

    def ready(self) -> bool:
        """Report whether migration 007 created every feed table needed by the API."""
        try:
            with connect(self.database_url) as connection:
                return bool(connection.execute("""SELECT to_regclass('feed_pipeline.sessions') IS NOT NULL
                    AND to_regclass('feed_pipeline.exposures') IS NOT NULL
                    AND to_regclass('feed_pipeline.decisions') IS NOT NULL
                    AND to_regclass('feed_pipeline.saved') IS NOT NULL
                    AND to_regclass('feed_pipeline.visited') IS NOT NULL
                    AND to_regclass('learning_pipeline.events') IS NOT NULL
                    AND to_regclass('preference_pipeline.jobs') IS NOT NULL
                    AND to_regclass('hypothesis_pipeline.pairs') IS NOT NULL
                    AND to_regclass('feed_pipeline.refill_jobs') IS NOT NULL AS ready""").fetchone()["ready"])
        except psycopg.Error:
            return False

    @staticmethod
    def _owner_vacancy(connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID) -> dict:
        """Lock and return the current open ready vacancy owned by one employer."""
        row = connection.execute("""SELECT v.owner_id, v.revision AS source_revision, v.state AS vacancy_state,
                v.business_status, r.*, s.active_schema_version, sc.schema_hash AS active_schema_hash
            FROM vacancy_pipeline.vacancies v
            JOIN vacancy_pipeline.ready r ON r.vacancy_id=v.vacancy_id AND r.revision=v.revision
            JOIN candidate_pipeline.settings s ON true
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=s.active_schema_version
                AND sc.schema_hash=r.schema_hash
            WHERE v.vacancy_id=%s AND v.owner_id=%s AND v.state='ready' AND v.business_status='open'
            FOR UPDATE OF v""", (vacancy_id, owner_id)).fetchone()
        if row is None:
            raise NotFoundError("owned ready vacancy not found")
        return row

    def _policy_snapshot(self, connection: psycopg.Connection, vacancy_id: UUID, main: dict) -> tuple[dict, str]:
        """Obtain a deterministic policy snapshot from the policy worker or safe fallback."""
        worker = self.policy_worker or feed_policy
        snapshot = worker.default_policy()
        return snapshot, worker.policy_hash(snapshot)

    def _policy_current(self, connection: psycopg.Connection, vacancy_id: UUID, session: dict) -> bool:
        """Compare a session policy with the current worker policy without changing the session."""
        if session.get("source") == "showcase":
            return self._policy_snapshot(connection, vacancy_id, {})[1] == session["policy_hash"]
        main = connection.execute("SELECT policy_hash FROM matching_pipeline.runs WHERE run_id=%s", (session["main_run_id"],)).fetchone()
        if main is None:
            return False
        _, digest = self._policy_snapshot(connection, vacancy_id, main)
        return digest == session["policy_hash"]

    @staticmethod
    def _active_model_identity(connection: psycopg.Connection, main: dict) -> tuple[str | None, str, str, int]:
        """Read active artifact, feature schema and tail policy identities from the registry."""
        active = connection.execute("SELECT active_artifact_hash,activation_revision FROM rerank_pipeline.settings WHERE singleton").fetchone()
        artifact_hash = active["active_artifact_hash"] if active else None
        artifact = None if artifact_hash is None else connection.execute(
            "SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (artifact_hash,)
        ).fetchone()
        if artifact_hash is not None and artifact is None:
            raise ConflictError("active rerank artifact is missing")
        feature_hash = __import__("matching_engine.rerank_features", fromlist=["feature_schema"]).feature_schema(
            _schema(connection, main)
        ).schema_hash
        return artifact_hash, feature_hash, tail_policy_hash(None if artifact is None else artifact["artifact"]), active["activation_revision"] if active else 0

    @staticmethod
    def _candidate_ready(connection: psycopg.Connection, vacancy: dict, candidate_id: UUID, revision: int,
                         *, allow_blocked: bool = False) -> bool:
        """Require current consent, pool artifact, registry identity and nonblocked selection state."""
        row = connection.execute("""SELECT p.candidate_id
            FROM candidate_pipeline.pool p
            JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id AND c.revision=p.revision
                AND c.state='ready' AND (c.profile->'consents'->>'processing')::boolean IS TRUE
                AND (c.profile->'consents'->>'pool')::boolean IS TRUE
            JOIN candidate_pipeline.settings s ON s.active_schema_version=p.schema_version
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=p.schema_version AND sc.schema_hash=p.schema_hash
            LEFT JOIN matching_pipeline.selection_states ss ON ss.vacancy_id=%s AND ss.candidate_id=p.candidate_id
            WHERE p.candidate_id=%s AND p.revision=%s AND p.schema_version=%s AND p.schema_hash=%s
                AND (%s OR ss.state IS NULL OR ss.state='eligible' OR ss.state='visited_updated')
                """, (
                    vacancy["vacancy_id"], candidate_id, revision, vacancy["schema_version"], vacancy["schema_hash"], allow_blocked
        )).fetchone()
        return row is not None


    @staticmethod
    def _profile_snapshot(connection: psycopg.Connection, candidate_id: UUID, revision: int) -> dict[str, Any]:
        """Build the canonical text-free structural baseline from the raw profile row."""
        row = connection.execute("SELECT profile FROM candidate_pipeline.candidates WHERE candidate_id=%s AND revision=%s FOR SHARE", (candidate_id, revision)).fetchone()
        if row is None:
            raise ConflictError("candidate profile revision is stale")
        return feed_policy.snapshot(row["profile"])

    @staticmethod
    def _card(row: dict, decision: str | None = None, badges: list[str] | None = None) -> FeedCard:
        """Add exposure identity and live earned activity to immutable ranking metadata."""
        value = dict(row["card"])
        # Activity is live server-owned evidence, not an immutable ranking contribution.
        current = row.get("current_profile")
        value["weekly_activity_expires_at"] = weekly_activity_deadline(CandidateProfile.model_validate(current)) if current else None
        value.update({"exposure_id": row["exposure_id"], "session_id": row["session_id"],
                      "kind": row["kind"], "served_at": row["served_at"], "decision": decision,
                      "history_badges": badges or [], "change_assessment": row.get("change_assessment"),
                      "batch_id": row.get("batch_id"), "source_main_run_id": row.get("source_main_run_id"),
                      "source_rerank_run_id": row.get("source_rerank_run_id"),
                      "source_pool_generation": row.get("source_pool_generation")})
        return FeedCard.model_validate(value)

    def _current_run(self, connection: psycopg.Connection, vacancy: dict) -> dict:
        """Read the current ready rerank run and ensure all source identities are live."""
        row = connection.execute("""SELECT rr.*, mr.vacancy_revision, mr.schema_version, mr.schema_hash,
                mr.pool_generation, mr.policy_hash AS main_policy_hash
            FROM rerank_pipeline.controls rc
            JOIN rerank_pipeline.runs rr ON rr.run_id=rc.current_run_id
            JOIN matching_pipeline.runs mr ON mr.run_id=rr.main_run_id
            WHERE rc.vacancy_id=%s""", (vacancy["vacancy_id"],)).fetchone()
        if row is None or row["state"] != "ready" or row["main_policy_hash"] is None:
            raise ConflictError("current rerank selection is not ready")
        if row["main_policy_hash"] != scoring_policy_hash(ScoringPolicy()):
            raise ConflictError("current main policy is stale")
        if row["vacancy_revision"] != vacancy["revision"] or row["schema_version"] != vacancy["schema_version"] \
                or row["schema_hash"] != vacancy["schema_hash"]:
            raise ConflictError("current rerank selection is stale")
        artifact_hash, feature_hash, tail_hash, activation_revision = self._active_model_identity(connection, row)
        if row["artifact_hash"] != artifact_hash or row["feature_schema_hash"] != feature_hash \
                or row["tail_policy_hash"] != tail_hash or row["activation_revision"] != activation_revision:
            raise ConflictError("current rerank model is stale")
        main_row = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
        if main_row is None or not CombineStore._valid(connection, main_row):
            raise ConflictError("current main selection is stale")
        policy, policy_hash = self._policy_snapshot(connection, vacancy["vacancy_id"], row)
        if policy is None or not policy_hash:
            raise ConflictError("feed policy unavailable")
        row["feed_policy"] = policy
        row["feed_policy_hash"] = policy_hash
        cards = connection.execute("SELECT * FROM rerank_pipeline.cards WHERE run_id=%s ORDER BY position", (row["run_id"],)).fetchall()
        for card in cards:
            if not self._candidate_ready(connection, vacancy, card["candidate_id"], card["profile_revision"]):
                raise ConflictError("current candidate source is stale or blocked")
        return row

    def _validate_session(self, connection: psycopg.Connection, session: dict, *, allow_decided: bool = False) -> set[UUID]:
        """Validate current vacancy/model identities and all still-actionable card sources."""
        vacancy = self._owner_vacancy(connection, session["owner_id"], session["vacancy_id"])
        if session["expires_at"] <= connection.execute("SELECT now() AS value").fetchone()["value"]:
            raise ConflictError("feed session expired")
        if vacancy["revision"] != session["vacancy_revision"] or vacancy["schema_version"] != session["schema_version"] \
                or vacancy["schema_hash"] != session["schema_hash"]:
            raise ConflictError("feed vacancy revision is stale")
        if scoring_policy_hash(ScoringPolicy()) != session["main_policy_hash"]:
            raise ConflictError("feed main policy is stale")
        if session.get("source", "tinder") != "showcase":
            main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (session["main_run_id"],)).fetchone()
            if main is None:
                raise ConflictError("feed main snapshot is missing")
            if main["policy_hash"] != session["main_policy_hash"]:
                raise ConflictError("feed main policy is stale")
            identity = self._active_model_identity(connection, main)
            if identity != (session["artifact_hash"], session["feature_schema_hash"], session["tail_policy_hash"], session["activation_revision"]):
                raise ConflictError("feed model identity is stale")
        if not self._policy_current(connection, session["vacancy_id"], session):
            raise ConflictError("feed policy is stale")
        rows = connection.execute("""SELECT e.exposure_id,e.candidate_id,e.profile_revision,
                EXISTS (SELECT 1 FROM feed_pipeline.decisions d WHERE d.exposure_id=e.exposure_id) AS decided
            FROM feed_pipeline.exposures e WHERE e.session_id=%s ORDER BY e.position""", (session["session_id"],)).fetchall()
        stale: set[UUID] = set()
        for row in rows:
            if not self._candidate_ready(connection, vacancy, row["candidate_id"], row["profile_revision"],
                                         allow_blocked=bool(row["decided"])):
                if row["decided"] and allow_decided:
                    # A decided exposure remains an immutable receipt, but its source
                    # is still checked by callers that request a fresh PDF.
                    stale.add(row["candidate_id"])
                else:
                    stale.add(row["candidate_id"])
        return stale

    def open(self, owner_id: UUID, request: FeedRequest, vacancy_id: UUID | None = None) -> FeedPage:
        """Open a new 24-hour immutable session from the current valid rerank run."""
        selected_vacancy = vacancy_id
        with connect(self.database_url) as connection:
            _lock(connection)
            if selected_vacancy is None:
                row = connection.execute("""SELECT vacancy_id FROM vacancy_pipeline.vacancies
                    WHERE owner_id=%s AND state='ready' AND business_status='open' ORDER BY updated_at DESC LIMIT 1""", (owner_id,)).fetchone()
                if row is None:
                    raise NotFoundError("owned open vacancy not found")
                selected_vacancy = row["vacancy_id"]
            vacancy = self._owner_vacancy(connection, owner_id, selected_vacancy)
            main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE vacancy_id=%s ORDER BY created_at DESC LIMIT 1",
                                      (selected_vacancy,)).fetchone()
            if main is None:
                raise ConflictError("current main selection is not ready")
            rerank = self._current_run(connection, vacancy)
            main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (rerank["main_run_id"],)).fetchone()
            LearningStore.capture_main(connection, main)
            initial_snapshot = pool_snapshot(connection, VacancyReady.model_validate(main["vacancy_snapshot"]))
            initial_snapshot_hash = pool_snapshot_hash(initial_snapshot)
            session_id = uuid4()
            expires_at = connection.execute("SELECT now()+interval '24 hours' AS value").fetchone()["value"]
            connection.execute("""INSERT INTO feed_pipeline.sessions
                (session_id,owner_id,vacancy_id,vacancy_revision,schema_version,schema_hash,pool_generation,
                 main_run_id,rerank_run_id,main_policy_hash,policy_hash,feature_schema_hash,tail_policy_hash,artifact_hash,
                 include_reserve,expires_at,activation_revision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                    session_id, owner_id, selected_vacancy, rerank["vacancy_revision"], rerank["schema_version"],
                    rerank["schema_hash"], rerank["pool_generation"], rerank["main_run_id"], rerank["run_id"],
                    rerank["main_policy_hash"], rerank["feed_policy_hash"], rerank["feature_schema_hash"], rerank["tail_policy_hash"],
                    rerank["artifact_hash"], request.include_reserve, expires_at, rerank["activation_revision"],
            ))
            batch_id = uuid4()
            connection.execute("""INSERT INTO feed_pipeline.batches
                (batch_id,session_id,owner_id,vacancy_id,batch_number,reason,main_run_id,rerank_run_id,
                 pool_generation,pool_snapshot_version,pool_snapshot,pool_snapshot_hash)
                VALUES (%s,%s,%s,%s,1,'initial',%s,%s,%s,%s,%s,%s)""", (
                batch_id, session_id, owner_id, selected_vacancy, rerank["main_run_id"], rerank["run_id"],
                rerank["pool_generation"], "pool-n1-v1", Jsonb(initial_snapshot), initial_snapshot_hash))
            connection.execute("""UPDATE feed_pipeline.sessions SET pool_snapshot_version=%s,pool_snapshot=%s,
                pool_snapshot_hash=%s,refill_state='active' WHERE session_id=%s""",
                               ("pool-n1-v1", Jsonb(initial_snapshot), initial_snapshot_hash, session_id))
            cards = connection.execute("""SELECT * FROM rerank_pipeline.cards WHERE run_id=%s
                AND (%s OR kind='recommended') ORDER BY position""", (rerank["run_id"], request.include_reserve)).fetchall()
            if not cards:
                connection.execute("""UPDATE feed_pipeline.sessions SET refill_state='exhausted',
                    exhausted_at=clock_timestamp() WHERE session_id=%s""", (session_id,))
            for source in cards:
                exposure = uuid4()
                card = {**source["card"], "batch_id": str(batch_id), "source_main_run_id": str(rerank["main_run_id"]),
                        "source_rerank_run_id": str(rerank["run_id"]), "source_pool_generation": rerank["pool_generation"]}
                connection.execute("""INSERT INTO feed_pipeline.exposures
                    (exposure_id,session_id,owner_id,vacancy_id,candidate_id,profile_revision,position,kind,card,batch_id,
                     source_main_run_id,source_rerank_run_id,source_pool_generation)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                        exposure, session_id, owner_id, selected_vacancy, source["candidate_id"], source["profile_revision"],
                        source["position"], source["kind"], Jsonb(card), batch_id, rerank["main_run_id"], rerank["run_id"],
                        rerank["pool_generation"],
                ))
                LearningStore.event(connection, {"exposure_id": exposure, "owner_id": owner_id,
                                                 "vacancy_id": selected_vacancy}, "allocated")
            return self._page_locked(connection, owner_id, session_id, None, request.limit)

    @staticmethod
    def _refresh_preferences(connection: psycopg.Connection, session: dict) -> dict:
        """Permute only unserved positions beyond the delivered high-water mark.

        Delivered cards, receipts and earlier position slots are immutable. New
        versions use existing suffix slots, preserving every issued position
        cursor and keeping recommended/reserve membership unchanged.
        """
        if session.get("source") == "showcase":
            # A manual choice must neither impersonate a ranked exposure nor reset its learner.
            return session
        control = PreferenceStore.ensure(connection, session)
        active = PreferenceStore.active(connection, control)
        model_hash = active["model_hash"] if active else None
        if (session["preference_revision"], session["preference_model_hash"]) == (control["revision"], model_hash):
            return session
        rows = connection.execute("""SELECT e.*,r.card AS baseline_card,s.features,s.feature_hash
            FROM feed_pipeline.exposures e JOIN feed_pipeline.sessions f USING(session_id)
            JOIN rerank_pipeline.cards r ON r.run_id=f.rerank_run_id AND r.candidate_id=e.candidate_id
            LEFT JOIN learning_pipeline.samples s ON s.main_run_id=f.main_run_id AND s.candidate_id=e.candidate_id
            WHERE e.session_id=%s AND e.served_at IS NULL AND e.position>
                (SELECT COALESCE(max(position),0) FROM feed_pipeline.exposures WHERE session_id=%s AND served_at IS NOT NULL)
                AND e.card->>'hypothesis_pair_id' IS NULL AND NOT e.exploration_suppressed
            ORDER BY e.position FOR UPDATE OF e""", (session["session_id"], session["session_id"])).fetchall()
        artifact = None
        if active:
            artifact = connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (session["artifact_hash"],)).fetchone()["artifact"]
        prepared = []
        try:
            for row in rows:
                card = dict(row["baseline_card"])
                card.update(preference_model_hash=model_hash, preference_revision=control["revision"],
                            preference_score=None, preference_adjustment=0., preference_contributions={},
                            preference_state="baseline", order_revision=session["order_revision"] + 1)
                if active:
                    if row["features"] is None or digest(row["features"]) != row["feature_hash"]:
                        raise ValueError("preference feature snapshot unavailable")
                    result = apply_preference(row["features"], card["rerank_score"], active["model"], artifact)
                    card.update(preference_score=result["score"], preference_adjustment=result["adjustment"],
                                preference_contributions=result["contributions"], preference_state="personalized")
                prepared.append((row, card))
        except (ValueError, TypeError, KeyError):
            # Corrupt/incompatible optional models never prevent safe baseline delivery.
            model_hash = None
            prepared = [(row, {**row["baseline_card"], "preference_model_hash": None,
                         "preference_revision": control["revision"], "preference_score": None,
                         "preference_adjustment": 0., "preference_contributions": {}, "preference_state": "baseline",
                         "order_revision": session["order_revision"] + 1}) for row in rows]
        connection.execute("SET CONSTRAINTS feed_pipeline.exposures_session_id_position_key DEFERRED")
        for kind in ("recommended", "reserve"):
            group = [(row, card) for row, card in prepared if row["kind"] == kind]
            positions = sorted(row["position"] for row, _card in group)
            if model_hash:
                group.sort(key=lambda pair: (-pair[1]["preference_score"], -pair[1]["explanation"]["score"], str(pair[0]["candidate_id"])))
            else:
                group.sort(key=lambda pair: pair[1]["position"])
            for position, (row, card) in zip(positions, group, strict=True):
                card["preference_position"] = position
                connection.execute("UPDATE feed_pipeline.exposures SET position=%s,card=%s WHERE exposure_id=%s",
                                   (position, Jsonb(card), row["exposure_id"]))
        return connection.execute("""UPDATE feed_pipeline.sessions SET preference_revision=%s,
            preference_model_hash=%s,order_revision=order_revision+1 WHERE session_id=%s RETURNING *""",
            (control["revision"], model_hash, session["session_id"])).fetchone()

    def _page_locked(self, connection: psycopg.Connection, owner_id: UUID, session_id: UUID,
                     cursor: str | None, limit: int) -> FeedPage:
        """Read and mark a stable position page while holding the session row lock."""
        if not 1 <= limit <= 100:
            raise ValueError("invalid feed limit")
        session = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE session_id=%s AND owner_id=%s FOR UPDATE",
                                     (session_id, owner_id)).fetchone()
        if session is None:
            raise NotFoundError("feed session not found")
        if session["expires_at"] <= connection.execute("SELECT now() AS value").fetchone()["value"]:
            raise ConflictError("feed session expired")
        stale = self._validate_session(connection, session)
        session = self._refresh_preferences(connection, session)
        after = _decode_cursor(cursor)
        from matching_engine.hypothesis_store import HypothesisStore
        session = HypothesisStore.prepare(connection, session, after)
        rows = connection.execute("""SELECT e.*,d.action,c.profile AS current_profile,
                COALESCE(v.history_badges,'[]'::jsonb) AS history_badges,
                COALESCE(v.last_assessment,'null'::jsonb) AS change_assessment
            FROM feed_pipeline.exposures e
            LEFT JOIN candidate_pipeline.candidates c ON c.candidate_id=e.candidate_id
                AND c.revision=e.profile_revision AND c.state='ready'
            LEFT JOIN feed_pipeline.decisions d ON d.exposure_id=e.exposure_id
            LEFT JOIN feed_pipeline.visited v ON v.owner_id=e.owner_id AND v.vacancy_id=e.vacancy_id
                AND v.candidate_id=e.candidate_id
            WHERE e.session_id=%s AND e.position>%s AND d.exposure_id IS NULL
                AND NOT e.exploration_suppressed
                AND NOT (e.candidate_id = ANY(%s::uuid[]))
            ORDER BY e.position LIMIT %s""", (session_id, after, list(stale), limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if rows:
            connection.execute("UPDATE feed_pipeline.exposures SET served_at=COALESCE(served_at,now()) WHERE exposure_id=ANY(%s::uuid[])",
                               ([row["exposure_id"] for row in rows],))
            for row in rows:
                row["served_at"] = row["served_at"] or connection.execute("SELECT now() AS value").fetchone()["value"]
                LearningStore.event(connection, row, "delivered")
            # Credit actual deliveries before signalling the end of this page:
            # a feasible full-pool pair may create the next two slots now.
            session = HypothesisStore.prepare(connection, session, rows[-1]["position"])
            has_more = bool(connection.execute("""SELECT 1 FROM feed_pipeline.exposures e
                LEFT JOIN feed_pipeline.decisions d USING(exposure_id) WHERE e.session_id=%s
                AND e.position>%s AND d.exposure_id IS NULL AND NOT e.exploration_suppressed
                AND NOT(e.candidate_id=ANY(%s::uuid[])) LIMIT 1""",
                (session_id, rows[-1]["position"], list(stale))).fetchone())
        refill_status = None
        pool_fraction = 0.0
        if session.get("source", "tinder") != "showcase":
            refill = FeedRefillStore(self.database_url)
            refill_status = refill.enqueue_locked(connection, owner_id, session["vacancy_id"], session_id)
            current_pool = pool_snapshot(connection, VacancyReady.model_validate(
                connection.execute("SELECT vacancy_snapshot FROM matching_pipeline.runs WHERE run_id=%s",
                                   (session["main_run_id"],)).fetchone()["vacancy_snapshot"]))
            pool_fraction = pool_change_fraction(list(session["pool_snapshot"] or []), current_pool)
            latest = connection.execute("""SELECT * FROM feed_pipeline.refill_jobs
                WHERE session_id=%s ORDER BY created_at DESC LIMIT 1""", (session_id,)).fetchone()
            if refill_status is None and latest is not None:
                from matching_engine.feed_refill import _status
                refill_status = _status(latest)
            session = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE session_id=%s", (session_id,)).fetchone()
            has_more = has_more or (refill_status is not None and refill_status.state in ("queued", "running", "retrying"))
        items = [self._card(row, badges=list(row["history_badges"] or [])) for row in rows]
        next_cursor = _encode_cursor(rows[-1]["position"]) if rows and has_more else None
        return FeedPage(session_id=session_id, vacancy_id=session["vacancy_id"], vacancy_revision=session["vacancy_revision"],
                        policy_hash=session["policy_hash"], expires_at=session["expires_at"], cursor=cursor,
                        next_cursor=next_cursor, has_more=has_more, items=items, order_revision=session["order_revision"],
                        refill_state=session.get("refill_state", "active"),
                        refill_job_id=None if refill_status is None else refill_status.job_id,
                        pool_change_fraction=pool_fraction)

    def page(self, owner_id: UUID, vacancy_id: UUID, session_id: UUID, cursor: str | None, limit: int = 50) -> FeedPage:
        """Continue one owner's session after feedback using a stable position cursor."""
        with connect(self.database_url) as connection:
            _lock(connection)
            session = connection.execute("SELECT 1 FROM feed_pipeline.sessions WHERE session_id=%s AND owner_id=%s AND vacancy_id=%s",
                                         (session_id, owner_id, vacancy_id)).fetchone()
            if session is None:
                raise NotFoundError("feed session not found")
            return self._page_locked(connection, owner_id, session_id, cursor, limit)

    def request_refill(self, owner_id: UUID, vacancy_id: UUID, session_id: UUID,
                       *, force: bool = False, reason: str | None = None) -> FeedRefillStatus | None:
        """Request a deduplicated refill job for an owned immutable session."""
        return FeedRefillStore(self.database_url).enqueue(owner_id, vacancy_id, session_id,
                                                          force=force, reason=reason)

    def refill_status(self, owner_id: UUID, vacancy_id: UUID, session_id: UUID) -> FeedRefillStatus | None:
        """Read the latest durable refill job for an owned immutable session."""
        return FeedRefillStore(self.database_url).status(owner_id, vacancy_id, session_id)

    def exposure(self, owner_id: UUID, vacancy_id: UUID, exposure_id: UUID) -> FeedCard:
        """Return a current exposure; durable saved membership keeps PDF access past session expiry."""
        with connect(self.database_url) as connection:
            _lock(connection)
            row = connection.execute("""SELECT e.*,s.*,d.action,c.profile AS current_profile,
                    COALESCE(v.history_badges,'[]'::jsonb) AS history_badges,
                    COALESCE(v.last_assessment,'null'::jsonb) AS change_assessment
                FROM feed_pipeline.exposures e JOIN feed_pipeline.sessions s ON s.session_id=e.session_id
                LEFT JOIN candidate_pipeline.candidates c ON c.candidate_id=e.candidate_id
                    AND c.revision=e.profile_revision AND c.state='ready'
                LEFT JOIN feed_pipeline.decisions d ON d.exposure_id=e.exposure_id
                LEFT JOIN feed_pipeline.visited v ON v.owner_id=e.owner_id AND v.vacancy_id=e.vacancy_id
                    AND v.candidate_id=e.candidate_id
                WHERE e.exposure_id=%s AND e.owner_id=%s AND e.vacancy_id=%s FOR UPDATE OF e""", (exposure_id, owner_id, vacancy_id)).fetchone()
            if row is None:
                raise NotFoundError("feed exposure not found")
            saved = connection.execute("""SELECT 1 FROM feed_pipeline.saved
                WHERE owner_id=%s AND vacancy_id=%s AND candidate_id=%s AND exposure_id=%s""",
                (owner_id, vacancy_id, row["candidate_id"], exposure_id)).fetchone()
            if saved is not None:
                vacancy = self._owner_vacancy(connection, owner_id, vacancy_id)
                if not self._candidate_ready(connection, vacancy, row["candidate_id"], row["profile_revision"], allow_blocked=True):
                    raise ConflictError("saved exposure source is stale or withdrawn")
                return self._card(row, row["action"], list(row["history_badges"] or []))
            stale = self._validate_session(connection, row, allow_decided=True)
            if row["candidate_id"] in stale:
                raise ConflictError("feed exposure source is stale or withdrawn")
            return self._card(row, row["action"], list(row["history_badges"] or []))

    def receipt(self, owner_id: UUID, vacancy_id: UUID, idempotency_key: UUID) -> FeedDecision:
        """Read an existing receipt before source freshness checks, enabling safe gateway replay."""
        with connect(self.database_url) as connection:
            query = "SELECT * FROM feed_pipeline.decisions WHERE owner_id=%s AND vacancy_id=%s AND idempotency_key=%s"
            args: list[object] = [owner_id, vacancy_id, idempotency_key]
            rows = connection.execute(query, args).fetchall()
            if not rows:
                raise NotFoundError("feed decision receipt not found")
            if len(rows) > 1:
                raise ConflictError("vacancy_id is required for this idempotency key")
            row = rows[0]
            return FeedDecision(receipt_id=row["receipt_id"], exposure_id=row["exposure_id"], vacancy_id=row["vacancy_id"],
                candidate_id=row["candidate_id"], action=row["action"], idempotency_key=row["idempotency_key"],
                saved=row["action"] == "like", visited=row["action"] == "dislike", replayed=True,
                history_badges=list(row["history_badges"] or []))

    def impression_receipt(self, owner_id: UUID, vacancy_id: UUID, idempotency_key: UUID) -> FeedImpression:
        """Read only this owner's prior acknowledgement, even after session invalidation."""
        with connect(self.database_url) as connection:
            row = connection.execute("""SELECT * FROM learning_pipeline.events WHERE owner_id=%s
                AND vacancy_id=%s AND event_type='impression' AND idempotency_key=%s""",
                (owner_id, vacancy_id, idempotency_key)).fetchone()
            if row is None:
                raise NotFoundError("impression receipt not found")
            return FeedImpression(event_id=row["event_id"], exposure_id=row["exposure_id"],
                                  recorded_at=row["recorded_at"], replayed=True)

    def impression(self, owner_id: UUID, vacancy_id: UUID, request: FeedImpressionRequest) -> FeedImpression:
        """Acknowledge one delivered, current card; idempotent replay does not create new views."""
        with connect(self.database_url) as connection:
            _lock(connection)
            previous = connection.execute("""SELECT * FROM learning_pipeline.events WHERE owner_id=%s
                AND event_type='impression' AND idempotency_key=%s""", (owner_id, request.idempotency_key)).fetchone()
            if previous:
                if previous["exposure_id"] != request.exposure_id or previous["vacancy_id"] != vacancy_id:
                    raise ConflictError("impression idempotency key belongs to another exposure")
                return FeedImpression(event_id=previous["event_id"], exposure_id=previous["exposure_id"],
                                      recorded_at=previous["recorded_at"], replayed=True)
            row = connection.execute("""SELECT e.*,s.* FROM feed_pipeline.exposures e
                JOIN feed_pipeline.sessions s USING(session_id)
                WHERE e.exposure_id=%s AND e.owner_id=%s AND e.vacancy_id=%s FOR UPDATE OF e,s""",
                (request.exposure_id, owner_id, vacancy_id)).fetchone()
            if row is None:
                raise NotFoundError("feed exposure not found")
            if row["served_at"] is None:
                raise ConflictError("feed exposure has not been delivered")
            stale = self._validate_session(connection, row, allow_decided=True)
            if row["candidate_id"] in stale:
                raise ConflictError("impression source is stale or withdrawn")
            event = LearningStore.event(connection, row, "impression", request.idempotency_key)
            return FeedImpression(event_id=event["event_id"], exposure_id=event["exposure_id"], recorded_at=event["recorded_at"])

    def decision(self, owner_id: UUID, vacancy_id: UUID, request: FeedDecisionRequest) -> FeedDecision:
        """Apply exactly one served action per exposure with owner-scoped idempotency."""
        with connect(self.database_url) as connection:
            _lock(connection)
            return self._decision_locked(connection, owner_id, vacancy_id, request)

    def _decision_locked(self, connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID,
                         request: FeedDecisionRequest) -> FeedDecision:
        """Share one atomic decision transition between ranked and manual exposures."""
        old = connection.execute("""SELECT * FROM feed_pipeline.decisions
            WHERE owner_id=%s AND vacancy_id=%s AND idempotency_key=%s FOR UPDATE""", (owner_id, vacancy_id, request.idempotency_key)).fetchone()
        if old is not None:
            if old["exposure_id"] != request.exposure_id or old["action"] != request.action:
                raise ConflictError("idempotency key conflicts with an existing decision")
            return FeedDecision(receipt_id=old["receipt_id"], exposure_id=old["exposure_id"], vacancy_id=old["vacancy_id"],
                candidate_id=old["candidate_id"], action=old["action"], idempotency_key=old["idempotency_key"],
                saved=old["action"] == "like", visited=old["action"] == "dislike", replayed=True,
                history_badges=list(old["history_badges"] or []))
        row = connection.execute("""SELECT e.*,s.*,d.action AS existing_action,
                COALESCE(v.history_badges,'[]'::jsonb) AS old_badges
            FROM feed_pipeline.exposures e JOIN feed_pipeline.sessions s ON s.session_id=e.session_id
            LEFT JOIN feed_pipeline.decisions d ON d.exposure_id=e.exposure_id
            LEFT JOIN feed_pipeline.visited v ON v.owner_id=e.owner_id AND v.vacancy_id=e.vacancy_id
                AND v.candidate_id=e.candidate_id
            WHERE e.exposure_id=%s AND e.owner_id=%s AND e.vacancy_id=%s FOR UPDATE OF e,s""", (request.exposure_id, owner_id, vacancy_id)).fetchone()
        if row is None:
            raise NotFoundError("feed exposure not found")
        if row["existing_action"] is not None:
            raise ConflictError("feed exposure already has a decision")
        if row["served_at"] is None:
            raise ConflictError("feed exposure has not been served")
        self._validate_session(connection, row)
        vacancy = self._owner_vacancy(connection, owner_id, row["vacancy_id"])
        if not self._candidate_ready(connection, vacancy, row["candidate_id"], row["profile_revision"]):
            raise ConflictError("candidate source is stale or withdrawn")
        badges = list(row["old_badges"] or [])
        if "previously_visited" not in badges and row["old_badges"]:
            badges.append("previously_visited")
        baseline = self._profile_snapshot(connection, row["candidate_id"], row["profile_revision"])
        main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (row["main_run_id"],)).fetchone()
        if main is None and row.get("source") != "showcase":
            raise ConflictError("feed main snapshot is missing")
        if ((main is not None and main["policy_hash"] != row["main_policy_hash"])
                or scoring_policy_hash(ScoringPolicy()) != row["main_policy_hash"]):
            raise ConflictError("feed main policy is stale")
        policy, policy_digest = self._policy_snapshot(connection, row["vacancy_id"], main or {})
        receipt_id = uuid4()
        connection.execute("""INSERT INTO feed_pipeline.decisions
            (receipt_id,owner_id,vacancy_id,exposure_id,candidate_id,idempotency_key,action,profile_revision,
             baseline,baseline_origin,frozen_policy,frozen_policy_hash,history_badges)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'exposure',%s,%s,%s)""", (
                receipt_id, owner_id, row["vacancy_id"], request.exposure_id, row["candidate_id"], request.idempotency_key,
                request.action, row["profile_revision"], Jsonb(baseline), Jsonb(policy), policy_digest, Jsonb(badges),
        ))
        LearningStore.event(connection, row, request.action, request.idempotency_key)
        if row.get("source", "tinder") != "showcase":
            PreferenceStore.enqueue(connection, row)
        if request.action == "like":
            connection.execute("""INSERT INTO feed_pipeline.saved(owner_id,vacancy_id,candidate_id,profile_revision,exposure_id,card)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,vacancy_id,candidate_id) DO UPDATE SET
                profile_revision=EXCLUDED.profile_revision,exposure_id=EXCLUDED.exposure_id,card=EXCLUDED.card,saved_at=now()""",
                (owner_id, row["vacancy_id"], row["candidate_id"], row["profile_revision"], request.exposure_id, Jsonb(row["card"])))
            state = "attractive"
        else:
            connection.execute("""INSERT INTO feed_pipeline.visited
                (owner_id,vacancy_id,candidate_id,profile_revision,exposure_id,baseline_origin,baseline,frozen_policy,evaluated_revision,last_assessment,history_badges,visited_at)
                VALUES (%s,%s,%s,%s,%s,'exposure',%s,%s,%s,%s,%s,now())
                ON CONFLICT(owner_id,vacancy_id,candidate_id) DO UPDATE SET profile_revision=EXCLUDED.profile_revision,
                exposure_id=EXCLUDED.exposure_id,baseline_origin='exposure',baseline=EXCLUDED.baseline,
                frozen_policy=EXCLUDED.frozen_policy,
                evaluated_revision=EXCLUDED.evaluated_revision,last_assessment=EXCLUDED.last_assessment,
                history_badges=EXCLUDED.history_badges,visited_at=now(),updated_at=now()""",
                (owner_id, row["vacancy_id"], row["candidate_id"], row["profile_revision"], request.exposure_id,
                 Jsonb(baseline), Jsonb(policy), row["profile_revision"], Jsonb({"event": "dislike", "refresh": False}), Jsonb(badges)))
            connection.execute("""INSERT INTO feed_pipeline.visited_history
                (owner_id,vacancy_id,candidate_id,baseline_revision,evaluated_revision,baseline,frozen_policy,assessment,event_type)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'dislike')""", (
                owner_id, row["vacancy_id"], row["candidate_id"], row["profile_revision"], row["profile_revision"],
                Jsonb(baseline), Jsonb(policy), Jsonb({"event": "dislike", "refresh": False}),
            ))
            state = "visited"
        connection.execute("""INSERT INTO matching_pipeline.selection_states(vacancy_id,candidate_id,state)
            VALUES (%s,%s,%s) ON CONFLICT(vacancy_id,candidate_id) DO UPDATE SET state=EXCLUDED.state,updated_at=now()""",
            (row["vacancy_id"], row["candidate_id"], state))
        connection.execute("""INSERT INTO matching_pipeline.controls(vacancy_id) VALUES (%s)
            ON CONFLICT DO NOTHING""", (row["vacancy_id"],))
        connection.execute("UPDATE matching_pipeline.controls SET revision=revision+1 WHERE vacancy_id=%s", (row["vacancy_id"],))
        CombineStore(self.database_url)._queue(connection, row["vacancy_id"])
        from matching_engine.hypothesis_store import HypothesisStore
        HypothesisStore.seal(connection, row, vacancy)
        return FeedDecision(receipt_id=receipt_id, exposure_id=request.exposure_id, vacancy_id=row["vacancy_id"],
            candidate_id=row["candidate_id"], action=request.action, idempotency_key=request.idempotency_key,
            saved=request.action == "like", visited=request.action == "dislike", history_badges=badges)

    @staticmethod
    def _showcase_receipt_locked(connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID,
                                 key: UUID) -> ShowcaseLikeReceipt | None:
        """Read only a manual-like receipt; a shared key cannot change operation origin."""
        row = connection.execute("""SELECT d.*,e.kind,e.card FROM feed_pipeline.decisions d
            JOIN feed_pipeline.exposures e USING(exposure_id)
            WHERE d.owner_id=%s AND d.vacancy_id=%s AND d.idempotency_key=%s""",
            (owner_id, vacancy_id, key)).fetchone()
        if row is None:
            return None
        if row["kind"] != "showcase" or row["action"] != "like":
            raise ConflictError("idempotency key belongs to another decision source")
        return ShowcaseLikeReceipt(receipt_id=row["receipt_id"], exposure_id=row["exposure_id"],
            vacancy_id=row["vacancy_id"], candidate_id=row["candidate_id"], action="like",
            idempotency_key=key, saved=True, visited=False, replayed=True,
            history_badges=row["history_badges"], profile_revision=row["profile_revision"],
            strict_filter_warnings=row["card"].get("strict_filter_warnings", []),
            mismatch_token=row["card"].get("mismatch_token"))

    def showcase_receipt(self, owner_id: UUID, vacancy_id: UUID, key: UUID) -> ShowcaseLikeReceipt:
        """Read an immutable owned receipt even after source withdrawal or vacancy closure."""
        with connect(self.database_url) as connection:
            result = self._showcase_receipt_locked(connection, owner_id, vacancy_id, key)
            if result is None:
                raise NotFoundError("showcase like receipt not found")
            return result

    @staticmethod
    def _showcase_authority(connection: psycopg.Connection, candidate_id: UUID, revision: int) -> None:
        """Hold known API source consent/ACK locks until the manual decision commits.

        Legacy pipeline-only profiles retain the existing feed compatibility rule.
        The public gateway independently checks authoritative source watermarks.
        """
        if not connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]:
            return
        profile = connection.execute("""SELECT revision,processing_consent,pool_consent
            FROM public.candidate_profiles WHERE candidate_id=%s FOR SHARE""", (candidate_id,)).fetchone()
        if profile is None:
            return
        ack = connection.execute("""SELECT source_revision,projected_revision,state
            FROM public.candidate_profile_outbox WHERE candidate_id=%s
            ORDER BY source_revision DESC,created_at DESC LIMIT 1 FOR SHARE""", (candidate_id,)).fetchone()
        if not (profile["processing_consent"] and profile["pool_consent"] and ack
                and ack["state"] == "delivered" and ack["source_revision"] == profile["revision"]
                and ack["projected_revision"] == revision):
            raise ConflictError("showcase candidate source is stale or withdrawn")

    def showcase_like(self, owner_id: UUID, vacancy_id: UUID, request: ShowcaseLikeRequest) -> ShowcaseLikeReceipt:
        """Atomically allocate one manual exposure and reuse the ordinary like transition.

        No main/rerank run is fabricated. Suitability mismatches require a source-bound
        acknowledgement; visited/pending and source guards cannot be overridden.
        """
        with connect(self.database_url) as connection:
            # Keep the established pipeline lock order while bounding contention.
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL statement_timeout = '10s'")
            _lock(connection)
            prior = self._showcase_receipt_locked(connection, owner_id, vacancy_id, request.idempotency_key)
            if prior is not None:
                if (prior.candidate_id != request.candidate_id or prior.profile_revision != request.profile_revision
                        or prior.mismatch_token != request.mismatch_token):
                    raise ConflictError("showcase like idempotency key conflicts with its source")
                return prior
            vacancy = self._owner_vacancy(connection, owner_id, vacancy_id)
            self._showcase_authority(connection, request.candidate_id, request.profile_revision)
            if not self._candidate_ready(connection, vacancy, request.candidate_id, request.profile_revision):
                raise ConflictError("showcase candidate is stale, withdrawn or already blocked for this vacancy")
            self._profile_snapshot(connection, request.candidate_id, request.profile_revision)
            ready = CombineStore._vacancy(connection, vacancy_id)
            candidates = CombineStore._candidates(connection, request.candidate_id)
            if not candidates:
                raise ConflictError("showcase candidate source is missing")
            candidate = candidates[0]
            suitability = strict_filter(ready, candidate)
            token = digest({"owner": str(owner_id), "vacancy": str(vacancy_id),
                "vacancy_revision": ready.revision, "candidate": str(request.candidate_id),
                "profile_revision": request.profile_revision, "warnings": suitability.reasons,
                "policy": scoring_policy_hash(ScoringPolicy())}) if suitability.reasons else None
            if suitability.reasons and request.mismatch_token != token:
                raise ShowcaseMismatchError(ShowcaseMismatchWarning(candidate_id=request.candidate_id,
                    profile_revision=request.profile_revision, vacancy_revision=ready.revision,
                    warnings=suitability.reasons, mismatch_token=token))
            if request.mismatch_token != token:
                raise ConflictError("showcase mismatch acknowledgement is obsolete")
            card = CandidateCard(candidate_id=candidate.candidate_id, profile_revision=candidate.profile_revision,
                position=1, display_name=candidate.display_name, city=candidate.city, salary=candidate.salary,
                category=candidate.category, rank=candidate.rank, confirmed_strength=candidate.confirmed_strength,
                resume_url=candidate.resume_url, explanation=score_candidate(ready, candidate, ScoringPolicy()),
                filter_factors=suitability.factors).model_dump(mode="json")
            card.update(main_position=1, rerank_position=None, mode="manual", rerank_score=None,
                        artifact_hash=None, rerank_contributions={},
                        strict_filter_warnings=suitability.reasons, mismatch_token=token)
            session_id, exposure_id = uuid4(), uuid4()
            policy_digest = self._policy_snapshot(connection, vacancy_id, {})[1]
            generation = connection.execute("SELECT pool_generation FROM matching_pipeline.settings WHERE singleton").fetchone()["pool_generation"]
            manual_identity = digest({"source": "showcase", "version": 1, "ranking": "manual"})
            connection.execute("""INSERT INTO feed_pipeline.sessions
                (session_id,owner_id,vacancy_id,vacancy_revision,schema_version,schema_hash,pool_generation,
                 main_run_id,rerank_run_id,main_policy_hash,policy_hash,feature_schema_hash,tail_policy_hash,
                 artifact_hash,include_reserve,expires_at,source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,NULL,false,now()+interval '24 hours','showcase')""",
                (session_id, owner_id, vacancy_id, vacancy["revision"], vacancy["schema_version"], vacancy["schema_hash"],
                 generation, scoring_policy_hash(ScoringPolicy()), policy_digest, manual_identity, manual_identity))
            connection.execute("""INSERT INTO feed_pipeline.exposures
                (exposure_id,session_id,owner_id,vacancy_id,candidate_id,profile_revision,position,kind,card,served_at)
                VALUES (%s,%s,%s,%s,%s,%s,1,'showcase',%s,now())""",
                (exposure_id, session_id, owner_id, vacancy_id, request.candidate_id, request.profile_revision, Jsonb(card)))
            exposure = {"exposure_id": exposure_id, "owner_id": owner_id, "vacancy_id": vacancy_id}
            LearningStore.event(connection, exposure, "allocated")
            LearningStore.event(connection, exposure, "delivered")
            result = self._decision_locked(connection, owner_id, vacancy_id,
                FeedDecisionRequest(exposure_id=exposure_id, action="like", idempotency_key=request.idempotency_key))
            return ShowcaseLikeReceipt(**result.model_dump(), profile_revision=request.profile_revision,
                                       strict_filter_warnings=suitability.reasons, mismatch_token=token)

    def saved(self, owner_id: UUID, vacancy_id: UUID, limit: int = 50, offset: int = 0,
              candidate_id: UUID | None = None, cursor: str | None = None) -> FeedListPage[SavedCard]:
        """List saved candidates with immutable cards and optional keyset pagination."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid saved pagination")
        if cursor is not None and offset:
            raise ValueError("cursor cannot be combined with offset")
        with connect(self.database_url) as connection:
            owner_vacancy = connection.execute("SELECT vacancy_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s AND owner_id=%s",
                                               (vacancy_id, owner_id)).fetchone()
            if owner_vacancy is None:
                raise NotFoundError("owned vacancy not found")
            try:
                vacancy = self._owner_vacancy(connection, owner_id, vacancy_id)
            except NotFoundError:
                vacancy = None
            source = connection.execute("SELECT revision FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s AND owner_id=%s",
                                        (vacancy_id, owner_id)).fetchone()
            if source is None:
                raise NotFoundError("owned vacancy not found")
            snapshot_at = connection.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            keyset = None
            if cursor is not None:
                keyset = _decode_list_cursor(cursor, "saved", owner_id, vacancy_id, candidate_id)
                if keyset[3] != source["revision"]:
                    raise ConflictError("saved listing source changed")
                snapshot_at = keyset[0]
            where_keyset = "" if keyset is None else " AND (s.saved_at < %s OR (s.saved_at=%s AND s.candidate_id>%s))"
            rows = connection.execute("""SELECT s.*,e.session_id,e.kind,e.served_at,e.position,e.card,c.profile AS current_profile,
                    COALESCE(v.history_badges,'[]'::jsonb) AS history_badges,
                    COALESCE(v.last_assessment,'null'::jsonb) AS change_assessment,
                    d.action FROM feed_pipeline.saved s JOIN feed_pipeline.exposures e ON e.exposure_id=s.exposure_id
                    LEFT JOIN candidate_pipeline.candidates c ON c.candidate_id=e.candidate_id
                        AND c.revision=e.profile_revision AND c.state='ready'
                    LEFT JOIN feed_pipeline.visited v ON v.owner_id=s.owner_id AND v.vacancy_id=s.vacancy_id AND v.candidate_id=s.candidate_id
                    LEFT JOIN feed_pipeline.decisions d ON d.exposure_id=e.exposure_id WHERE s.owner_id=%s AND s.vacancy_id=%s
                    AND (%s::uuid IS NULL OR s.candidate_id=%s)
                    AND s.saved_at<=%s""" + where_keyset + " ORDER BY s.saved_at DESC,s.candidate_id LIMIT %s OFFSET %s""",
                (owner_id, vacancy_id, candidate_id, candidate_id, snapshot_at, *(() if keyset is None else (keyset[1], keyset[1], keyset[2])), limit + 1, offset)).fetchall()
            has_more = len(rows) > limit
            items = []
            for row in rows[:limit]:
                live = vacancy is not None and self._candidate_ready(connection, vacancy, row["candidate_id"], row["profile_revision"], allow_blocked=True)
                items.append(SavedCard(vacancy_id=row["vacancy_id"], candidate_id=row["candidate_id"], profile_revision=row["profile_revision"],
                    exposure_id=row["exposure_id"], saved_at=row["saved_at"], card=self._card(row, row["action"], list(row["history_badges"] or [])) if live else None))
            next_cursor = _encode_list_cursor("saved", snapshot_at, rows[limit - 1]["saved_at"], rows[limit - 1]["candidate_id"], source["revision"],
                                              owner_id, vacancy_id, candidate_id) if has_more else None
            return FeedListPage[SavedCard](items=items, limit=limit, offset=offset, has_more=has_more,
                                           cursor=cursor, next_cursor=next_cursor, snapshot_at=snapshot_at)

    def saved_candidate(self, owner_id: UUID, vacancy_id: UUID, candidate_id: UUID) -> SavedCard:
        """Read one durable saved membership for the recruitment service without scanning the shortlist."""
        page = self.saved(owner_id, vacancy_id, limit=1, candidate_id=candidate_id)
        if not page.items:
            raise NotFoundError("saved candidate not found")
        return page.items[0]

    def visited(self, owner_id: UUID, vacancy_id: UUID, limit: int = 50, offset: int = 0,
                cursor: str | None = None) -> FeedListPage[VisitedCard]:
        """List visited baselines with a stable updated_at/candidate_id keyset."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid visited pagination")
        if cursor is not None and offset:
            raise ValueError("cursor cannot be combined with offset")
        with connect(self.database_url) as connection:
            owner_vacancy = connection.execute("SELECT vacancy_id FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s AND owner_id=%s",
                                               (vacancy_id, owner_id)).fetchone()
            if owner_vacancy is None:
                raise NotFoundError("owned vacancy not found")
            source = connection.execute("SELECT revision FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s AND owner_id=%s",
                                        (vacancy_id, owner_id)).fetchone()
            if source is None:
                raise NotFoundError("owned vacancy not found")
            snapshot_at = connection.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
            keyset = None
            if cursor is not None:
                keyset = _decode_list_cursor(cursor, "visited", owner_id, vacancy_id)
                if keyset[3] != source["revision"]:
                    raise ConflictError("visited listing source changed")
                snapshot_at = keyset[0]
            where_keyset = "" if keyset is None else " AND (v.updated_at < %s OR (v.updated_at=%s AND v.candidate_id>%s))"
            rows = connection.execute("""SELECT v.*,COALESCE(ss.state,'visited') AS selection_state
                FROM feed_pipeline.visited v LEFT JOIN matching_pipeline.selection_states ss
                  ON ss.vacancy_id=v.vacancy_id AND ss.candidate_id=v.candidate_id
                WHERE v.owner_id=%s AND v.vacancy_id=%s
                  AND v.updated_at<=%s""" + where_keyset + " ORDER BY v.updated_at DESC,v.candidate_id LIMIT %s OFFSET %s""",
                (owner_id, vacancy_id, snapshot_at, *(() if keyset is None else (keyset[1], keyset[1], keyset[2])), limit + 1, offset)).fetchall()
            has_more = len(rows) > limit
            items = [VisitedCard(vacancy_id=row["vacancy_id"], candidate_id=row["candidate_id"], profile_revision=row["profile_revision"],
                evaluated_revision=row["evaluated_revision"], state=row["selection_state"],
                exposure_id=row["exposure_id"], baseline_origin=row["baseline_origin"], visited_at=row["visited_at"],
                updated_at=row["updated_at"], history_badges=list(row["history_badges"] or []), assessment=row["last_assessment"])
                for row in rows[:limit]]
            next_cursor = _encode_list_cursor("visited", snapshot_at, rows[limit - 1]["updated_at"], rows[limit - 1]["candidate_id"], source["revision"],
                                              owner_id, vacancy_id) if has_more else None
            return FeedListPage[VisitedCard](items=items, limit=limit, offset=offset, has_more=has_more,
                                             cursor=cursor, next_cursor=next_cursor, snapshot_at=snapshot_at)

    @staticmethod
    def _legacy_baseline(row: dict) -> dict[str, Any]:
        """Build a current text-free structural snapshot for old manual visited states."""
        return feed_policy.snapshot(row.get("profile") or {})

    def seed_legacy_manualvisited(self, connection: psycopg.Connection) -> int:
        """Migrate old manual visited states with current facts and no fabricated historic time."""
        rows = connection.execute("""SELECT ss.vacancy_id,ss.candidate_id,c.revision,c.profile,v.owner_id FROM matching_pipeline.selection_states ss
                JOIN vacancy_pipeline.vacancies v ON v.vacancy_id=ss.vacancy_id
                JOIN candidate_pipeline.candidates c ON c.candidate_id=ss.candidate_id
                WHERE ss.state='visited'""").fetchall()
        count = 0
        for row in rows:
            if row["revision"] is None:
                continue
            result = connection.execute("""INSERT INTO feed_pipeline.visited
                (owner_id,vacancy_id,candidate_id,profile_revision,baseline_origin,baseline,frozen_policy,evaluated_revision,last_assessment,history_badges,visited_at)
                VALUES (%s,%s,%s,%s,'legacy_migration',%s,%s,%s,%s,%s,NULL)
                ON CONFLICT(owner_id,vacancy_id,candidate_id) DO NOTHING RETURNING candidate_id""", (
                    row["owner_id"], row["vacancy_id"], row["candidate_id"], row["revision"],
                    Jsonb(self._legacy_baseline(row)), Jsonb(feed_policy.default_policy()), row["revision"], Jsonb({"event": "legacy_migration", "refresh": False}), Jsonb(["legacy_migration"]),
            )).fetchone()
            count += result is not None
            if result is not None:
                connection.execute("""INSERT INTO feed_pipeline.visited_history
                    (owner_id,vacancy_id,candidate_id,baseline_revision,evaluated_revision,baseline,frozen_policy,assessment,event_type)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'legacy_migration')""", (
                    row["owner_id"], row["vacancy_id"], row["candidate_id"], row["revision"], row["revision"],
                    Jsonb(self._legacy_baseline(row)), Jsonb(feed_policy.default_policy()),
                    Jsonb({"event": "legacy_migration", "refresh": False}),
                ))
        return count

    def reconsider(self, connection: psycopg.Connection, candidate_id: UUID) -> int:
        """Assess newer profile facts against the frozen baseline and mark only N>K refreshes."""
        rows = connection.execute("""SELECT v.owner_id,v.vacancy_id,v.profile_revision,v.frozen_policy,v.baseline,
                v.history_badges,v.evaluated_revision,p.revision,c.profile
            FROM feed_pipeline.visited v JOIN vacancy_pipeline.vacancies vv
                ON vv.vacancy_id=v.vacancy_id AND vv.owner_id=v.owner_id
            JOIN matching_pipeline.selection_states ss ON ss.vacancy_id=v.vacancy_id
                AND ss.candidate_id=v.candidate_id AND ss.state='visited'
            JOIN candidate_pipeline.pool p ON p.candidate_id=v.candidate_id
            JOIN candidate_pipeline.candidates c ON c.candidate_id=p.candidate_id AND c.revision=p.revision
            WHERE v.candidate_id=%s AND p.revision>v.evaluated_revision FOR UPDATE OF v""", (candidate_id,)).fetchall()
        changed = 0
        for row in rows:
            current = feed_policy.snapshot(row["profile"])
            policy = row["frozen_policy"] or feed_policy.default_policy()
            assessment = feed_policy.assess(row["baseline"], current, policy)
            badges = list(row["history_badges"] or [])
            refreshed = bool(assessment.get("refresh"))
            if refreshed and "visited_updated" not in badges:
                badges.append("visited_updated")
            connection.execute("""UPDATE feed_pipeline.visited SET evaluated_revision=%s,last_assessment=%s,
                history_badges=%s,updated_at=now() WHERE owner_id=%s AND vacancy_id=%s AND candidate_id=%s""",
                               (row["revision"], Jsonb(assessment), Jsonb(badges), row["owner_id"], row["vacancy_id"], candidate_id))
            if refreshed:
                changed_state = connection.execute("""UPDATE matching_pipeline.selection_states SET state='visited_updated',updated_at=now()
                    WHERE vacancy_id=%s AND candidate_id=%s AND state='visited'""", (row["vacancy_id"], candidate_id)).rowcount
                if changed_state:
                    connection.execute("""INSERT INTO matching_pipeline.controls(vacancy_id) VALUES (%s)
                        ON CONFLICT DO NOTHING""", (row["vacancy_id"],))
                    connection.execute("UPDATE matching_pipeline.controls SET revision=revision+1 WHERE vacancy_id=%s",
                                       (row["vacancy_id"],))
                connection.execute("""INSERT INTO feed_pipeline.visited_history
                    (owner_id,vacancy_id,candidate_id,baseline_revision,evaluated_revision,baseline,frozen_policy,assessment,event_type)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'visited_updated')""", (
                    row["owner_id"], row["vacancy_id"], candidate_id, row["profile_revision"], row["revision"],
                    Jsonb(row["baseline"]), Jsonb(policy), Jsonb(assessment),
                ))
            changed += 1
        return changed
