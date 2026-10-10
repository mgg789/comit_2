"""Pre-reaction feature snapshots and consent-aware, operator-only dataset export."""

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import CandidateCard, MatchCandidate
from matching_engine.combine_store import _lock
from matching_engine.database import connect
from matching_engine.rerank_features import build_features, feature_schema
from matching_engine.rerank_hash import digest
from matching_engine.rerank_store import _schema
from matching_engine.store import ConflictError
from matching_engine.vacancy_models import VacancyReady

DATASET_VERSION = "real-card-observations-v1"


def pseudonym(key: bytes, namespace: str, value: object) -> str:
    """Produce stable domain-separated IDs; the private key is never exported."""
    if len(key) < 32:
        raise ValueError("dataset pseudonym key must contain at least 32 bytes")
    return hmac.new(key, f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()


class LearningStore:
    """Write observations in the same transaction as main/feed publication and feedback."""

    def __init__(self, database_url: str) -> None:
        """Use only the matching database; no LLM, tokenizer or training dependencies."""
        self.database_url = database_url

    def status(self) -> dict:
        """Report durable collection counts only; no account IDs or feature payloads."""
        with connect(self.database_url) as connection:
            sources = connection.execute("""SELECT c.source,count(*) AS samples,
                count(*) FILTER(WHERE s.withdrawn_at IS NOT NULL) AS withdrawn
                FROM learning_pipeline.samples s JOIN learning_pipeline.cohorts c USING(main_run_id)
                GROUP BY c.source ORDER BY c.source""").fetchall()
            events = connection.execute("SELECT event_type,count(*) AS count FROM learning_pipeline.events GROUP BY event_type ORDER BY event_type").fetchall()
            return {"dataset_version": DATASET_VERSION, "sources": sources, "events": events,
                    "training_started": False, "no_action_is_negative": False}

    @staticmethod
    def capture_main(connection: psycopg.Connection, main: dict) -> None:
        """Freeze consented main top-k features, retaining audit counts for other cards.

        Features exclude names, prose, contacts and PDF paths. Existing snapshots
        are never recomputed after feedback or model changes. Beyond-top-k counts
        remain explicit; unselected candidates are not fabricated as card samples.
        """
        if connection.execute("SELECT 1 FROM learning_pipeline.cohorts WHERE main_run_id=%s",
                              (main["run_id"],)).fetchone():
            return
        if main["state"] != "ready" or main["published_at"] is None:
            raise ConflictError("learning source main run is not published")
        schema = _schema(connection, main)
        contract = feature_schema(schema)
        vacancy = VacancyReady.model_validate(main["vacancy_snapshot"])
        source = connection.execute("SELECT source FROM learning_pipeline.settings").fetchone()["source"]
        connection.execute("""INSERT INTO learning_pipeline.cohorts
            (main_run_id,owner_id,vacancy_id,vacancy_revision,schema_hash,feature_schema_hash,
             policy_hash,source,scanned_count,passed_count,selected_count,outside_top_k,published_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                main["run_id"], vacancy.owner_id, main["vacancy_id"], vacancy.revision,
                schema.schema_hash, contract.schema_hash, main["policy_hash"], source,
                main["scanned_count"], main["passed_count"], main["selected_count"],
                main["passed_count"] - main["selected_count"], main["published_at"],
            ))
        inputs = {item["candidate_id"]: item for item in main["candidates_snapshot"]}
        cards = connection.execute("SELECT card FROM matching_pipeline.selections WHERE run_id=%s ORDER BY position",
                                   (main["run_id"],)).fetchall()
        for selected in cards:
            card = CandidateCard.model_validate(selected["card"])
            candidate = MatchCandidate.model_validate(inputs[str(card.candidate_id)])
            vector = build_features(vacancy, candidate, card, schema)
            features = {"names": list(vector.names), "values": list(vector.values),
                        "available": list(vector.available), "schema_hash": vector.schema_hash}
            current = connection.execute("SELECT revision,profile FROM candidate_pipeline.candidates WHERE candidate_id=%s",
                                         (card.candidate_id,)).fetchone()
            consent = current["profile"]["consents"]
            from matching_engine.preference_store import PreferenceStore
            withdrawn = not PreferenceStore._consent(connection, [card.candidate_id], source, lock=True)
            stamp = consent if current["revision"] == card.profile_revision else {}
            connection.execute("""INSERT INTO learning_pipeline.samples
                (main_run_id,candidate_id,profile_revision,main_position,main_score,features,
                 feature_hash,consent_version,consent_accepted_at,withdrawn_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END)""", (
                    main["run_id"], card.candidate_id, card.profile_revision, card.position,
                    card.explanation.score, Jsonb(features) if not withdrawn else None,
                    digest(features), stamp.get("training_version"), stamp.get("training_accepted_at"), withdrawn,
                ))

    @staticmethod
    def event(connection: psycopg.Connection, exposure: dict, kind: str,
              idempotency_key: UUID | None = None) -> dict:
        """Append a scoped first-event receipt; duplicate keys cannot change exposure identity."""
        if kind not in ("allocated", "delivered", "impression", "like", "dislike"):
            raise ValueError("unsupported observation event")
        if idempotency_key is not None:
            previous = connection.execute("""SELECT * FROM learning_pipeline.events
                WHERE owner_id=%s AND event_type=%s AND idempotency_key=%s""",
                (exposure["owner_id"], kind, idempotency_key)).fetchone()
            if previous:
                if previous["exposure_id"] != exposure["exposure_id"] or previous["vacancy_id"] != exposure["vacancy_id"]:
                    raise ConflictError("impression idempotency key belongs to another exposure")
                return previous
        previous = connection.execute("SELECT * FROM learning_pipeline.events WHERE exposure_id=%s AND event_type=%s",
                                      (exposure["exposure_id"], kind)).fetchone()
        if previous:
            if idempotency_key is not None and previous["idempotency_key"] != idempotency_key:
                raise ConflictError("exposure already has an observation receipt")
            return previous
        return connection.execute("""INSERT INTO learning_pipeline.events
            (event_id,exposure_id,owner_id,vacancy_id,event_type,idempotency_key)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""", (
                uuid4(), exposure["exposure_id"], exposure["owner_id"], exposure["vacancy_id"], kind, idempotency_key,
            )).fetchone()

    @staticmethod
    def withdraw(connection: psycopg.Connection, candidate_id: UUID) -> int:
        """Purge learning feature payloads on consent withdrawal; hashes remain audit-only."""
        result = connection.execute("""UPDATE learning_pipeline.samples SET features=NULL,withdrawn_at=now()
            WHERE candidate_id=%s AND withdrawn_at IS NULL""", (candidate_id,))
        from matching_engine.preference_store import PreferenceStore
        PreferenceStore.withdraw(connection, candidate_id)
        from matching_engine.hypothesis_store import HypothesisStore
        HypothesisStore.withdraw(connection, candidate_id)
        from matching_engine.recommendations import withdraw
        withdraw(connection, candidate_id)
        return result.rowcount

    def export(self, directory: Path, key: bytes, *, source: str = "operational", limit: int = 10000) -> dict:
        """Export bounded pseudonymous observations with no-action explicitly unlabeled.

        Operational export requires authoritative API consent in this database,
        including consent changes not yet delivered to matching. All observations
        remain biased by deterministic selection: propensity is unknown, not 1.
        No splits, hiring labels, pair labels or training are invented here.
        """
        if source not in ("operational", "synthetic") or not 1 <= limit <= 100000:
            raise ValueError("invalid dataset source or export limit")
        pseudonym(key, "validation", "key")
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        output = directory / "observations.jsonl"
        with connect(self.database_url) as connection:
            _lock(connection)
            authority = connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]
            if source == "operational" and not authority:
                raise ConflictError("authoritative candidate consent database is required for operational export")
            if authority:
                connection.execute("""UPDATE learning_pipeline.samples s SET features=NULL,withdrawn_at=now()
                    FROM public.candidate_profiles p WHERE s.candidate_id=p.candidate_id AND s.withdrawn_at IS NULL
                    AND (NOT p.processing_consent OR NOT p.pool_consent OR NOT p.training_consent)""")
            consent_join = "JOIN public.candidate_profiles p ON p.candidate_id=s.candidate_id AND p.processing_consent AND p.pool_consent AND p.training_consent" if source == "operational" else ""
            # API consent writes use their own transaction rather than PIPELINE_LOCK.
            # Row locks retain valid authoritative consent until export commits.
            consent_lock = "FOR SHARE OF p" if source == "operational" else ""
            rows = connection.cursor(name="learning_samples_export")
            rows.execute(f"""SELECT s.*,c.* FROM learning_pipeline.samples s
                JOIN learning_pipeline.cohorts c USING(main_run_id)
                JOIN candidate_pipeline.candidates live USING(candidate_id)
                {consent_join}
                WHERE c.source=%s AND s.withdrawn_at IS NULL AND s.features IS NOT NULL
                  AND (live.profile->'consents'->>'processing')::boolean IS TRUE
                  AND (live.profile->'consents'->>'pool')::boolean IS TRUE
                  AND (live.profile->'consents'->>'training')::boolean IS TRUE
                ORDER BY c.published_at,s.main_run_id,s.candidate_id LIMIT %s {consent_lock}""", (source, limit + 1))
            truncated = False
            row_count = 0
            checksum = hashlib.sha256()
            decisions = 0
            with output.open("x", encoding="utf-8") as handle:
                os.chmod(output, 0o600)
                for row in rows:
                    if row_count >= limit:
                        truncated = True
                        break
                    if digest(row["features"]) != row["feature_hash"]:
                        raise ConflictError("learning feature snapshot integrity mismatch")
                    observations = connection.execute("""SELECT e.exposure_id,e.session_id,e.position,e.kind,
                        e.served_at,e.card,e.batch_id,e.source_main_run_id,e.source_rerank_run_id,e.source_pool_generation,
                        f.rerank_run_id,f.artifact_hash,f.activation_revision,
                        f.feature_schema_hash,f.tail_policy_hash,f.expires_at,
                        COALESCE(jsonb_agg(jsonb_build_object('kind',o.event_type,'at',o.recorded_at)
                            ORDER BY o.recorded_at,o.event_id) FILTER(WHERE o.event_id IS NOT NULL),'[]') AS events
                        FROM feed_pipeline.exposures e JOIN feed_pipeline.sessions f USING(session_id)
                        LEFT JOIN learning_pipeline.events o USING(exposure_id)
                        WHERE f.main_run_id=%s AND e.candidate_id=%s
                        GROUP BY e.exposure_id,f.session_id ORDER BY e.created_at,e.exposure_id""",
                        (row["main_run_id"], row["candidate_id"])).fetchall()
                    exposures = []
                    for exposure in observations:
                        action = next((event["kind"] for event in exposure["events"] if event["kind"] in ("like", "dislike")), None)
                        decisions += action is not None
                        exposures.append({"exposure_id": pseudonym(key, "exposure", exposure["exposure_id"]),
                            "session_id": pseudonym(key, "session", exposure["session_id"]),
                            "position": exposure["position"], "kind": exposure["kind"],
                            "batch_id": pseudonym(key, "feed-batch", exposure["batch_id"]) if exposure["batch_id"] else None,
                            "source_main_run_id": pseudonym(key, "main", exposure["source_main_run_id"]) if exposure["source_main_run_id"] else None,
                            "source_rerank_run_id": pseudonym(key, "rerank", exposure["source_rerank_run_id"]) if exposure["source_rerank_run_id"] else None,
                            "source_pool_generation": exposure["source_pool_generation"],
                            "rerank_run_id": pseudonym(key, "rerank", exposure["rerank_run_id"]),
                            "artifact_hash": exposure["artifact_hash"], "activation_revision": exposure["activation_revision"],
                            "mode": exposure["card"].get("mode"), "rerank_score": exposure["card"].get("rerank_score"),
                            "approval_receipt_hash": exposure["card"].get("approval_receipt_hash"),
                            "preference_model_hash": exposure["card"].get("preference_model_hash"),
                            "preference_revision": exposure["card"].get("preference_revision", 0),
                            "preference_score": exposure["card"].get("preference_score"),
                            "order_revision": exposure["card"].get("order_revision", 0),
                            "hypothesis_pair_id": pseudonym(key, "hypothesis-pair", exposure["card"]["hypothesis_pair_id"]) if exposure["card"].get("hypothesis_pair_id") else None,
                            "hypothesis_id": exposure["card"].get("hypothesis_id"),
                            "hypothesis_role": exposure["card"].get("hypothesis_role"),
                            "hypothesis_policy_hash": exposure["card"].get("hypothesis_policy_hash"),
                            "hypothesis_parameters": exposure["card"].get("hypothesis_parameters"),
                            "pair_selection_probability": exposure["card"].get("selection_probability"),
                            "order_probability": exposure["card"].get("order_probability"),
                            "probability_scope": exposure["card"].get("probability_scope"),
                            "tail_policy_hash": exposure["tail_policy_hash"], "events": exposure["events"],
                            "action": action, "label": 1 if action == "like" else 0 if action == "dislike" else None,
                            "label_kind": "explicit_behavior" if action else "unlabeled",
                            "censoring": "session_expired" if action is None and exposure["expires_at"] < datetime.now(exposure["expires_at"].tzinfo) else None,
                            "selection_policy": "matched_pair_exploration_v1" if exposure["card"].get("hypothesis_pair_id") else ("deterministic_bounded_preferences" if exposure["card"].get("preference_state") == "personalized" else "deterministic_no_exploration"),
                            "propensity": None})
                    value = {"dataset_version": DATASET_VERSION, "source": row["source"],
                        "main_run_id": pseudonym(key, "main", row["main_run_id"]),
                        "owner_group": pseudonym(key, "owner", row["owner_id"]),
                        "vacancy_group": pseudonym(key, "vacancy", row["vacancy_id"]),
                        "candidate_group": pseudonym(key, "candidate", row["candidate_id"]),
                        "vacancy_revision": row["vacancy_revision"], "profile_revision": row["profile_revision"],
                        "published_at": row["published_at"].isoformat(), "main_position": row["main_position"],
                        "sample_scope": row.get("sample_scope", "main_top_k"),
                        "main_score": row["main_score"], "strict_schema_hash": row["schema_hash"],
                        "feature_schema_hash": row["feature_schema_hash"], "main_policy_hash": row["policy_hash"],
                        "outside_top_k": row["outside_top_k"], "feature_hash": row["feature_hash"],
                        "features": row["features"], "exposures": exposures}
                    line = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                    handle.write(line)
                    checksum.update(line.encode())
                    row_count += 1
            rows.close()
            manifest = {"dataset_version": DATASET_VERSION, "source": source, "rows": row_count,
                        "explicit_decisions": decisions, "truncated": truncated, "limit": limit,
                        "observations_sha256": checksum.hexdigest(), "pseudonym_key_fingerprint": hashlib.sha256(key).hexdigest(),
                        "split_state": "pending_group_and_temporal_assignment", "training_started": False,
                        "labels_are_hiring_suitability": False, "propensity_available": False,
                        "exported_at": datetime.now().astimezone().isoformat(),
                        "consent_scope": "authoritative_api_and_matching" if source == "operational" else "synthetic_matching_only"}
            manifest["manifest_hash"] = digest(manifest)
            target = directory / "manifest.json"
            with target.open("x", encoding="utf-8") as handle:
                os.chmod(target, 0o600)
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
            return manifest
