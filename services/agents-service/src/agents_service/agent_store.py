"""PostgreSQL persistence for durable agent sessions and provider/tool state.

The store is intentionally a small persistence adapter.  It does not know how a
draft is edited or how a provider works; those concerns are injected into
``AgentRuntime``.  Callers may use one connection/transaction around a mutation
and its journal record when their draft store exposes such a transaction.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from .agent_models import (
    AgentDefinition,
    AgentInteraction,
    AgentMessage,
    AgentSession,
    AgentTurn,
    InteractionOption,
    ToolJournalEntry,
)
from .store import ConflictError, NotFoundError, StaleRevisionError


def _dump_json(value: Any) -> Any:
    """Return a JSON-compatible value without leaking Python model internals."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


class AgentStore:
    """Durable session/message/interaction/journal store on an existing psycopg connection."""

    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection

    def create_session(
        self,
        *,
        owner_id: UUID,
        draft_id: UUID,
        draft_revision: int,
        definition: AgentDefinition,
        session_id: UUID | None = None,
    ) -> AgentSession:
        """Create an owner-bound session pinned to packaged knowledge and definition hashes."""
        session_id = session_id or uuid4()
        row = self.connection.execute(
            """
            INSERT INTO agents_service.agent_sessions
                (session_id,owner_id,draft_id,agent_key,agent_version,knowledge_version,
                 knowledge_hash,definition_hash,draft_revision)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                session_id,
                owner_id,
                draft_id,
                definition.key,
                definition.version,
                definition.knowledge_version,
                definition.knowledge_hash,
                definition.definition_hash,
                draft_revision,
            ),
        ).fetchone()
        assert row is not None
        return self._session(row)

    def get_session(self, session_id: UUID, owner_id: UUID, *, for_update: bool = False) -> AgentSession | None:
        """Read one session only when its owner matches the trusted request boundary."""
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT * FROM agents_service.agent_sessions WHERE session_id=%s AND owner_id=%s{suffix}",
            (session_id, owner_id),
        ).fetchone()
        return self._session(row) if row else None

    def update_session_revision(self, session_id: UUID, owner_id: UUID, revision: int) -> None:
        """Advance a session's observed draft revision monotonically."""
        self.connection.execute(
            """
            UPDATE agents_service.agent_sessions
            SET draft_revision=GREATEST(draft_revision,%s), updated_at=now()
            WHERE session_id=%s AND owner_id=%s
            """,
            (revision, session_id, owner_id),
        )

    def set_session_status(self, session_id: UUID, owner_id: UUID, status: str) -> None:
        """Set a terminal/session status after validating the owner scope in SQL."""
        if status not in {"active", "approved", "closed", "failed"}:
            raise ValueError("invalid agent session status")
        self.connection.execute(
            "UPDATE agents_service.agent_sessions SET status=%s,updated_at=now() WHERE session_id=%s AND owner_id=%s",
            (status, session_id, owner_id),
        )

    def append_turn(
        self,
        *,
        session_id: UUID,
        owner_id: UUID,
        draft_id: UUID,
        message: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> AgentTurn:
        """Append one queued turn, rejecting a second active turn for the session."""
        session = self.connection.execute(
            "SELECT * FROM agents_service.agent_sessions WHERE session_id=%s AND owner_id=%s FOR UPDATE",
            (session_id, owner_id),
        ).fetchone()
        if session is None or session["draft_id"] != draft_id:
            raise KeyError("agent session not found")
        existing = self.connection.execute(
            "SELECT * FROM agents_service.agent_turns WHERE session_id=%s AND idempotency_key=%s",
            (session_id, idempotency_key),
        ).fetchone()
        if existing:
            return self._turn(existing)
        draft = self.connection.execute(
            "SELECT state,revision FROM agents_service.drafts WHERE draft_id=%s AND owner_id=%s FOR UPDATE",
            (draft_id, owner_id),
        ).fetchone()
        if draft is None:
            raise NotFoundError("draft is unavailable")
        if draft["state"] in {"creating", "created", "failed"}:
            raise ConflictError("publication snapshot is frozen")
        if draft["revision"] != expected_revision:
            raise StaleRevisionError("draft revision changed")
        active = self.connection.execute(
            """
            SELECT turn_id FROM agents_service.agent_turns
            WHERE session_id=%s AND status IN ('queued','processing','retrying')
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if active:
            raise RuntimeError("agent turn already active")
        sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS sequence FROM agents_service.agent_turns WHERE session_id=%s",
            (session_id,),
        ).fetchone()["sequence"]
        turn_id = uuid4()
        row = self.connection.execute(
            """
            INSERT INTO agents_service.agent_turns
                (turn_id,session_id,owner_id,draft_id,sequence,user_message,
                 expected_revision,draft_revision,idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (turn_id, session_id, owner_id, draft_id, sequence, message, expected_revision, expected_revision, idempotency_key),
        ).fetchone()
        assert row is not None
        turn = self._turn(row)
        self.append_message(session_id=session_id, turn_id=turn_id, role="user", content=message)
        return turn

    def get_turn(self, turn_id: UUID, owner_id: UUID) -> AgentTurn | None:
        """Read a turn under owner scope."""
        row = self.connection.execute(
            "SELECT * FROM agents_service.agent_turns WHERE turn_id=%s AND owner_id=%s",
            (turn_id, owner_id),
        ).fetchone()
        return self._turn(row) if row else None

    def get_turn_by_idempotency(self, session_id: UUID, owner_id: UUID, idempotency_key: str) -> AgentTurn | None:
        """Load an idempotent retry candidate before enforcing a fresh revision guard."""
        row = self.connection.execute(
            "SELECT * FROM agents_service.agent_turns WHERE session_id=%s AND owner_id=%s AND idempotency_key=%s",
            (session_id, owner_id, idempotency_key),
        ).fetchone()
        return self._turn(row) if row else None

    def latest_turn(self, session_id: UUID, owner_id: UUID) -> AgentTurn | None:
        """Return the most recently appended turn under owner scope."""
        row = self.connection.execute(
            "SELECT * FROM agents_service.agent_turns WHERE session_id=%s AND owner_id=%s ORDER BY sequence DESC LIMIT 1",
            (session_id, owner_id),
        ).fetchone()
        return self._turn(row) if row else None

    def list_interactions(self, session_id: UUID, *, limit: int = 32) -> list[AgentInteraction]:
        """Return recent typed interactions in chronological order."""
        limit = max(1, min(limit, 64))
        rows = self.connection.execute(
            "SELECT * FROM agents_service.agent_interactions WHERE session_id=%s ORDER BY created_at DESC LIMIT %s",
            (session_id, limit),
        ).fetchall()
        return [self._interaction(row) for row in reversed(rows)]

    def retry_turn(self, turn_id: UUID, owner_id: UUID) -> AgentTurn:
        """Durably requeue a failed/retryable turn without changing its idempotency key."""
        turn = self.get_turn(turn_id, owner_id)
        if turn is None:
            raise RuntimeError("turn is not retryable")
        self.connection.execute(
            "SELECT session_id FROM agents_service.agent_sessions WHERE session_id=%s AND owner_id=%s FOR UPDATE",
            (turn.session_id, owner_id),
        ).fetchone()
        active = self.connection.execute(
            "SELECT turn_id FROM agents_service.agent_turns WHERE session_id=%s AND turn_id<>%s "
            "AND status IN ('queued','processing','retrying') LIMIT 1",
            (turn.session_id, turn_id),
        ).fetchone()
        if active is not None:
            raise RuntimeError("agent turn already active")
        row = self.connection.execute(
            """
            UPDATE agents_service.agent_turns
            SET status='queued',error_code=NULL,error_message=NULL,updated_at=now()
            WHERE turn_id=%s AND owner_id=%s AND status IN ('failed','retrying') RETURNING *
            """,
            (turn_id, owner_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("turn is not retryable")
        return self._turn(row)

    def claim_turn(self, turn_id: UUID, owner_id: UUID) -> AgentTurn | None:
        """Claim one queued/retryable turn; a concurrent worker gets no row."""
        row = self.connection.execute(
            """
            UPDATE agents_service.agent_turns
            SET status='processing',updated_at=now()
            WHERE turn_id=%s AND owner_id=%s AND status IN ('queued','retrying')
            RETURNING *
            """,
            (turn_id, owner_id),
        ).fetchone()
        return self._turn(row) if row else None

    def update_turn(
        self,
        turn_id: UUID,
        owner_id: UUID,
        *,
        status: str | None = None,
        draft_revision: int | None = None,
        provider_result: Mapping[str, Any] | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rounds: int | None = None,
        tool_calls: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentTurn:
        """Update only supplied turn columns and return the owner-scoped result."""
        allowed = {"queued", "processing", "retrying", "failed", "succeeded"}
        if status is not None and status not in allowed:
            raise ValueError("invalid agent turn status")
        sets: list[str] = ["updated_at=now()"]
        values: list[Any] = []
        for column, value in (
            ("status", status),
            ("draft_revision", draft_revision),
            ("provider_result", Jsonb(_dump_json(provider_result)) if provider_result is not None else None),
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("rounds", rounds),
            ("tool_calls", tool_calls),
            ("error_code", error_code),
            ("error_message", error_message),
        ):
            if value is not None:
                sets.append(f"{column}=%s")
                values.append(value)
        if status == "succeeded":
            sets.extend(["error_code=NULL", "error_message=NULL"])
        values.extend((turn_id, owner_id))
        row = self.connection.execute(
            f"UPDATE agents_service.agent_turns SET {','.join(sets)} WHERE turn_id=%s AND owner_id=%s RETURNING *",
            values,
        ).fetchone()
        if row is None:
            raise KeyError("agent turn not found")
        return self._turn(row)

    @contextmanager
    def tool_transaction(self) -> Iterator[None]:
        """Open a short transaction for one tool effect and its journal rows."""
        with self.connection.transaction():
            yield

    def append_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str = "",
        turn_id: UUID | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        name: str | None = None,
        tool_call_id: str | None = None,
    ) -> AgentMessage:
        """Append a message with a monotonic session-local sequence number."""
        sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence),-1)+1 AS sequence FROM agents_service.agent_messages WHERE session_id=%s",
            (session_id,),
        ).fetchone()["sequence"]
        row = self.connection.execute(
            """
            INSERT INTO agents_service.agent_messages
                (message_id,session_id,turn_id,role,content,tool_calls,name,tool_call_id,sequence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """,
            (uuid4(), session_id, turn_id, role, content, Jsonb(tool_calls or []), name, tool_call_id, sequence),
        ).fetchone()
        assert row is not None
        return self._message(row)

    def list_messages(self, session_id: UUID, *, limit: int = 8) -> list[AgentMessage]:
        """Return the latest bounded conversation messages in chronological order."""
        limit = max(1, min(limit, 32))
        rows = self.connection.execute(
            "SELECT * FROM agents_service.agent_messages WHERE session_id=%s ORDER BY sequence DESC LIMIT %s",
            (session_id, limit),
        ).fetchall()
        return [self._message(row) for row in reversed(rows)]

    def journal_get(self, turn_id: UUID, call_id: str) -> ToolJournalEntry | None:
        """Find a prior tool call so retries replay its exact result."""
        row = self.connection.execute(
            "SELECT * FROM agents_service.agent_tool_journal WHERE turn_id=%s AND call_id=%s",
            (turn_id, call_id),
        ).fetchone()
        return self._journal(row) if row else None

    def journal_start(
        self,
        *,
        turn_id: UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        draft_revision: int,
    ) -> ToolJournalEntry:
        """Create an idempotency row, returning the existing row on a replay."""
        row = self.connection.execute(
            """
            INSERT INTO agents_service.agent_tool_journal
                (turn_id,call_id,tool_name,arguments,status,draft_revision)
            VALUES (%s,%s,%s,%s,'started',%s)
            ON CONFLICT (turn_id,call_id) DO UPDATE SET updated_at=now()
            RETURNING *
            """,
            (turn_id, call_id, tool_name, Jsonb(dict(arguments)), draft_revision),
        ).fetchone()
        assert row is not None
        return self._journal(row)

    def journal_finish(
        self,
        *,
        turn_id: UUID,
        call_id: str,
        status: str,
        result: Mapping[str, Any] | None,
        draft_revision: int,
        error_code: str | None = None,
    ) -> ToolJournalEntry:
        """Persist the result after a tool mutation so a resumed turn cannot double-apply it."""
        if status not in {"completed", "failed"}:
            raise ValueError("invalid journal status")
        row = self.connection.execute(
            """
            UPDATE agents_service.agent_tool_journal
            SET status=%s,result=%s,draft_revision=%s,error_code=%s,updated_at=now()
            WHERE turn_id=%s AND call_id=%s RETURNING *
            """,
            (status, Jsonb(_dump_json(result)) if result is not None else None, draft_revision, error_code, turn_id, call_id),
        ).fetchone()
        if row is None:
            raise KeyError("tool journal row not found")
        return self._journal(row)

    def save_interaction(self, interaction: AgentInteraction) -> AgentInteraction:
        """Persist a typed interaction emitted by an assistant turn."""
        row = self.connection.execute(
            """
            INSERT INTO agents_service.agent_interactions
                (interaction_id,session_id,turn_id,draft_revision,kind,prompt,field_target,options,min_value,max_value,value)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """,
            (
                interaction.interaction_id, interaction.session_id, interaction.turn_id, interaction.draft_revision,
                interaction.kind, interaction.prompt, interaction.field_target, Jsonb([_dump_json(item) for item in interaction.options]),
                interaction.min_value, interaction.max_value, Jsonb(interaction.value) if interaction.value is not None else None,
            ),
        ).fetchone()
        assert row is not None
        return self._interaction(row)

    def get_interaction(self, interaction_id: UUID, session_id: UUID) -> AgentInteraction | None:
        """Load an interaction only within its session scope."""
        row = self.connection.execute(
            "SELECT * FROM agents_service.agent_interactions WHERE interaction_id=%s AND session_id=%s",
            (interaction_id, session_id),
        ).fetchone()
        return self._interaction(row) if row else None

    def respond_interaction(
        self,
        interaction_id: UUID,
        session_id: UUID,
        value: Any,
        *,
        expected_draft_revision: int | None = None,
    ) -> AgentInteraction:
        """Mark a pending interaction answered exactly once and optionally fence its revision."""
        revision_clause = " AND draft_revision=%s" if expected_draft_revision is not None else ""
        params: tuple[Any, ...] = (Jsonb(_dump_json(value)), interaction_id, session_id)
        if expected_draft_revision is not None:
            params += (expected_draft_revision,)
        row = self.connection.execute(
            f"""
            UPDATE agents_service.agent_interactions
            SET value=%s,responded=true,responded_at=now()
            WHERE interaction_id=%s AND session_id=%s AND responded=false{revision_clause} RETURNING *
            """,
            params,
        ).fetchone()
        if row is None:
            raise RuntimeError("interaction is missing or already answered")
        return self._interaction(row)

    def cache_get(self, owner_id: UUID, request_hash: str) -> dict[str, Any] | None:
        """Read a cached external response under owner scope."""
        row = self.connection.execute(
            "SELECT response FROM agents_service.agent_external_cache WHERE owner_id=%s AND request_hash=%s",
            (owner_id, request_hash),
        ).fetchone()
        return dict(row["response"]) if row else None

    def cache_put(self, owner_id: UUID, request_hash: str, response: Mapping[str, Any]) -> None:
        """Store an external response idempotently for later offline/retry use."""
        self.connection.execute(
            """
            INSERT INTO agents_service.agent_external_cache(owner_id,request_hash,response)
            VALUES (%s,%s,%s)
            ON CONFLICT (owner_id,request_hash) DO UPDATE SET response=EXCLUDED.response,updated_at=now()
            """,
            (owner_id, request_hash, Jsonb(_dump_json(response))),
        )

    @staticmethod
    def _session(row: Mapping[str, Any]) -> AgentSession:
        return AgentSession.model_validate(dict(row))

    @staticmethod
    def _turn(row: Mapping[str, Any]) -> AgentTurn:
        return AgentTurn.model_validate(dict(row))

    @staticmethod
    def _message(row: Mapping[str, Any]) -> AgentMessage:
        return AgentMessage.model_validate(dict(row))

    @staticmethod
    def _journal(row: Mapping[str, Any]) -> ToolJournalEntry:
        return ToolJournalEntry.model_validate(dict(row))

    @staticmethod
    def _interaction(row: Mapping[str, Any]) -> AgentInteraction:
        raw = dict(row)
        raw["options"] = [InteractionOption.model_validate(item) for item in raw.get("options") or []]
        return AgentInteraction.model_validate(raw)
