"""Durable matched-pair exploration with frozen conditional probabilities and delivery slots."""

from __future__ import annotations

import math
import random
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import CandidateCard, MatchCandidate, ScoringPolicy
from matching_engine.combine_scoring import score_candidate, strict_filter
from matching_engine.combine_store import _lock
from matching_engine.database import connect
from matching_engine.hypothesis_policy import (
    MAX_NUISANCE_DISTANCE,
    MIN_CONTRAST,
    comparable_pairs,
    policy_hash,
    summarize,
)
from matching_engine.preference_model import apply
from matching_engine.preference_store import PreferenceStore
from matching_engine.rerank_features import build_features
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import RerankArtifact, scaled_features
from matching_engine.rerank_store import _schema
from matching_engine.store import ConflictError
from matching_engine.vacancy_models import VacancyReady

MAX_SCAN = 5000
MAX_COMPARISON_POOL = 200
ORDINARY_PER_PAIR = 18
MIN_PRIORITY = .000001


def allocation_hash() -> str:
    """Bind candidate caps, credit rate and the exact probability construction."""
    return digest({"comparison_policy": policy_hash(), "version": "paired-allocation-v1", "session_source": "tinder", "scan_cap": MAX_SCAN,
        "comparison_cap": MAX_COMPARISON_POOL, "ordinary_per_pair": ORDINARY_PER_PAIR,
        "min_priority": MIN_PRIORITY, "order_probability": .5, "scope": "conditional_feasible_pair_at_budget_opportunity"})


