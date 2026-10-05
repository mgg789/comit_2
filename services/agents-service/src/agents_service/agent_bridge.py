"""Concrete shared-transaction bridge between editorial drafts and durable chat."""

from contextlib import contextmanager
from functools import wraps
from uuid import UUID

import psycopg

from agents_service.agent_store import AgentStore
from agents_service.models import DraftFields, JobView, SchemaSnapshot
from agents_service.store import LeaseError, Store


def _fenced(method):
    """Fence every concrete persistence write, including runtime failure checkpoints."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.checkpoint_transaction():
            return method(self, *args, **kwargs)
    return wrapped


class AtomicAgentStore(AgentStore):
    """Use autocommit between checkpoints, and fenced transactions for durable effects."""

    def __init__(self, connection: psycopg.Connection, job: JobView | None = None):
        super().__init__(connection)
        self.job = job

    @_fenced
    def claim_turn(self, turn_id: UUID, owner_id: UUID):
        """Reclaim processing turns only under the currently owned generic job lease."""
        if self.job is None:
            return super().claim_turn(turn_id, owner_id)
        row = self.connection.execute("UPDATE agents_service.agent_turns SET status='processing',updated_at=now() "
            "WHERE turn_id=%s AND owner_id=%s AND status IN ('queued','retrying','processing') RETURNING *",
            (turn_id, owner_id)).fetchone()
        return self._turn(row) if row else None
    update_turn = _fenced(AgentStore.update_turn)
    append_message = _fenced(AgentStore.append_message)
    journal_start = _fenced(AgentStore.journal_start)
    journal_finish = _fenced(AgentStore.journal_finish)
    save_interaction = _fenced(AgentStore.save_interaction)
    respond_interaction = _fenced(AgentStore.respond_interaction)
    update_session_revision = _fenced(AgentStore.update_session_revision)
    set_session_status = _fenced(AgentStore.set_session_status)
    cache_put = _fenced(AgentStore.cache_put)

    def assert_job_lease(self, job: JobView | None = None, *, lock: bool = False) -> None:
        """Fence a reclaimed/expired worker before it writes any turn or draft state."""
        claimed = job or self.job
        if claimed is None:
            return
        suffix = " FOR UPDATE" if lock else ""
        row = self.connection.execute(
            "SELECT job_id FROM agents_service.jobs WHERE job_id=%s AND owner_id=%s "
            "AND state='processing' AND lease_token=%s AND lease_until>now()" + suffix,
            (claimed.job_id, claimed.owner_id, claimed.lease_token),
        ).fetchone()
        if row is None:
            raise LeaseError("agent job lease is stale or expired")

    @contextmanager
    def tool_transaction(self):
        """Commit draft mutation, tool journal and turn revision atomically."""
        with self.connection.transaction():
            self.assert_job_lease(lock=True)
            yield

    @contextmanager
    def checkpoint_transaction(self):
        """Commit a provider/message checkpoint only while this worker owns its lease."""
        with self.connection.transaction():
            self.assert_job_lease(lock=True)
            yield


class DraftGateway:
    """Share one connection for agent mutations and journals, using a pinned registry."""

    def __init__(self, store: Store, connection: psycopg.Connection,
                 schema: SchemaSnapshot | None = None):
        self.store = store
        self.connection = connection
        self.schema = schema

    def get_draft(self, owner_id: UUID, draft_id: UUID):
        """Read current authoritative state within the active tool transaction."""
        return self.store._view(self.store._get_row(self.connection, owner_id, draft_id))

    def patch_fields(self, owner_id: UUID, draft_id: UUID, expected_revision: int, fields: DraftFields):
        """Apply the identical CAS patch path used by the form, with agent provenance."""
        if self.schema is None:
            raise RuntimeError("registry_not_loaded")
        return self.store.patch_in_connection(self.connection, owner_id, draft_id,
            expected_revision, fields, self.schema, source="agent")

    def approve(self, owner_id: UUID, draft_id: UUID, expected_revision: int):
        """Seal summary atomically with the approve tool journal; never publish."""
        if self.schema is None:
            raise RuntimeError("registry_not_loaded")
        return self.store.approve_in_connection(self.connection, owner_id, draft_id,
            expected_revision, self.schema)
