"""Durable vacancy-local training, consent-safe activation and reversible feed ordering."""

from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from matching_engine.combine_models import ScoringPolicy
from matching_engine.combine_scoring import policy_hash as main_policy_hash
from matching_engine.combine_store import _lock
from matching_engine.database import connect
from matching_engine.preference_dataset import timestamp
from matching_engine.preference_evaluation import evaluate
from matching_engine.preference_live_policy import activation_gate, policy_hash
from matching_engine.preference_model import PreferenceModel, apply
from matching_engine.rerank_hash import digest
from matching_engine.rerank_model import RerankArtifact
from matching_engine.store import ConflictError, NotFoundError

MAX_OBSERVATIONS = 10000
MAX_TRAIN = 1000


class PreferenceStore:
    """Coordinate local fitting through PostgreSQL, without external calls or file exports."""

    def __init__(self, database_url: str) -> None:
        """Use the shared matching/API database for source consent and durable work."""
        self.database_url = database_url

    @staticmethod
    def identity(connection: psycopg.Connection, session: dict) -> dict:
        """Freeze exact vacancy, baseline and policy identities; main run IDs may change."""
        source = connection.execute("SELECT source FROM learning_pipeline.settings WHERE singleton").fetchone()["source"]
        approval = session.get("approval_receipt_hash", session.get("card", {}).get("approval_receipt_hash"))
        if "approval_receipt_hash" not in session and "card" not in session:
            run = connection.execute("SELECT approval_receipt_hash FROM rerank_pipeline.runs WHERE run_id=%s", (session["rerank_run_id"],)).fetchone()
            approval = run["approval_receipt_hash"] if run else None
        return {"owner_id": str(session["owner_id"]), "vacancy_id": str(session["vacancy_id"]),
                "vacancy_revision": session["vacancy_revision"], "strict_schema_hash": session["schema_hash"],
                "feature_schema_hash": session["feature_schema_hash"], "main_policy_hash": session["main_policy_hash"],
                "artifact_hash": session["artifact_hash"], "activation_revision": session["activation_revision"],
                "approval_receipt_hash": approval, "source": source,
                "preference_policy_hash": policy_hash()}

    @staticmethod
    def ensure(connection: psycopg.Connection, session: dict) -> dict:
        """Replace incompatible contexts atomically; old jobs cannot publish into a new epoch."""
        identity = PreferenceStore.identity(connection, session)
        context_hash = digest(identity)
        control = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s FOR UPDATE",
                                     (session["vacancy_id"],)).fetchone()
        if control is None:
            control = connection.execute("""INSERT INTO preference_pipeline.controls
                (vacancy_id,owner_id,context_hash,identity) VALUES(%s,%s,%s,%s) RETURNING *""",
                (session["vacancy_id"], session["owner_id"], context_hash, Jsonb(identity))).fetchone()
        elif control["context_hash"] != context_hash:
            connection.execute("""UPDATE preference_pipeline.jobs SET state='stale',lease_token=NULL,lease_until=NULL,
                completed_at=now(),error_code='context_changed' WHERE vacancy_id=%s AND state IN ('queued','running')""",
                (session["vacancy_id"],))
            previous = control["active_model_hash"]
            control = connection.execute("""UPDATE preference_pipeline.controls SET context_hash=%s,identity=%s,
                epoch=epoch+1,active_model_hash=NULL,revision=revision+1,last_attempt_at=NULL,
                state=CASE WHEN enabled THEN 'collecting' ELSE 'disabled' END,updated_at=now()
                WHERE vacancy_id=%s RETURNING *""", (context_hash, Jsonb(identity), session["vacancy_id"])).fetchone()
            PreferenceStore._event(connection, control, "context_changed", previous=previous)
        return control

    @staticmethod
    def _event(connection: psycopg.Connection, control: dict, action: str, *, previous: str | None = None,
               job_id: UUID | None = None, reason: str | None = None, key: UUID | None = None) -> None:
        """Record pointer transitions independently of mutable coefficients or job payloads."""
        connection.execute("""INSERT INTO preference_pipeline.events
            (vacancy_id,revision,action,model_hash,previous_model_hash,job_id,reason,idempotency_key)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (control["vacancy_id"], control["revision"], action,
                control["active_model_hash"], previous, job_id, reason, key))

    @staticmethod
    def _current(connection: psycopg.Connection, control: dict) -> bool:
        """Require current open source vacancy, strict schema, baseline approval and source mode."""
        row = connection.execute("""SELECT v.owner_id,v.revision,v.state,v.business_status,sc.schema_hash,
            r.active_artifact_hash,r.activation_revision,r.active_approval_receipt_hash,l.source
            FROM vacancy_pipeline.vacancies v CROSS JOIN candidate_pipeline.settings s
            JOIN candidate_pipeline.strict_schemas sc ON sc.version=s.active_schema_version
            CROSS JOIN rerank_pipeline.settings r CROSS JOIN learning_pipeline.settings l
            WHERE v.vacancy_id=%s""", (control["vacancy_id"],)).fetchone()
        identity = control["identity"]
        return bool(row and row["state"] == "ready" and row["business_status"] == "open"
                    and str(row["owner_id"]) == identity["owner_id"]
                    and row["revision"] == identity["vacancy_revision"]
                    and row["schema_hash"] == identity["strict_schema_hash"]
                    and row["active_artifact_hash"] == identity["artifact_hash"]
                    and row["activation_revision"] == identity["activation_revision"]
                    and row["active_approval_receipt_hash"] == identity["approval_receipt_hash"]
                    and identity["main_policy_hash"] == main_policy_hash(ScoringPolicy())
                    and row["source"] == identity["source"] and identity["preference_policy_hash"] == policy_hash())

    @staticmethod
    def _observations(connection: psycopg.Connection, control: dict, *, until: datetime | None = None) -> list[dict]:
        """Read delivered explicit actions, deduplicate candidates and quarantine contradictions."""
        identity = control["identity"]
        rows = connection.execute("""SELECT e.candidate_id,e.exposure_id,e.card,s.features,s.feature_hash,
            s.profile_revision,o.event_type,o.recorded_at
            FROM learning_pipeline.events o
            JOIN feed_pipeline.exposures e USING(exposure_id)
            JOIN feed_pipeline.sessions f USING(session_id)
            JOIN learning_pipeline.samples s ON s.main_run_id=f.main_run_id AND s.candidate_id=e.candidate_id
            JOIN learning_pipeline.cohorts c ON c.main_run_id=s.main_run_id
            WHERE o.vacancy_id=%s AND o.owner_id=%s AND o.event_type IN ('like','dislike')
              AND e.served_at IS NOT NULL AND e.served_at<=o.recorded_at
              AND EXISTS(SELECT 1 FROM learning_pipeline.events d WHERE d.exposure_id=e.exposure_id
                  AND d.event_type='delivered' AND d.recorded_at<=o.recorded_at)
              AND c.source=%s AND f.vacancy_revision=%s AND f.schema_hash=%s
              AND f.feature_schema_hash=%s AND f.main_policy_hash=%s AND f.artifact_hash=%s
              AND f.activation_revision=%s AND e.card->>'approval_receipt_hash' IS NOT DISTINCT FROM %s
              AND e.card->>'mode'='learned' AND s.withdrawn_at IS NULL AND s.features IS NOT NULL
              AND (%s::timestamptz IS NULL OR o.recorded_at>%s)
              AND (%s::timestamptz IS NULL OR o.recorded_at<=%s)
            ORDER BY o.recorded_at,e.exposure_id LIMIT %s""", (
                control["vacancy_id"], control["owner_id"], identity["source"], identity["vacancy_revision"],
                identity["strict_schema_hash"], identity["feature_schema_hash"], identity["main_policy_hash"],
                identity["artifact_hash"], identity["activation_revision"], identity["approval_receipt_hash"],
                control["cutoff"], control["cutoff"], until, until, MAX_OBSERVATIONS + 1)).fetchall()
        if len(rows) > MAX_OBSERVATIONS:
            raise ConflictError("preference observation limit exceeded")
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row["candidate_id"]), []).append(row)
        result = []
        for candidate_id, values in grouped.items():
            if len({row["event_type"] for row in values}) > 1:
                continue
            row = values[0]
            result.append({"candidate_key": candidate_id, "features": row["features"],
                           "feature_hash": row["feature_hash"], "baseline_score": row["card"]["rerank_score"],
                           "label": int(row["event_type"] == "like"), "decision_at": row["recorded_at"].isoformat(),
                           "exposure_key": str(row["exposure_id"]), "profile_revision": row["profile_revision"]})
        return sorted(result, key=lambda row: (row["decision_at"], row["candidate_key"]))

    @staticmethod
    def enqueue(connection: psycopg.Connection, session: dict) -> UUID | None:
        """Coalesce reactions transactionally: first 40 candidates, then 16 fresh candidates per fit."""
        if session.get("artifact_hash") is None or session.get("card", {}).get("mode") == "main_fallback":
            return None
        control = PreferenceStore.ensure(connection, session)
        if not control["enabled"] or not PreferenceStore._current(connection, control):
            return None
        pending = connection.execute("""SELECT job_id FROM preference_pipeline.jobs
            WHERE vacancy_id=%s AND state IN ('queued','running')""", (control["vacancy_id"],)).fetchone()
        if pending:
            return pending["job_id"]
        try:
            rows = PreferenceStore._observations(connection, control)
        except ConflictError:
            connection.execute("UPDATE preference_pipeline.controls SET state='observation_limit' WHERE vacancy_id=%s", (control["vacancy_id"],))
            return None
        fresh = sum(control["last_attempt_at"] is None or timestamp(row["decision_at"]) > control["last_attempt_at"] for row in rows)
        if len(rows) < 40 or (control["last_attempt_at"] is not None and fresh < 16):
            return None
        job_id = uuid4()
        connection.execute("INSERT INTO preference_pipeline.jobs(job_id,vacancy_id,context_hash,epoch) VALUES(%s,%s,%s,%s)",
                           (job_id, control["vacancy_id"], control["context_hash"], control["epoch"]))
        connection.execute("UPDATE preference_pipeline.controls SET state='queued',updated_at=now() WHERE vacancy_id=%s",
                           (control["vacancy_id"],))
        return job_id

    def claim(self, lease_seconds: int, max_attempts: int) -> dict | None:
        """Lease one queued or abandoned job; old workers cannot publish after token replacement."""
        with connect(self.database_url) as connection:
            _lock(connection)
            job = connection.execute("""SELECT * FROM preference_pipeline.jobs WHERE
                (state='queued' AND available_at<=clock_timestamp()) OR (state='running' AND lease_until<clock_timestamp())
                ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if job is None:
                return None
            if job["attempts"] >= max_attempts:
                connection.execute("UPDATE preference_pipeline.jobs SET state='failed',error_code='attempts_exhausted',completed_at=now() WHERE job_id=%s", (job["job_id"],))
                connection.execute("UPDATE preference_pipeline.controls SET state='failed' WHERE vacancy_id=%s", (job["vacancy_id"],))
                return None
            token = uuid4()
            return connection.execute("""UPDATE preference_pipeline.jobs SET state='running',attempts=attempts+1,
                lease_token=%s,lease_until=clock_timestamp()+%s*interval '1 second' WHERE job_id=%s RETURNING *""",
                (token, lease_seconds, job["job_id"])).fetchone()

    def heartbeat(self, job: dict, lease_seconds: int) -> bool:
        """Renew only the live claim without blocking feed transactions."""
        with connect(self.database_url) as connection:
            return connection.execute("""UPDATE preference_pipeline.jobs SET lease_until=clock_timestamp()+%s*interval '1 second'
                WHERE job_id=%s AND state='running' AND lease_token=%s AND lease_until>clock_timestamp()""",
                (lease_seconds, job["job_id"], job["lease_token"])).rowcount == 1

    @staticmethod
    def _consent(connection: psycopg.Connection, candidates: list[UUID], source: str, *, lock: bool = False) -> bool:
        """Check all contributing candidates; operational processing requires authoritative source rows."""
        if not candidates:
            return False
        authority = connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]
        if source == "operational" and not authority:
            return False
        valid = connection.execute("""SELECT candidate_id FROM candidate_pipeline.candidates
            WHERE candidate_id=ANY(%s::uuid[]) AND (profile->'consents'->>'processing')::boolean IS TRUE
            AND (profile->'consents'->>'pool')::boolean IS TRUE""", (candidates,)).fetchall()
        if len(valid) != len(set(candidates)):
            return False
        if authority:
            clause = " FOR SHARE" if lock else ""
            valid = connection.execute("SELECT candidate_id FROM public.candidate_profiles WHERE candidate_id=ANY(%s::uuid[]) AND processing_consent AND pool_consent" + clause, (candidates,)).fetchall()
            return len(valid) == len(set(candidates))
        return source == "synthetic"

    @staticmethod
    def _verify_rows(rows: list[dict], artifact: dict) -> None:
        """Reject corrupted, incompatible or fabricated baseline features before fitting."""
        baseline = RerankArtifact.model_validate(artifact)
        for row in rows:
            features = row["features"]
            values, flags = features["values"], features["available"]
            if (digest(features) != row["feature_hash"] or features["names"] != baseline.feature_names
                    or features["schema_hash"] != baseline.feature_schema_hash
                    or len(values) != len(baseline.feature_names) or len(flags) != len(values)
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                    or any(type(flag) is not bool for flag in flags)):
                raise ValueError("invalid live preference feature snapshot")
            expected = math.fsum(weight * ((value - mean) / scale if present else 0.)
                                for weight, value, mean, scale, present in zip(baseline.coefficients, values,
                                    baseline.means, baseline.scales, flags, strict=True))
            if type(row["baseline_score"]) not in (int, float) or not math.isclose(row["baseline_score"], expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("invalid live baseline score")

    def fit_and_publish(self, job: dict) -> bool:
        """Seal a fresh temporal audit, fit locally and atomically activate only a passing version.

        Authoritative consent row locks cover fitting and publication. No source
        snapshots are copied into job JSON: participant references and hashes let
        operators reproduce a fit while withdrawal can erase the original payloads.
        """
        with connect(self.database_url) as connection:
            control = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (job["vacancy_id"],)).fetchone()
            lease = connection.execute("SELECT state,lease_token FROM preference_pipeline.jobs WHERE job_id=%s", (job["job_id"],)).fetchone()
            if (control is None or lease is None or lease["state"] != "running"
                    or lease["lease_token"] != job["lease_token"] or not control["enabled"]
                    or control["context_hash"] != job["context_hash"] or control["epoch"] != job["epoch"]):
                return False
            rows = self._observations(connection, control, until=job["requested_at"])
            selected = rows[-(MAX_TRAIN + 16):]
            participants = [UUID(row["candidate_key"]) for row in selected]
            if not self._consent(connection, participants, control["identity"]["source"], lock=True):
                raise ConflictError("preference source consent unavailable")
            artifact_row = connection.execute("SELECT artifact FROM rerank_pipeline.artifacts WHERE artifact_hash=%s", (control["identity"]["artifact_hash"],)).fetchone()
            if artifact_row is None:
                raise ConflictError("preference baseline unavailable")
            artifact = artifact_row["artifact"]
            self._verify_rows(selected, artifact)
            context = {"identity": control["identity"], "train": selected[:-16],
                       "validation": selected[-16:-8], "golden": selected[-8:]}
            fresh_audit = bool(len(selected) >= 40 and (control["last_attempt_at"] is None
                              or timestamp(selected[-16]["decision_at"]) > control["last_attempt_at"]))
            input_hash = digest(selected)
            plan = {"context_hash": control["context_hash"], "epoch": control["epoch"], "input_hash": input_hash,
                    "policy_hash": policy_hash(), "splits": {name: [{"candidate_key": row["candidate_key"],
                    "exposure_key": row["exposure_key"], "feature_hash": row["feature_hash"], "label": row["label"],
                    "decision_at": row["decision_at"]} for row in context[name]] for name in ("train", "validation", "golden")}}
            plan_hash = digest(plan)
            # Commit the seal before fitting, without taking matching locks under source row locks.
            with connect(self.database_url) as sealing:
                live = sealing.execute("SELECT input_hash,lease_token,state FROM preference_pipeline.jobs WHERE job_id=%s FOR UPDATE", (job["job_id"],)).fetchone()
                if live["state"] != "running" or live["lease_token"] != job["lease_token"]:
                    return False
                if live["input_hash"] is not None and live["input_hash"] != input_hash:
                    raise ConflictError("sealed preference inputs changed")
                sealing.execute("UPDATE preference_pipeline.jobs SET input_hash=%s,plan_hash=%s,plan=%s WHERE job_id=%s",
                                (input_hash, plan_hash, Jsonb(plan), job["job_id"]))
                for candidate in participants:
                    sealing.execute("INSERT INTO preference_pipeline.participants(job_id,candidate_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (job["job_id"], candidate))
            report = evaluate({control["context_hash"]: context}, artifact, source=control["identity"]["source"],
                              dataset_hash=input_hash, split_hash=plan_hash)
            outcome = report["contexts"][control["context_hash"]]
            model_json = report["model_full_json"].get(control["context_hash"])
            gate = activation_gate(outcome, model_json, artifact, source=control["identity"]["source"], allow_synthetic=control["identity"]["source"] == "synthetic")
            if not fresh_audit:
                gate = {"passed": False, "reasons": ["fresh_audit_required"], "policy_hash": policy_hash()}
            report.pop("model_full_json", None)
            # Hash the reduced persisted report rather than retaining a removed-payload seal.
            _lock(connection)
            current = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s FOR UPDATE", (job["vacancy_id"],)).fetchone()
            lease = connection.execute("SELECT * FROM preference_pipeline.jobs WHERE job_id=%s FOR UPDATE", (job["job_id"],)).fetchone()
            valid = (lease["state"] == "running" and lease["lease_token"] == job["lease_token"]
                     and lease["lease_until"] > connection.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
                     and current["epoch"] == job["epoch"] and current["context_hash"] == job["context_hash"]
                     and current["enabled"] and self._current(connection, current)
                     and self._consent(connection, participants, current["identity"]["source"]))
            if not valid:
                if lease["lease_token"] == job["lease_token"] and lease["state"] == "running":
                    connection.execute("UPDATE preference_pipeline.jobs SET state='stale',error_code='source_changed',lease_token=NULL,lease_until=NULL,completed_at=now() WHERE job_id=%s", (job["job_id"],))
                return False
            previous = current["active_model_hash"]
            if gate["passed"] and previous:
                previous_row = self.active(connection, current)
                if previous_row:
                    from matching_engine.preference_evaluation import _metrics
                    old_model = PreferenceModel.model_validate(previous_row["model"])
                    for split in ("validation", "golden"):
                        old_metrics = _metrics(context[split], [old_model.response_logit(row["features"], row["baseline_score"], personalized=True) for row in context[split]],
                            [apply(row["features"], row["baseline_score"], old_model, artifact)["score"] for row in context[split]])
                        new_metrics = outcome[split]["learned"]
                        if any(new_metrics[name] + 1e-12 < old_metrics[name] for name in ("pair_accuracy", "ndcg_at_10")):
                            gate = {"passed": False, "reasons": ["active_model_ranking_regression"], "policy_hash": policy_hash()}
                            break
            state = "ready" if gate["passed"] else "rejected"
            report["runtime_gate"] = gate
            report["runtime_activation"] = gate["passed"]
            report["report_hash"] = digest({key: value for key, value in report.items() if key != "report_hash"})
            connection.execute("""UPDATE preference_pipeline.jobs SET state=%s,report=%s,error_code=%s,
                lease_token=NULL,lease_until=NULL,completed_at=now() WHERE job_id=%s""", (state, Jsonb(report),
                None if gate["passed"] else ",".join(gate["reasons"]), job["job_id"]))
            if model_json:
                model_hash = digest({"model": model_json, "context": job["context_hash"], "epoch": job["epoch"], "plan": plan_hash})
                connection.execute("""INSERT INTO preference_pipeline.models
                    (model_hash,vacancy_id,context_hash,epoch,job_id,model,input_hash,plan_hash,gate)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (model_hash,
                    job["vacancy_id"], job["context_hash"], job["epoch"], job["job_id"], Jsonb(model_json), input_hash, plan_hash, Jsonb(gate)))
            else:
                model_hash = None
            current = connection.execute("""UPDATE preference_pipeline.controls SET last_attempt_at=%s,
                active_model_hash=CASE WHEN %s THEN %s ELSE active_model_hash END,
                revision=revision+CASE WHEN %s THEN 1 ELSE 0 END,
                state=CASE WHEN %s THEN 'active' ELSE %s END,updated_at=now()
                WHERE vacancy_id=%s RETURNING *""", (job["requested_at"], gate["passed"], model_hash,
                gate["passed"], gate["passed"], state, job["vacancy_id"])).fetchone()
            self._event(connection, current, "activate" if gate["passed"] else "reject", previous=previous,
                        job_id=job["job_id"], reason=None if gate["passed"] else ",".join(gate["reasons"]))
            # Reactions committed during fitting are not lost; queue the next fresh audit if due.
            self.enqueue(connection, {**current["identity"], "owner_id": current["owner_id"], "vacancy_id": current["vacancy_id"], "schema_hash": current["identity"]["strict_schema_hash"]})
            return True

    def fail(self, job: dict, code: str, max_attempts: int, retry_base_seconds: int) -> None:
        """Record bounded safe failures; keep the last active model available."""
        with connect(self.database_url) as connection:
            _lock(connection)
            connection.execute("""UPDATE preference_pipeline.jobs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'queued' END,
                error_code=%s,available_at=now()+%s*interval '1 second',lease_token=NULL,lease_until=NULL
                WHERE job_id=%s AND state='running' AND lease_token=%s""", (max_attempts, code,
                retry_base_seconds * min(32, 2 ** max(0, job["attempts"] - 1)), job["job_id"], job["lease_token"]))

    @staticmethod
    def active(connection: psycopg.Connection, control: dict) -> dict | None:
        """Return a compatible active model only while every participant still consents."""
        if not control["enabled"] or not control["active_model_hash"] or not PreferenceStore._current(connection, control):
            return None
        row = connection.execute("SELECT * FROM preference_pipeline.models WHERE model_hash=%s AND model IS NOT NULL AND revoked_at IS NULL", (control["active_model_hash"],)).fetchone()
        if row is None or row["context_hash"] != control["context_hash"] or row["epoch"] != control["epoch"] or not row["gate"].get("passed"):
            return None
        candidates = [item["candidate_id"] for item in connection.execute("SELECT candidate_id FROM preference_pipeline.participants WHERE job_id=%s", (row["job_id"],)).fetchall()]
        if not PreferenceStore._consent(connection, candidates, control["identity"]["source"], lock=True):
            return None
        expected = digest({"model": row["model"], "context": row["context_hash"], "epoch": row["epoch"], "plan": row["plan_hash"]})
        if expected != row["model_hash"]:
            return None
        return row

    @staticmethod
    def withdraw(connection: psycopg.Connection, candidate_id: UUID) -> None:
        """Erase all fitted derivatives and seals that included a withdrawn candidate."""
        jobs = [row["job_id"] for row in connection.execute("SELECT job_id FROM preference_pipeline.participants WHERE candidate_id=%s", (candidate_id,)).fetchall()]
        if not jobs:
            return
        affected = connection.execute("""SELECT c.* FROM preference_pipeline.controls c
            JOIN preference_pipeline.models m ON m.model_hash=c.active_model_hash WHERE m.job_id=ANY(%s::uuid[]) FOR UPDATE OF c""", (jobs,)).fetchall()
        connection.execute("UPDATE preference_pipeline.models SET model=NULL,revoked_at=now() WHERE job_id=ANY(%s::uuid[]) AND revoked_at IS NULL", (jobs,))
        connection.execute("""UPDATE preference_pipeline.jobs SET plan=NULL,report=NULL,state='stale',error_code='consent_withdrawn',
            lease_token=NULL,lease_until=NULL,completed_at=now() WHERE job_id=ANY(%s::uuid[])""", (jobs,))
        for control in affected:
            previous = control["active_model_hash"]
            control = connection.execute("""UPDATE preference_pipeline.controls SET active_model_hash=NULL,
                revision=revision+1,state='collecting',updated_at=now() WHERE vacancy_id=%s RETURNING *""", (control["vacancy_id"],)).fetchone()
            PreferenceStore._event(connection, control, "invalidate", previous=previous, reason="consent_withdrawn")

    def reconcile(self) -> None:
        """Purge source-only consent withdrawals and invalidate changed contexts after worker restart."""
        with connect(self.database_url) as connection:
            _lock(connection)
            authority = connection.execute("SELECT to_regclass('public.candidate_profiles') IS NOT NULL AS ready").fetchone()["ready"]
            clause = "LEFT JOIN public.candidate_profiles p USING(candidate_id)" if authority else ""
            revoked = " OR p.candidate_id IS NULL OR NOT p.processing_consent OR NOT p.pool_consent" if authority else ""
            rows = connection.execute(f"""SELECT DISTINCT t.candidate_id FROM learning_pipeline.samples t
                JOIN candidate_pipeline.candidates c USING(candidate_id) {clause}
                WHERE (c.profile->'consents'->>'processing')::boolean IS NOT TRUE
                   OR (c.profile->'consents'->>'pool')::boolean IS NOT TRUE {revoked}""").fetchall()
            for row in rows:
                connection.execute("UPDATE learning_pipeline.samples SET features=NULL,withdrawn_at=now() WHERE candidate_id=%s AND withdrawn_at IS NULL", (row["candidate_id"],))
                self.withdraw(connection, row["candidate_id"])
            for control in connection.execute("SELECT * FROM preference_pipeline.controls").fetchall():
                if not self._current(connection, control):
                    connection.execute("UPDATE preference_pipeline.jobs SET state='stale',lease_token=NULL,lease_until=NULL,error_code='context_changed',completed_at=now() WHERE vacancy_id=%s AND state IN ('queued','running')", (control["vacancy_id"],))
                    if control["active_model_hash"]:
                        previous = control["active_model_hash"]
                        current = connection.execute("UPDATE preference_pipeline.controls SET active_model_hash=NULL,revision=revision+1,state='stale' WHERE vacancy_id=%s RETURNING *", (control["vacancy_id"],)).fetchone()
                        self._event(connection, current, "invalidate", previous=previous, reason="context_changed")

    def status(self, owner_id: UUID, vacancy_id: UUID) -> dict:
        """Return owner-scoped operational counts, model pointer and latest safe training outcome."""
        with connect(self.database_url) as connection:
            _lock(connection)
            return self._status(connection, owner_id, vacancy_id)

    @staticmethod
    def _status(connection: psycopg.Connection, owner_id: UUID, vacancy_id: UUID) -> dict:
        """Project runtime state without coefficients, participant IDs or feature snapshots."""
        if connection.execute("SELECT 1 FROM vacancy_pipeline.vacancies WHERE vacancy_id=%s AND owner_id=%s", (vacancy_id, owner_id)).fetchone() is None:
            raise NotFoundError("owned vacancy not found")
        control = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s", (vacancy_id,)).fetchone()
        if not control:
            return {"vacancy_id": vacancy_id, "revision": 0, "enabled": True, "state": "collecting", "active_model_hash": None,
                    "eligible_reactions": 0, "fresh_reactions": 0, "job_id": None, "job_state": None, "error_code": None,
                    "policy_hash": policy_hash(), "selection_bias": "deterministic_feedback_unknown_propensity"}
        active = PreferenceStore.active(connection, control)
        try:
            rows = PreferenceStore._observations(connection, control)
        except ConflictError:
            rows = []
            control = {**control, "state": "observation_limit"}
        latest = connection.execute("SELECT job_id,state,error_code FROM preference_pipeline.jobs WHERE vacancy_id=%s ORDER BY requested_at DESC LIMIT 1", (vacancy_id,)).fetchone()
        return {"vacancy_id": vacancy_id, "revision": control["revision"], "enabled": control["enabled"],
                "state": "active" if active else control["state"] if control["state"] != "active" else "fallback",
                "active_model_hash": active["model_hash"] if active else None, "eligible_reactions": len(rows),
                "fresh_reactions": sum(control["last_attempt_at"] is None or timestamp(row["decision_at"]) > control["last_attempt_at"] for row in rows),
                "job_id": latest["job_id"] if latest else None, "job_state": latest["state"] if latest else None,
                "error_code": latest["error_code"] if latest else None, "policy_hash": policy_hash(),
                "selection_bias": "deterministic_feedback_unknown_propensity"}

    def command(self, owner_id: UUID, vacancy_id: UUID, action: str, key: UUID, expected_revision: int) -> dict:
        """Owner-scoped CAS/idempotent reset, rollback-to-R0 or retry of the same sealed failed job."""
        if action not in ("reset", "rollback", "retry"):
            raise ValueError("unknown preference operation")
        with connect(self.database_url) as connection:
            _lock(connection)
            self._status(connection, owner_id, vacancy_id)
            old = connection.execute("SELECT action FROM preference_pipeline.events WHERE vacancy_id=%s AND idempotency_key=%s", (vacancy_id, key)).fetchone()
            if old:
                if old["action"] != action:
                    raise ConflictError("preference key conflicts with another operation")
                return self._status(connection, owner_id, vacancy_id)
            control = connection.execute("SELECT * FROM preference_pipeline.controls WHERE vacancy_id=%s FOR UPDATE", (vacancy_id,)).fetchone()
            if control is None:
                raise ConflictError("open a baseline feed before managing preferences")
            if control["revision"] != expected_revision:
                raise ConflictError("preference revision changed")
            previous = control["active_model_hash"]
            if action in ("reset", "rollback"):
                connection.execute("UPDATE preference_pipeline.jobs SET state='stale',lease_token=NULL,lease_until=NULL,error_code='operator_changed',completed_at=now() WHERE vacancy_id=%s AND state IN ('queued','running')", (vacancy_id,))
                control = connection.execute("""UPDATE preference_pipeline.controls SET epoch=epoch+1,revision=revision+1,
                    active_model_hash=NULL,enabled=%s,cutoff=CASE WHEN %s THEN clock_timestamp() ELSE cutoff END,
                    last_attempt_at=NULL,state=%s,updated_at=now() WHERE vacancy_id=%s RETURNING *""",
                    (action == "reset", action == "reset", "collecting" if action == "reset" else "disabled", vacancy_id)).fetchone()
            else:
                if not control["enabled"] or not self._current(connection, control):
                    raise ConflictError("preferences are disabled or stale")
                connection.execute("""UPDATE preference_pipeline.jobs SET state='queued',attempts=0,available_at=now(),
                    lease_token=NULL,lease_until=NULL WHERE job_id=(SELECT job_id FROM preference_pipeline.jobs
                    WHERE vacancy_id=%s AND context_hash=%s AND epoch=%s AND state='failed'
                    ORDER BY requested_at DESC LIMIT 1)
                    AND NOT EXISTS(SELECT 1 FROM preference_pipeline.jobs WHERE vacancy_id=%s AND state IN ('queued','running'))""",
                    (vacancy_id, control["context_hash"], control["epoch"], vacancy_id))
            self._event(connection, control, action, previous=previous, key=key)
            return self._status(connection, owner_id, vacancy_id)
