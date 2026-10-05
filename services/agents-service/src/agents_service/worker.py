"""Lease-fenced publication, form inference and agent-turn processing."""

import threading
from uuid import UUID

import psycopg

from agents_service.agent_bridge import AtomicAgentStore, DraftGateway
from agents_service.agent_runtime import AgentRuntime, AgentRuntimeError
from agents_service.agent_store import AgentStore
from agents_service.database import connect
from agents_service.models import JobView, SchemaSnapshot
from agents_service.providers import (
    DeepSeekClient,
    LayaClient,
    MatchingClient,
    ProviderError,
    fingerprint,
)
from agents_service.store import ConflictError, LeaseError, Store


class PinnedMatching:
    """Provide the registry fetched outside SQL transactions to the agent tools."""

    def __init__(self, schema: SchemaSnapshot):
        self.snapshot = schema

    def schema(self) -> SchemaSnapshot:
        """Return one pinned registry; human Create checks the live registry again."""
        return self.snapshot


class Worker:
    """Execute durable jobs without secrets in errors or fabricated provider results."""

    def __init__(self, store: Store, matching: MatchingClient | None = None,
                 laya: LayaClient | None = None, deepseek: DeepSeekClient | None = None,
                 *, lease_seconds: int = 300):
        self.store = store
        self.matching = matching or MatchingClient()
        self.laya = laya or LayaClient()
        self.deepseek = deepseek or DeepSeekClient()
        self.lease_seconds = lease_seconds

    def process_once(self, kind=None) -> bool:
        """Claim one available job; return false only when there is no work."""
        job = self.store.claim_job(kind, lease_seconds=self.lease_seconds)
        with connect(self.store.database_url) as connection:
            connection.execute("""UPDATE agents_service.agent_turns t SET status='failed',
                error_code=COALESCE(j.error,'job_failed'),error_message=COALESCE(j.error,'job_failed'),updated_at=now()
                FROM agents_service.jobs j WHERE j.kind='agent_turn' AND j.state='failed'
                AND j.owner_id=t.owner_id AND j.payload->>'turn_id'=t.turn_id::text
                AND t.status IN ('queued','processing','retrying')""")
        if job is None:
            return False
        done = threading.Event()

        def heartbeat():
            while not done.wait(max(1, self.lease_seconds / 3)):
                try:
                    self.store.heartbeat_job(job.job_id, job.lease_token, lease_seconds=self.lease_seconds)
                except (psycopg.Error, LeaseError):
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            if job.kind == "publication":
                result = self.matching.publish(str(job.draft_id), job.payload)
                self.store.succeed_publication(job.job_id, job.lease_token, result)
            elif job.kind == "enrichment":
                self._enrich(job)
            else:
                self._agent(job)
        except LeaseError:
            pass  # A new lease owns recovery; this process must not acknowledge anything.
        except (ProviderError, AgentRuntimeError) as error:
            self._fail(job, error.code, error.retryable)
        except ConflictError:
            self._fail(job, "draft_revision_or_schema_conflict", False)
        except (psycopg.Error, ValueError, KeyError, TypeError, RuntimeError):
            self._fail(job, "worker_dependency_error", True)
        finally:
            done.set()
            thread.join(timeout=1)
        return True

    def _fail(self, job: JobView, code: str, retryable: bool) -> None:
        try:
            failed = self.store.fail_job(job.job_id, job.lease_token, code, retryable=retryable)
            if job.kind == "agent_turn":
                with connect(self.store.database_url) as connection:
                    current = connection.execute("SELECT job_id FROM agents_service.jobs WHERE job_id=%s "
                        "AND state=%s AND attempts=%s FOR UPDATE", (job.job_id, failed.state, failed.attempts)).fetchone()
                    if current:
                        turn_id = UUID(str(job.payload["turn_id"]))
                        AgentStore(connection).update_turn(turn_id, job.owner_id, status=failed.state,
                                                          error_code=code, error_message=code)
        except LeaseError:
            pass

    def _enrich(self, job: JobView) -> None:
        schema = SchemaSnapshot.model_validate(job.payload["schema"])
        request_hash = fingerprint({"provider": self.laya.model, "version": self.laya.version,
                                    "fields": job.payload["fields"], "schema": schema.model_dump()})
        with connect(self.store.database_url) as connection:
            cached = AgentStore(connection).cache_get(job.owner_id, request_hash)
        result = cached or self.laya.derive(job.payload["fields"], schema.model_dump())
        if cached is None:
            with connect(self.store.database_url) as connection:
                AgentStore(connection).cache_put(job.owner_id, request_hash, result)
        current = self.matching.schema()
        provenance = result["provenance"]
        self.store.apply_enrichment_job(job.job_id, job.lease_token,
            job.payload["expected_revision"], result["fields"], current,
            input_hash=provenance["input_hash"], model_version=provenance["version"],
            schema_version=schema.version, schema_hash=schema.schema_hash,
            provenance=provenance, result=result)

    def _agent(self, job: JobView) -> None:
        schema = self.matching.schema()
        with connect(self.store.database_url) as connection:
            # Provider calls must never retain database locks/transactions.
            connection.autocommit = True
            runtime = AgentRuntime(AtomicAgentStore(connection, job),
                DraftGateway(self.store, connection, schema), self.deepseek, PinnedMatching(schema))
            turn = runtime.process_job(job)
        if turn.status == "succeeded":
            self.store.succeed_job(job.job_id, job.lease_token,
                {"turn_id": str(turn.turn_id), "draft_revision": turn.draft_revision})
        else:
            self._fail(job, turn.error_code or "agent_turn_failed", turn.status == "retrying")

    def run(self, stop: threading.Event, poll_seconds: float = 2) -> None:
        """Poll until graceful shutdown, finishing the currently leased operation."""
        while not stop.is_set():
            if not self.process_once():
                stop.wait(poll_seconds)