class HypothesisStore:
    """Explore only current strictly eligible unserved candidates; never mutate facts or filters."""

    def __init__(self, database_url: str) -> None:
        """Use the same PostgreSQL transaction boundary as feed delivery and explicit reactions."""
        self.database_url = database_url

    @staticmethod
    def ensure(connection: psycopg.Connection, session: dict) -> dict:
        """Bind experiments to exact vacancy/baseline context and preference reset epoch."""
        if session.get("source", "tinder") != "tinder":
            raise ConflictError("hypotheses require a ranked feed session")
        preference = PreferenceStore.ensure(connection, session)
        context_hash = digest({"identity": preference["identity"], "preference_epoch": preference["epoch"],
                               "hypothesis_policy": allocation_hash()})
        control = connection.execute("SELECT * FROM hypothesis_pipeline.controls WHERE vacancy_id=%s FOR UPDATE",
                                     (session["vacancy_id"],)).fetchone()
        if control is None:
            return connection.execute("""INSERT INTO hypothesis_pipeline.controls(vacancy_id,owner_id,context_hash)
                VALUES(%s,%s,%s) RETURNING *""", (session["vacancy_id"], session["owner_id"], context_hash)).fetchone()
        if control["context_hash"] != context_hash:
            HypothesisStore._cancel(connection, control, "context_changed")
            control = connection.execute("""UPDATE hypothesis_pipeline.controls SET context_hash=%s,epoch=epoch+1,
                revision=revision+1,budget_start=clock_timestamp(),last_search_block=-1,search_report='{}',
                updated_at=clock_timestamp() WHERE vacancy_id=%s RETURNING *""", (context_hash, session["vacancy_id"])).fetchone()
        return control

    @staticmethod
    def _cancel(connection: psycopg.Connection, control: dict, reason: str, pair_id: UUID | None = None) -> None:
        """Invalidate evidence; retain delivered receipts and suppress newly introduced undelivered reserve."""
        pairs = connection.execute("""UPDATE hypothesis_pipeline.pairs SET invalidated_at=clock_timestamp(),
            invalidation_reason=%s,parameters=NULL WHERE vacancy_id=%s AND invalidated_at IS NULL
            AND (%s::uuid IS NULL OR pair_id=%s)
            AND (%s<>'pause' OR EXISTS(SELECT 1 FROM feed_pipeline.exposures e
                WHERE e.exposure_id IN (test_exposure,control_exposure) AND e.served_at IS NULL))
            RETURNING test_exposure,control_exposure""",
            (reason, control["vacancy_id"], pair_id, pair_id, reason)).fetchall()
        ids = [row[key] for row in pairs for key in ("test_exposure", "control_exposure")]
        if ids:
            connection.execute("""UPDATE hypothesis_pipeline.batches b SET report=NULL,invalidated_at=clock_timestamp()
                WHERE b.invalidated_at IS NULL AND EXISTS(SELECT 1 FROM hypothesis_pipeline.pairs p
                    WHERE p.pair_id=ANY(b.pair_ids) AND p.invalidated_at IS NOT NULL)""")
            connection.execute("""UPDATE feed_pipeline.exposures SET
                exploration_suppressed=COALESCE((card->>'exploration_added')::boolean,false),
                card=card - ARRAY['hypothesis_pair_id','hypothesis_id','hypothesis_role','hypothesis_policy_hash',
                    'selection_probability','order_probability','probability_scope','hypothesis_parameters']::text[]
                WHERE exposure_id=ANY(%s::uuid[]) AND served_at IS NULL""", (ids,))

    @staticmethod
    def withdraw(connection: psycopg.Connection, candidate_id: UUID) -> None:
        """Remove fact-derived pair parameters and conclusions when either participant withdraws."""
        for row in connection.execute("""SELECT p.pair_id,c.* FROM hypothesis_pipeline.pairs p
            JOIN hypothesis_pipeline.controls c USING(vacancy_id) WHERE invalidated_at IS NULL
            AND (test_candidate=%s OR control_candidate=%s)""", (candidate_id, candidate_id)).fetchall():
            HypothesisStore._cancel(connection, row, "consent_withdrawn", row["pair_id"])

    @staticmethod
    def _pool(connection: psycopg.Connection, session: dict, vacancy: dict) -> tuple[list[dict], dict]:
        """Recompute frozen full-snapshot strict eligibility, then bound the comparable search pool.

        Beyond-top-k candidates are explicit exploration inputs, never relabeled
        main selections. Source consent and exact revisions are rechecked.
        """
        from matching_engine.feed_store import FeedStore
        main = connection.execute("SELECT * FROM matching_pipeline.runs WHERE run_id=%s", (session["main_run_id"],)).fetchone()
        run = connection.execute("SELECT mode FROM rerank_pipeline.runs WHERE run_id=%s", (session["rerank_run_id"],)).fetchone()
        if run is None or run["mode"] != "learned":
            return [], {"reason": "approved_baseline_unavailable", "complete": False}
        if len(main["candidates_snapshot"]) > MAX_SCAN:
            return [], {"reason": "snapshot_scan_limit", "complete": False}
        artifact_row = connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s",
                                          (session["artifact_hash"],)).fetchone()
        if not artifact_row:
            return [], {"reason": "baseline_absent", "complete": False}
        artifact = RerankArtifact.model_validate(artifact_row["artifact"])
        schema, target = _schema(connection, main), VacancyReady.model_validate(main["vacancy_snapshot"])
        preference = PreferenceStore.ensure(connection, session)
        active = PreferenceStore.active(connection, preference)
        used = {str(row["candidate_id"]) for row in connection.execute("""SELECT candidate_id FROM feed_pipeline.exposures
            WHERE owner_id=%s AND vacancy_id=%s AND (served_at IS NOT NULL OR card->>'hypothesis_pair_id' IS NOT NULL)
            UNION SELECT test_candidate FROM hypothesis_pipeline.pairs WHERE vacancy_id=%s AND invalidated_at IS NULL
            UNION SELECT control_candidate FROM hypothesis_pipeline.pairs WHERE vacancy_id=%s AND invalidated_at IS NULL""",
            (session["owner_id"], session["vacancy_id"], session["vacancy_id"], session["vacancy_id"])).fetchall()}
        ranked = []
        for item in main["candidates_snapshot"]:
            candidate = MatchCandidate.model_validate(item)
            if str(candidate.candidate_id) in main["excluded_ids"] or not strict_filter(target, candidate).passed:
                continue
            ranked.append((candidate, score_candidate(target, candidate, ScoringPolicy())))
        ranked.sort(key=lambda pair: (-pair[1].score, -pair[1].rating_score, -(pair[1].strict_score or 0.),
                                     -(pair[1].semantic_cosine or 0.), str(pair[0].candidate_id)))
        eligible = []
        original_positions = {}
        for position, (candidate, score) in enumerate(ranked, start=1):
            original_positions[str(candidate.candidate_id)] = position
            if str(candidate.candidate_id) in used:
                continue
            if not FeedStore._candidate_ready(connection, vacancy, candidate.candidate_id, candidate.profile_revision):
                continue
            if not PreferenceStore._consent(connection, [candidate.candidate_id], preference["identity"]["source"]):
                continue
            eligible.append((candidate, score))
        eligible.sort(key=lambda pair: (-pair[1].score, str(pair[0].candidate_id)))
        existing = {str(row["candidate_id"]): row for row in connection.execute(
            "SELECT * FROM rerank_pipeline.cards WHERE run_id=%s", (session["rerank_run_id"],)).fetchall()}
        pool = []
        scale = max(1., math.sqrt(math.fsum(value * value for value in artifact.coefficients)))
        for position, (candidate, score) in enumerate(eligible[:MAX_COMPARISON_POOL], start=1):
            cid = str(candidate.candidate_id)
            source = existing.get(cid)
            main_position = source["card"]["main_position"] if source else original_positions[cid]
            card = CandidateCard(candidate_id=candidate.candidate_id, profile_revision=candidate.profile_revision,
                position=main_position, display_name=candidate.display_name, city=candidate.city, salary=candidate.salary,
                category=candidate.category, rank=candidate.rank, confirmed_strength=candidate.confirmed_strength,
                resume_url=candidate.resume_url, explanation=score, filter_factors=strict_filter(target, candidate).factors)
            vector = build_features(target, candidate, card, schema)
            features = {"names": list(vector.names), "values": list(vector.values), "available": list(vector.available),
                        "schema_hash": vector.schema_hash}
            contributions = dict(zip(vector.names, (a*b for a,b in zip(artifact.coefficients, scaled_features(vector, artifact), strict=True)), strict=True))
            r0_score = math.fsum(contributions.values())
            data = dict(source["card"]) if source else {**card.model_dump(mode="json"), "main_position": main_position,
                "rerank_position": None, "mode": "learned", "rerank_score": r0_score, "rerank_contributions": contributions,
                "artifact_hash": session["artifact_hash"], "activation_revision": session["activation_revision"],
                "approval_receipt_hash": preference["identity"]["approval_receipt_hash"],
                "activation_kind": "manual_override" if preference["identity"]["approval_receipt_hash"] else "gated"}
            # Reuse immutable top-k samples; never repair or overwrite them after feedback.
            sample = connection.execute("SELECT features,feature_hash,withdrawn_at FROM learning_pipeline.samples WHERE main_run_id=%s AND candidate_id=%s",
                                        (main["run_id"], candidate.candidate_id)).fetchone()
            if sample:
                if sample["features"] is None or digest(sample["features"]) != sample["feature_hash"]:
                    continue
                features = sample["features"]
            personal = apply(features, data["rerank_score"], active["model"], artifact_row["artifact"]) if active else None
            pool.append({"candidate_id": cid, "features": features, "score": personal["score"] if personal else data["rerank_score"],
                         "snapshot": candidate.model_dump(mode="json"), "card": data, "kind": source["kind"] if source else "reserve",
                         "outside_top_k": source is None})
        return pool, {"complete": len(eligible) <= MAX_COMPARISON_POOL, "eligible_unserved": len(eligible), "score_scale": scale,
                      "comparison_pool": len(pool), "max_comparison_pool": MAX_COMPARISON_POOL,
                      "preference_model_hash": active["model_hash"] if active else None}

    @staticmethod
    def prepare(connection: psycopg.Connection, session: dict, after: int) -> dict:
        """Reserve at most one randomized pair per forward page, after 18 ordinary unique deliveries.

        Retries/read-back pages never draw again. Pair reservation consumes budget
        even when abandoned; 2/(18+2) is the long-run upper exploration allocation.
        Probabilities are conditional on a feasible pair and credited opportunity,
        not propensities for the entire hiring pool.
        """
        if session.get("source", "tinder") != "tinder":
            return session
        from matching_engine.feed_store import FeedStore
        control = HypothesisStore.ensure(connection, session)
        vacancy = FeedStore._owner_vacancy(connection, session["owner_id"], session["vacancy_id"])
        preference = PreferenceStore.ensure(connection, session)
        source = preference["identity"]["source"]
        pending = connection.execute("""SELECT p.*,t.served_at AS test_served,c.served_at AS control_served,
                t.profile_revision AS test_revision,c.profile_revision AS control_revision
            FROM hypothesis_pipeline.pairs p JOIN feed_pipeline.exposures t ON t.exposure_id=p.test_exposure
            JOIN feed_pipeline.exposures c ON c.exposure_id=p.control_exposure
            WHERE p.vacancy_id=%s AND p.invalidated_at IS NULL ORDER BY p.created_at""", (session["vacancy_id"],)).fetchall()
        for pair in pending:
            ids = [pair["test_candidate"], pair["control_candidate"]]
            if not PreferenceStore._consent(connection, ids, source, lock=True):
                HypothesisStore._cancel(connection, control, "consent_withdrawn", pair["pair_id"])
            elif not all(FeedStore._candidate_ready(connection, vacancy, pair[f"{role}_candidate"], pair[f"{role}_revision"],
                allow_blocked=pair[f"{role}_served"] is not None) for role in ("test", "control")):
                HypothesisStore._cancel(connection, control, "source_changed", pair["pair_id"])
        if not control["enabled"] or not preference["enabled"] or session["artifact_hash"] is None:
            return session
        high = connection.execute("SELECT COALESCE(max(position),0) AS position FROM feed_pipeline.exposures WHERE session_id=%s AND served_at IS NOT NULL", (session["session_id"],)).fetchone()["position"]
        if after != high:
            return session
        if connection.execute("""SELECT 1 FROM hypothesis_pipeline.pairs p JOIN feed_pipeline.exposures t ON t.exposure_id=p.test_exposure
            JOIN feed_pipeline.exposures c ON c.exposure_id=p.control_exposure WHERE p.vacancy_id=%s AND p.invalidated_at IS NULL
            AND (t.served_at IS NULL OR c.served_at IS NULL) AND p.session_id=%s""", (session["vacancy_id"], session["session_id"])).fetchone():
            return session
        counts = connection.execute("""SELECT count(DISTINCT e.candidate_id) AS ordinary FROM feed_pipeline.exposures e
            JOIN learning_pipeline.events d ON d.exposure_id=e.exposure_id AND d.event_type='delivered'
            JOIN feed_pipeline.sessions s USING(session_id)
            WHERE s.source='tinder' AND e.owner_id=%s AND e.vacancy_id=%s AND d.recorded_at>=%s AND e.card->>'hypothesis_pair_id' IS NULL""",
            (session["owner_id"], session["vacancy_id"], control["budget_start"])).fetchone()
        block = counts["ordinary"] // ORDINARY_PER_PAIR
        pairs_used = connection.execute("SELECT count(*) AS count FROM hypothesis_pipeline.pairs WHERE vacancy_id=%s AND epoch=%s AND context_hash=%s",
                                        (session["vacancy_id"], control["epoch"], control["context_hash"])).fetchone()["count"]
        if block <= pairs_used or block <= control["last_search_block"]:
            return session
        try:
            pool, report = HypothesisStore._pool(connection, session, vacancy)
            active = PreferenceStore.active(connection, preference)
            pairs = comparable_pairs(pool, active["model"] if active else {"baseline_scale": report.get("score_scale", 1.)})
        except (ValueError, TypeError, KeyError):
            pool, pairs, active = [], [], None
            report = {"reason": "compatible_comparison_inputs_unavailable", "complete": False}
        connection.execute("UPDATE hypothesis_pipeline.controls SET last_search_block=%s,search_report=%s WHERE vacancy_id=%s",
                           (block, Jsonb({**report, "feasible_pairs": len(pairs)}), session["vacancy_id"]))
        if not pairs:
            return session
        weights = [max(MIN_PRIORITY, float(pair["priority"])) for pair in pairs]
        total = math.fsum(weights)
        draw = random.SystemRandom().random()
        cumulative, index = 0., len(pairs)-1
        for offset, weight in enumerate(weights):
            cumulative += weight/total
            if draw < cumulative:
                index = offset
                break
        pair, probability = pairs[index], weights[index]/total
        test_first = bool(random.SystemRandom().getrandbits(1))
        selected = {item["candidate_id"]: item for item in pool}
        ids = [UUID(pair["test_id"]), UUID(pair["control_id"])]
        if not PreferenceStore._consent(connection, ids, source, lock=True):
            return session
        pair_id, exposures = uuid4(), {}
        for role in ("test", "control"):
            item = selected[pair[f"{role}_id"]]
            row = connection.execute("SELECT * FROM feed_pipeline.exposures WHERE session_id=%s AND candidate_id=%s",
                                     (session["session_id"], UUID(item["candidate_id"]))).fetchone()
            added = row is None
            if added:
                position = connection.execute("SELECT COALESCE(max(position),0)+1 AS p FROM feed_pipeline.exposures WHERE session_id=%s", (session["session_id"],)).fetchone()["p"]
                row = connection.execute("""INSERT INTO feed_pipeline.exposures(exposure_id,session_id,owner_id,vacancy_id,
                    candidate_id,profile_revision,position,kind,card) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (uuid4(), session["session_id"], session["owner_id"], session["vacancy_id"], UUID(item["candidate_id"]),
                     item["card"]["profile_revision"], position, item["kind"], Jsonb(item["card"]))).fetchone()
                from matching_engine.learning_store import LearningStore
                LearningStore.event(connection, row, "allocated")
            if row["served_at"] is not None or row["position"] <= high:
                raise ConflictError("hypothesis source position is no longer unserved")
            if item["outside_top_k"]:
                consent = connection.execute("SELECT profile->'consents' AS consent FROM candidate_pipeline.candidates WHERE candidate_id=%s",
                                             (UUID(item["candidate_id"]),)).fetchone()["consent"]
                connection.execute("""INSERT INTO learning_pipeline.samples(main_run_id,candidate_id,profile_revision,
                    main_position,main_score,features,feature_hash,consent_version,consent_accepted_at,sample_scope)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'exploration')
                    ON CONFLICT(main_run_id,candidate_id) DO NOTHING""", (session["main_run_id"], UUID(item["candidate_id"]),
                        item["card"]["profile_revision"], item["card"]["main_position"], item["card"]["explanation"]["score"],
                        Jsonb(item["features"]), digest(item["features"]),
                        consent.get("training_version"), consent.get("training_accepted_at")))
            card = dict(row["card"])
            card.update(hypothesis_pair_id=str(pair_id), hypothesis_id=pair["hypothesis_id"], hypothesis_role=role,
                hypothesis_policy_hash=allocation_hash(), selection_probability=probability, order_probability=.5,
                probability_scope="conditional_pair_and_order_at_budget_opportunity",
                hypothesis_parameters={"preferred": pair["contrast"]["preferred"], "sacrificed": pair["contrast"]["sacrificed"],
                    "minimum_contrast": MIN_CONTRAST, "maximum_nuisance": MAX_NUISANCE_DISTANCE,
                    "calibration_status": "uncalibrated_baseline"},
                exploration_added=added, order_revision=session["order_revision"]+1)
            if active:
                personal = apply(item["features"], card["rerank_score"], active["model"],
                    connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (session["artifact_hash"],)).fetchone()["artifact"])
                card.update(preference_state="personalized",preference_model_hash=active["model_hash"],
                    preference_revision=session["preference_revision"],preference_score=personal["score"],
                    preference_adjustment=personal["adjustment"],preference_contributions=personal["contributions"])
            connection.execute("UPDATE feed_pipeline.exposures SET card=%s,exploration_suppressed=false WHERE exposure_id=%s", (Jsonb(card), row["exposure_id"]))
            exposures[role] = row["exposure_id"]
        connection.execute("""INSERT INTO hypothesis_pipeline.pairs(pair_id,vacancy_id,owner_id,context_hash,epoch,session_id,
            hypothesis_id,test_candidate,control_candidate,test_exposure,control_exposure,comparison_hash,policy_hash,parameters,
            pair_probability,draw,test_first,preference_model_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pair_id, session["vacancy_id"], session["owner_id"], control["context_hash"], control["epoch"], session["session_id"],
             pair["hypothesis_id"], *ids, exposures["test"], exposures["control"], pair["comparison_hash"], allocation_hash(),
             Jsonb({"contrast": pair["contrast"], "nuisance_distance": pair["nuisance_distance"], "pool_hash": digest([{"candidate_id": x["candidate_id"], "feature_hash": digest(x["features"])} for x in pool]),
                    "pool_size": len(pool), "feasible_pairs": len(pairs), "priority": pair["priority"],
                    "selected_index": index, "support": [{"comparison_hash": candidate["comparison_hash"],
                        "hypothesis_id": candidate["hypothesis_id"], "probability": weight/total}
                        for candidate,weight in zip(pairs,weights,strict=True)]}), probability, draw, test_first,
             active["model_hash"] if active else None))
        suffix = connection.execute("SELECT exposure_id,position FROM feed_pipeline.exposures WHERE session_id=%s AND served_at IS NULL AND position>%s ORDER BY position", (session["session_id"], high)).fetchall()
        first = [exposures["test"], exposures["control"]] if test_first else [exposures["control"], exposures["test"]]
        order = first + [row["exposure_id"] for row in suffix if row["exposure_id"] not in first]
        connection.execute("SET CONSTRAINTS feed_pipeline.exposures_session_id_position_key DEFERRED")
        for exposure, position in zip(order, [row["position"] for row in suffix], strict=True):
            connection.execute("UPDATE feed_pipeline.exposures SET position=%s WHERE exposure_id=%s", (position, exposure))
        return connection.execute("UPDATE feed_pipeline.sessions SET order_revision=order_revision+1 WHERE session_id=%s RETURNING *", (session["session_id"],)).fetchone()

    def status(self, owner_id: UUID, vacancy_id: UUID) -> dict:
        """Return safe local hypotheses, censored counts and explicit bounded search scope."""
        from matching_engine.feed_store import FeedStore
        with connect(self.database_url) as connection:
            _lock(connection)
            vacancy = FeedStore._owner_vacancy(connection, owner_id, vacancy_id)
            session = connection.execute("SELECT * FROM feed_pipeline.sessions WHERE owner_id=%s AND vacancy_id=%s AND source='tinder' ORDER BY created_at DESC LIMIT 1", (owner_id, vacancy_id)).fetchone()
            if session is None:
                return {"vacancy_id": vacancy_id, "revision": 0, "enabled": True, "context_hash": None,
                        "policy_hash": policy_hash(), "hypotheses": summarize([]), "search_report": {}, "pairs": 0}
            if (session["vacancy_revision"] != vacancy["revision"] or session["schema_hash"] != vacancy["schema_hash"]):
                raise ConflictError("hypothesis source context is stale")
            pref = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy_id,)).fetchone()
            if pref and not PreferenceStore._current(connection, pref):
                raise ConflictError("hypothesis baseline context is stale")
            summaries, pair_count, control = self._summaries(connection, session, vacancy)
            return {"vacancy_id": vacancy_id, "revision": control["revision"], "enabled": control["enabled"],
                    "context_hash": control["context_hash"], "policy_hash": policy_hash(), "hypotheses": summaries,
                    "search_report": control["search_report"], "pairs": pair_count}

    @staticmethod
    def seal(connection: psycopg.Connection, session: dict, vacancy: dict) -> None:
        """Seal the first decisive batch in the reaction transaction, independent of status polling."""
        if session.get("source", "tinder") != "tinder":
            return
        if connection.execute("""SELECT 1 FROM hypothesis_pipeline.pairs WHERE vacancy_id=%s
            AND invalidated_at IS NULL AND (test_exposure=%s OR control_exposure=%s)""",
            (session["vacancy_id"], session["exposure_id"], session["exposure_id"])).fetchone():
            HypothesisStore._summaries(connection, session, vacancy)

    @staticmethod
    def _summaries(connection: psycopg.Connection, session: dict, vacancy: dict) -> tuple[list[dict], int, dict]:
        """Recheck authority, preserve sealed evidence and report live journal counts under the pipeline lock."""
        from matching_engine.feed_store import FeedStore
        vacancy_id = session["vacancy_id"]
        control = HypothesisStore.ensure(connection, session)
        source = PreferenceStore.ensure(connection, session)["identity"]["source"]
        rows = connection.execute("""SELECT p.*,t.served_at AS test_served,c.served_at AS control_served,
            t.profile_revision AS test_revision,c.profile_revision AS control_revision,
            td.action AS test_action,cd.action AS control_action FROM hypothesis_pipeline.pairs p
            JOIN feed_pipeline.exposures t ON t.exposure_id=p.test_exposure JOIN feed_pipeline.exposures c ON c.exposure_id=p.control_exposure
            LEFT JOIN feed_pipeline.decisions td ON td.exposure_id=p.test_exposure LEFT JOIN feed_pipeline.decisions cd ON cd.exposure_id=p.control_exposure
            WHERE p.vacancy_id=%s AND p.context_hash=%s AND p.epoch=%s AND p.invalidated_at IS NULL
            ORDER BY GREATEST(td.created_at,cd.created_at),p.created_at,p.pair_id""",
            (vacancy_id, control["context_hash"], control["epoch"])).fetchall()
        outcomes = []
        for row in rows:
            if not PreferenceStore._consent(connection, [row["test_candidate"], row["control_candidate"]], source, lock=True):
                HypothesisStore._cancel(connection, control, "consent_withdrawn", row["pair_id"])
                continue
            if not all(FeedStore._candidate_ready(connection,vacancy,row[f"{role}_candidate"],row[f"{role}_revision"],
                allow_blocked=row[f"{role}_served"] is not None) for role in ("test","control")):
                HypothesisStore._cancel(connection,control,"source_changed",row["pair_id"])
                continue
            outcomes.append({"hypothesis_id": row["hypothesis_id"], "comparison_id": str(row["pair_id"]),
                "test_action": row["test_action"] if row["test_served"] else None,
                "control_action": row["control_action"] if row["control_served"] else None})
        summaries = summarize(outcomes)
        for item in summaries:
            batch = connection.execute("SELECT * FROM hypothesis_pipeline.batches WHERE vacancy_id=%s AND context_hash=%s AND epoch=%s AND hypothesis_id=%s",
                (vacancy_id,control["context_hash"],control["epoch"],item["hypothesis_id"])).fetchone()
            if batch:
                if batch["report"] is not None:
                    counts = {key: item[key] for key in ("total", "observed_decisive", "post_batch_decisive")}
                    item.update(batch["report"])
                    item.update(counts)
                else:
                    item.update(status="unclear", batch_status="invalidated", confidence_interval=[0.,1.],
                                supporting=0,opposing=0,decisive=0)
            elif item["batch_status"] == "frozen":
                decisive = [UUID(row["comparison_id"]) for row in outcomes if row["hypothesis_id"]==item["hypothesis_id"]
                    and (row["test_action"],row["control_action"]) in (("like","dislike"),("dislike","like"))][:20]
                connection.execute("INSERT INTO hypothesis_pipeline.batches(vacancy_id,context_hash,epoch,hypothesis_id,pair_ids,report,report_hash) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (vacancy_id,control["context_hash"],control["epoch"],item["hypothesis_id"],decisive,Jsonb(item),digest(item)))
        return summaries, len(outcomes), control

    def command(self, owner_id: UUID, vacancy_id: UUID, action: str, key: UUID, revision: int) -> dict:
        """Apply an owner/CAS command idempotently; reset invalidates old evidence and budget."""
        from matching_engine.feed_store import FeedStore
        if action not in ("pause", "resume", "reset"):
            raise ValueError("unsupported hypothesis command")
        with connect(self.database_url) as connection:
            _lock(connection)
            FeedStore._owner_vacancy(connection, owner_id, vacancy_id)
            control = connection.execute("SELECT * FROM hypothesis_pipeline.controls WHERE vacancy_id=%s FOR UPDATE", (vacancy_id,)).fetchone()
            if not control:
                raise ConflictError("open a feed before configuring hypotheses")
            old = connection.execute("SELECT * FROM hypothesis_pipeline.commands WHERE vacancy_id=%s AND idempotency_key=%s", (vacancy_id, key)).fetchone()
            if old and old["action"] != action:
                raise ConflictError("hypothesis command key belongs to another action")
            if not old:
                if control["revision"] != revision:
                    raise ConflictError("hypothesis revision conflict")
                if action != "resume":
                    self._cancel(connection, control, action)
                connection.execute("""UPDATE hypothesis_pipeline.controls SET enabled=%s,revision=revision+1,
                    epoch=epoch+%s,budget_start=CASE WHEN %s THEN clock_timestamp() ELSE budget_start END,
                    last_search_block=CASE WHEN %s THEN -1 ELSE last_search_block END,updated_at=clock_timestamp()
                    WHERE vacancy_id=%s""", (action != "pause", int(action == "reset"), action == "reset", action == "reset", vacancy_id))
                connection.execute("INSERT INTO hypothesis_pipeline.commands(vacancy_id,idempotency_key,action,revision) VALUES(%s,%s,%s,%s)", (vacancy_id, key, action, revision+1))
        return self.status(owner_id, vacancy_id)
