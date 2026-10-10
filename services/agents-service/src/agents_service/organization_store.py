"""Durable PostgreSQL repository for the global employer organization agent."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from .database import connect
from .organization_models import (
    OrganizationBudget,
    OrganizationChat,
    OrganizationContext,
    OrganizationMemory,
    OrganizationMessage,
    OrganizationMessagePage,
    OrganizationReasoningEffort,
    OrganizationTask,
    OrganizationTaskRequest,
    OrganizationToolJournal,
    OrganizationTurn,
    PendingAnswer,
)


def _json(value: Any) -> Any:
    """Convert models and UUIDs into values accepted by psycopg JSONB."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, UUID):
        return str(value)
    return value


class OrganizationConflict(RuntimeError):
    """A client or worker attempted an operation that cannot safely proceed."""


class OrganizationNotFound(OrganizationConflict):
    """An owner-scoped organization record does not exist."""


class OrganizationStore:
    """Small owner-scoped persistence adapter; every public mutation is transactional."""

    def __init__(self, database_url: str, connection: psycopg.Connection[Any] | None = None) -> None:
        self.database_url = database_url
        self.connection = connection

    @contextmanager
    def _db(self) -> Iterator[psycopg.Connection[Any]]:
        """Yield a caller connection or one short-lived connection and commit it."""
        if self.connection is not None:
            yield self.connection
            return
        with connect(self.database_url) as connection:
            yield connection

    @staticmethod
    def _chat(row: Mapping[str, Any]) -> OrganizationChat:
        return OrganizationChat.model_validate(dict(row))

    @staticmethod
    def _message(row: Mapping[str, Any]) -> OrganizationMessage:
        return OrganizationMessage.model_validate(dict(row))

    @staticmethod
    def _turn(row: Mapping[str, Any]) -> OrganizationTurn:
        return OrganizationTurn.model_validate(dict(row))

    @staticmethod
    def _task(row: Mapping[str, Any]) -> OrganizationTask:
        return OrganizationTask.model_validate(dict(row))

    def get_or_create_chat(self, owner_id: UUID) -> OrganizationChat:
        """Return the sole global chat, creating its context and memory rows atomically."""
        with self._db() as db:
            row = db.execute(
                """INSERT INTO agents_service.organization_chats(owner_id,chat_id)
                   VALUES (%s,%s) ON CONFLICT(owner_id) DO UPDATE SET updated_at=now()
                   RETURNING *""", (owner_id, uuid4())).fetchone()
            assert row is not None
            db.execute("""INSERT INTO agents_service.organization_context(owner_id)
                         VALUES (%s) ON CONFLICT(owner_id) DO NOTHING""", (owner_id,))
            db.execute("""INSERT INTO agents_service.organization_memory(owner_id)
                         VALUES (%s) ON CONFLICT(owner_id) DO NOTHING""", (owner_id,))
            return self._chat(row)

    def get_chat(self, owner_id: UUID) -> OrganizationChat | None:
        """Read the owner chat without creating it."""
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_chats WHERE owner_id=%s", (owner_id,)).fetchone()
            return self._chat(row) if row else None

    def set_presence(self, owner_id: UUID, present: bool) -> OrganizationChat:
        """Update owner presence and heartbeat timestamp under the chat owner key."""
        chat = self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("""UPDATE agents_service.organization_chats
                SET presence=%s,last_seen_at=CASE WHEN %s THEN now() ELSE last_seen_at END,updated_at=now()
                WHERE owner_id=%s RETURNING *""", (present, present, chat.owner_id)).fetchone()
            assert row is not None
            return self._chat(row)

    def append_message(self, owner_id: UUID, *, role: str, content: str, turn_id: UUID | None = None,
                       tool_calls: list[dict[str, Any]] | None = None, tool_call_id: str | None = None,
                       name: str | None = None, attachments: list[dict[str, Any]] | None = None,
                       idempotency_key: str | None = None, task_id: UUID | None = None,
                       task_lease_token: UUID | None = None) -> OrganizationMessage:
        """Append a message with a monotonic chat sequence and optional idempotency key."""
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("invalid organization message role")
        chat = self.get_or_create_chat(owner_id)
        with self._db() as db, db.transaction():
            db.execute("SELECT chat_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            if turn_id is not None:
                live = db.execute("SELECT 1 FROM agents_service.organization_turns WHERE owner_id=%s AND chat_id=%s AND turn_id=%s",
                                  (owner_id, chat.chat_id, turn_id)).fetchone()
                if live is None:
                    raise OrganizationConflict("organization_turn_not_found")
            if task_id is not None:
                live = db.execute("""SELECT 1 FROM agents_service.organization_tasks
                    WHERE owner_id=%s AND chat_id=%s AND task_id=%s AND lease_token=%s
                        AND status NOT IN ('stopped','failed')""", (owner_id, chat.chat_id, task_id, task_lease_token)).fetchone()
                if live is None:
                    raise OrganizationConflict("organization_task_lease_lost")
            elif task_lease_token is not None:
                raise ValueError("task_id_required")
            if idempotency_key:
                existing = db.execute("SELECT * FROM agents_service.organization_messages WHERE chat_id=%s AND idempotency_key=%s",
                                      (chat.chat_id, idempotency_key)).fetchone()
                if existing:
                    if (existing["role"] != role or existing["content"] != content
                            or existing["tool_calls"] != (tool_calls or [])
                            or existing["tool_call_id"] != tool_call_id
                            or existing["attachments"] != (attachments or [])):
                        raise OrganizationConflict("idempotency_key_conflict")
                    return self._message(existing)
            seq = db.execute("SELECT COALESCE(MAX(sequence),-1)+1 AS sequence FROM agents_service.organization_messages WHERE chat_id=%s",
                             (chat.chat_id,)).fetchone()["sequence"]
            row = db.execute("""INSERT INTO agents_service.organization_messages
                (message_id,chat_id,turn_id,role,content,tool_calls,tool_call_id,name,attachments,sequence,idempotency_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (uuid4(), chat.chat_id, turn_id, role, content, Jsonb(tool_calls or []), tool_call_id, name,
                 Jsonb(attachments or []), seq, idempotency_key)).fetchone()
            assert row is not None
            return self._message(row)

    def list_messages(self, owner_id: UUID, *, after_sequence: int | None = None, limit: int = 50,
                      latest: bool = False) -> OrganizationMessagePage:
        """Return a bounded keyset page; no message from another owner is reachable."""
        limit = max(1, min(limit, 200))
        chat = self.get_chat(owner_id)
        if chat is None:
            return OrganizationMessagePage(items=[])
        with self._db() as db:
            if latest and after_sequence is None:
                rows = db.execute("SELECT * FROM (SELECT * FROM agents_service.organization_messages WHERE chat_id=%s ORDER BY sequence DESC LIMIT %s) recent ORDER BY sequence",
                                  (chat.chat_id, limit)).fetchall()
            elif after_sequence is None:
                rows = db.execute("SELECT * FROM agents_service.organization_messages WHERE chat_id=%s ORDER BY sequence LIMIT %s",
                                  (chat.chat_id, limit + 1)).fetchall()
            else:
                rows = db.execute("SELECT * FROM agents_service.organization_messages WHERE chat_id=%s AND sequence>%s ORDER BY sequence LIMIT %s",
                                  (chat.chat_id, after_sequence, limit + 1)).fetchall()
        page = rows[:limit]
        return OrganizationMessagePage(items=[self._message(row) for row in page],
                                       next_sequence=page[-1]["sequence"] if len(rows) > limit else None)

    def clear_messages(self, owner_id: UUID, expected_context_revision: int) -> OrganizationContext:
        """Clear idle owner history atomically; preserve memory, vault, tasks and financial receipts.

        Busy work and ambiguous effects fail closed. Incrementing the authoritative
        context revision fences old provider requests and stale context writes.
        Terminal task leases are invalidated so delayed worker reports cannot return.
        """
        chat = self.get_or_create_chat(owner_id)
        with self._db() as db, db.transaction():
            db.execute("SELECT chat_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            context = db.execute("SELECT * FROM agents_service.organization_context WHERE owner_id=%s FOR UPDATE", (owner_id,)).fetchone()
            if context is None or context["revision"] != expected_context_revision:
                raise OrganizationConflict("context_revision_conflict")
            turns = db.execute("SELECT status FROM agents_service.organization_turns WHERE owner_id=%s FOR UPDATE", (owner_id,)).fetchall()
            tasks = db.execute("SELECT status FROM agents_service.organization_tasks WHERE owner_id=%s FOR UPDATE", (owner_id,)).fetchall()
            reservations = db.execute("SELECT reservation_id FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND status='reserved' FOR UPDATE", (owner_id,)).fetchall()
            providers = db.execute("SELECT request_hash FROM agents_service.organization_provider_calls WHERE owner_id=%s AND status='in_flight' FOR UPDATE", (owner_id,)).fetchall()
            effects = db.execute("""SELECT j.call_id FROM agents_service.organization_tool_journal j
                JOIN agents_service.organization_turns t ON t.turn_id=j.turn_id
                WHERE t.owner_id=%s AND j.status='started' FOR UPDATE OF j""", (owner_id,)).fetchall()
            if (any(row["status"] in {"queued", "processing", "retrying"} for row in turns)
                    or any(row["status"] not in {"succeeded", "failed", "stopped"} for row in tasks)
                    or reservations or providers or effects):
                raise OrganizationConflict("organization_chat_busy")
            db.execute("UPDATE agents_service.organization_tasks SET lease_token=NULL,lease_until=NULL WHERE owner_id=%s", (owner_id,))
            db.execute("DELETE FROM agents_service.organization_pending_answers WHERE chat_id=%s", (chat.chat_id,))
            db.execute("DELETE FROM agents_service.organization_messages WHERE chat_id=%s", (chat.chat_id,))
            # Journals cascade from turns; usage reservations/ledger and provider receipts have no turn FK.
            db.execute("DELETE FROM agents_service.organization_turns WHERE owner_id=%s AND chat_id=%s", (owner_id, chat.chat_id))
            row = db.execute("""UPDATE agents_service.organization_context SET current_summary='{}'::jsonb,
                revision=revision+1,updated_at=now() WHERE owner_id=%s RETURNING *""", (owner_id,)).fetchone()
            assert row is not None
            return OrganizationContext.model_validate(dict(row))

    def create_turn(self, owner_id: UUID, message: str, idempotency_key: str, *, task_id: UUID | None = None,
                    task_lease_token: UUID | None = None, reasoning_effort: OrganizationReasoningEffort = "off") -> OrganizationTurn:
        """Queue one owner turn and its user message atomically; retries return the same turn."""
        chat = self.get_or_create_chat(owner_id)
        with self._db() as db, db.transaction():
            db.execute("SELECT owner_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            existing = db.execute("SELECT * FROM agents_service.organization_turns WHERE owner_id=%s AND idempotency_key=%s FOR UPDATE",
                                  (owner_id, idempotency_key)).fetchone()
            if existing:
                if (existing["user_message"] != message or existing["task_id"] != task_id
                        or existing.get("reasoning_effort", "off") != reasoning_effort):
                    raise OrganizationConflict("idempotency_key_conflict")
                return self._turn(existing)
            lock = db.execute("SELECT * FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,)).fetchone()
            if lock is None:
                raise OrganizationNotFound("organization_chat_not_found")
            active = db.execute("SELECT turn_id FROM agents_service.organization_turns WHERE owner_id=%s AND status IN ('queued','processing','retrying') LIMIT 1",
                                (owner_id,)).fetchone()
            if active:
                raise OrganizationConflict("organization_turn_active")
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS sequence FROM agents_service.organization_turns WHERE chat_id=%s",
                                 (chat.chat_id,)).fetchone()["sequence"]
            turn_id = uuid4()
            row = db.execute("""INSERT INTO agents_service.organization_turns
                (turn_id,chat_id,owner_id,task_id,sequence,user_message,idempotency_key,source,task_lease_token,reasoning_effort)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (turn_id, chat.chat_id, owner_id, task_id, sequence, message, idempotency_key,
                 "workflow" if task_id else "employer", task_lease_token, reasoning_effort)).fetchone()
            if task_id is None:
                db.execute("UPDATE agents_service.organization_chats SET presence=true,last_seen_at=now() WHERE owner_id=%s", (owner_id,))
            db.execute("""INSERT INTO agents_service.organization_messages
                (message_id,chat_id,turn_id,role,content,sequence,idempotency_key)
                VALUES (%s,%s,%s,'user',%s,
                (SELECT COALESCE(MAX(sequence),-1)+1 FROM agents_service.organization_messages WHERE chat_id=%s),%s)""",
                (uuid4(), chat.chat_id, turn_id, message, chat.chat_id, "turn:" + idempotency_key))
            assert row is not None
            return self._turn(row)

    def get_turn(self, owner_id: UUID, turn_id: UUID) -> OrganizationTurn | None:
        """Read one turn under the asserted owner."""
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_turns WHERE owner_id=%s AND turn_id=%s",
                             (owner_id, turn_id)).fetchone()
            return self._turn(row) if row else None

    def claim_turn(self, *, lease_seconds: int = 300) -> OrganizationTurn | None:
        """Claim one queued turn with a short lease; expired calls remain fail-closed."""
        with self._db() as db, db.transaction():
            row = db.execute("""SELECT * FROM agents_service.organization_turns t
                WHERE (t.status IN ('queued','retrying') OR (t.status='processing' AND t.lease_until<now()))
                  AND t.task_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM agents_service.organization_turns active
                                  WHERE active.owner_id=t.owner_id AND active.status='processing'
                                    AND active.turn_id<>t.turn_id)
                ORDER BY t.created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if row is None:
                return None
            token = uuid4()
            row = db.execute("""UPDATE agents_service.organization_turns
                SET status='processing',lease_token=%s,lease_until=now()+(%s*interval '1 second'),updated_at=now()
                WHERE turn_id=%s RETURNING *""", (token, lease_seconds, row["turn_id"])).fetchone()
            assert row is not None
            return self._turn(row)

    def claim_turn_by_id(self, owner_id: UUID, turn_id: UUID, *, lease_seconds: int = 300) -> OrganizationTurn:
        """Claim a specific queued task turn after a worker created it."""
        with self._db() as db:
            row = db.execute("""UPDATE agents_service.organization_turns SET status='processing',lease_token=%s,
                lease_until=now()+(%s*interval '1 second'),updated_at=now()
                WHERE owner_id=%s AND turn_id=%s AND (status IN ('queued','retrying') OR (status='processing' AND lease_until<now())) RETURNING *""", (uuid4(), lease_seconds, owner_id, turn_id)).fetchone()
            if row is None:
                existing = db.execute("SELECT * FROM agents_service.organization_turns WHERE owner_id=%s AND turn_id=%s", (owner_id, turn_id)).fetchone()
                if existing is None:
                    raise OrganizationNotFound("organization_turn_not_found")
                return self._turn(existing)
            return self._turn(row)

    def update_turn(self, owner_id: UUID, turn_id: UUID, *, expected_lease_token: UUID | None = None, **values: Any) -> OrganizationTurn:
        """Update a turn's durable checkpoint under owner scope."""
        allowed = {"status", "provider_result", "reasoning_history", "input_tokens", "output_tokens", "rounds", "tool_calls",
                   "error_code", "error_message", "lease_token", "lease_until"}
        invalid = set(values) - allowed
        if invalid:
            raise ValueError(f"unsupported turn columns: {sorted(invalid)}")
        sets = ["updated_at=now()"]
        args: list[Any] = []
        for key, value in values.items():
            sets.append(f"{key}=%s")
            args.append(Jsonb(_json(value)) if key in {"provider_result", "reasoning_history"} else value)
        args.extend((owner_id, turn_id))
        clause = ""
        if expected_lease_token is not None:
            clause = " AND lease_token=%s AND lease_until>now() AND status='processing'"
            args.append(expected_lease_token)
        with self._db() as db:
            row = db.execute(f"UPDATE agents_service.organization_turns SET {','.join(sets)} WHERE owner_id=%s AND turn_id=%s{clause} RETURNING *", args).fetchone()
            if row is None:
                raise OrganizationNotFound("organization_turn_not_found")
            return self._turn(row)

    def lease_valid(self, turn: OrganizationTurn) -> bool:
        """Check a worker lease immediately before any external call or write."""
        with self._db() as db:
            return bool(db.execute("""SELECT 1 FROM agents_service.organization_turns t
                WHERE turn_id=%s AND t.owner_id=%s AND t.status='processing' AND t.lease_token=%s AND t.lease_until>now()
                AND (t.task_id IS NULL OR EXISTS(SELECT 1 FROM agents_service.organization_tasks task
                    JOIN agents_service.organization_chats chat ON chat.owner_id=task.owner_id
                    WHERE task.task_id=t.task_id AND task.owner_id=t.owner_id AND task.lease_token=t.task_lease_token
                        AND task.status NOT IN ('stopped','failed')
                        AND (task.kind<>'stand_in' OR (chat.last_seen_at IS NOT NULL
                            AND (NOT chat.presence OR chat.last_seen_at<now()-interval '120 seconds')))))""",
                                   (turn.turn_id, turn.owner_id, turn.lease_token)).fetchone())

    def journal_get(self, turn_id: UUID, call_id: str) -> OrganizationToolJournal | None:
        """Read a tool replay fence."""
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_tool_journal WHERE turn_id=%s AND call_id=%s", (turn_id, call_id)).fetchone()
            return OrganizationToolJournal.model_validate(dict(row)) if row else None

    def journal_start(self, turn: OrganizationTurn, call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> OrganizationToolJournal:
        """Persist the ambiguous-call fence before leaving the database."""
        with self._db() as db, db.transaction():
            live = db.execute("SELECT turn_id FROM agents_service.organization_turns WHERE turn_id=%s AND owner_id=%s AND lease_token=%s AND status='processing' AND lease_until>now() FOR UPDATE", (turn.turn_id, turn.owner_id, turn.lease_token)).fetchone()
            if live is None:
                raise OrganizationConflict("organization_lease_lost")
            row = db.execute("""INSERT INTO agents_service.organization_tool_journal
                (turn_id,call_id,tool_name,arguments,status) VALUES (%s,%s,%s,%s,'started')
                ON CONFLICT(turn_id,call_id) DO UPDATE SET updated_at=now() RETURNING *""", (turn.turn_id, call_id, tool_name, Jsonb(_json(dict(arguments))))).fetchone()
            assert row is not None
            return OrganizationToolJournal.model_validate(dict(row))

    def journal_finish(self, turn_id: UUID, call_id: str, *, status: str, result: Mapping[str, Any] | None = None,
                       error_code: str | None = None) -> OrganizationToolJournal:
        """Close a journal entry after a tool dispatch returns."""
        if status not in {"completed", "failed"}:
            raise ValueError("invalid organization journal status")
        with self._db() as db:
            row = db.execute("""UPDATE agents_service.organization_tool_journal
                SET status=%s,result=%s,error_code=%s,updated_at=now()
                WHERE turn_id=%s AND call_id=%s RETURNING *""", (status, Jsonb(_json(result or {})), error_code, turn_id, call_id)).fetchone()
            if row is None:
                raise OrganizationNotFound("organization_tool_journal_not_found")
            return OrganizationToolJournal.model_validate(dict(row))

    def create_task(self, owner_id: UUID, request: OrganizationTaskRequest, *, source_turn_id: UUID | None = None,
                    source_lease_token: UUID | None = None) -> OrganizationTask:
        """Persist an employer-authorized task with candidate/conversation references."""
        from .organization_delegation import normalize_task_scope
        refs = normalize_task_scope(request.kind, request.conversation_refs)
        chat = self.get_or_create_chat(owner_id)
        with self._db() as db, db.transaction():
            db.execute("SELECT owner_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            if source_turn_id is not None:
                source = db.execute("""SELECT 1 FROM agents_service.organization_turns
                    WHERE owner_id=%s AND chat_id=%s AND turn_id=%s AND status='processing'
                    AND lease_token=%s AND lease_until>now() FOR UPDATE""",
                                    (owner_id, chat.chat_id, source_turn_id, source_lease_token)).fetchone()
                if source is None:
                    raise OrganizationConflict("organization_lease_lost")
            existing = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s AND idempotency_key=%s FOR UPDATE",
                                  (owner_id, request.idempotency_key)).fetchone()
            if existing:
                if (existing["instruction"] != request.instruction or existing["kind"] != request.kind
                        or existing["conversation_refs"].get("process_ids") != refs["process_ids"]
                        or existing["conversation_refs"].get("availability") != refs["availability"]):
                    raise OrganizationConflict("idempotency_key_conflict")
                return self._task(existing)
            row = db.execute("""INSERT INTO agents_service.organization_tasks
                (task_id,chat_id,owner_id,kind,instruction,conversation_refs,idempotency_key,result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (uuid4(), chat.chat_id, owner_id, request.kind, request.instruction, Jsonb(refs), request.idempotency_key,
                    Jsonb({"authorization": {"source_turn_id": str(source_turn_id) if source_turn_id else None,
                                              "source": "employer_chat" if source_turn_id else "employer_api"}}))).fetchone()
            assert row is not None
            return self._task(row)

    def list_tasks(self, owner_id: UUID, *, limit: int = 50) -> list[OrganizationTask]:
        """List only tasks owned by the authenticated employer."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s ORDER BY created_at DESC LIMIT %s", (owner_id, max(1, min(limit, 200)))).fetchall()
            return [self._task(row) for row in rows]

    def claim_task(self, *, lease_seconds: int = 300, owner_id: UUID | None = None) -> OrganizationTask | None:
        """Claim one task; stopped tasks and expired ambiguous tasks remain untouched."""
        with self._db() as db, db.transaction():
            row = db.execute("""SELECT * FROM agents_service.organization_tasks t
                WHERE ((t.status IN ('queued','retrying','waiting') AND t.next_run_at<=now())
                    OR (t.status='processing' AND t.lease_until<now()))
                AND (%s::uuid IS NULL OR t.owner_id=%s)
                AND NOT EXISTS(SELECT 1 FROM agents_service.organization_tasks busy WHERE busy.owner_id=t.owner_id
                    AND busy.task_id<>t.task_id AND busy.status='processing' AND busy.lease_until>now())
                AND NOT EXISTS(SELECT 1 FROM agents_service.organization_turns busy WHERE busy.owner_id=t.owner_id
                    AND busy.task_id IS NULL AND busy.status IN ('queued','processing','retrying'))
                ORDER BY t.next_run_at,t.created_at FOR UPDATE SKIP LOCKED LIMIT 1""", (owner_id, owner_id)).fetchone()
            if row is None:
                return None
            locked = db.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked", (str(row["owner_id"]),)).fetchone()
            if not locked["locked"]:
                return None
            busy = db.execute("SELECT 1 FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id<>%s AND status='processing' AND lease_until>now()", (row["owner_id"], row["task_id"])).fetchone()
            if busy:
                return None
            token = uuid4()
            row = db.execute("UPDATE agents_service.organization_tasks SET status='processing',lease_token=%s,lease_until=now()+(%s*interval '1 second'),updated_at=now() WHERE task_id=%s RETURNING *", (token, lease_seconds, row["task_id"])).fetchone()
            assert row is not None
            return self._task(row)

    def update_task(self, owner_id: UUID, task_id: UUID, *, status: str, result: Mapping[str, Any] | None = None,
                    error_code: str | None = None, stop_reason: str | None = None,
                    lease_token: UUID | None = None, delay_seconds: int = 30) -> OrganizationTask:
        """Record task completion, failure or explicit stop under owner scope."""
        if status not in {"queued", "processing", "retrying", "waiting", "waiting_employer", "succeeded", "failed", "stopped"}:
            raise ValueError("invalid organization task status")
        with self._db() as db:
            lease_clause = " AND lease_token=%s AND lease_until>now() AND status='processing'" if lease_token is not None else ""
            params: list[Any] = [status, Jsonb(_json(result)) if result is not None else None, error_code, stop_reason,
                                 max(0, min(delay_seconds, 86400)), owner_id, task_id]
            if lease_token is not None:
                params.append(lease_token)
            row = db.execute(f"""UPDATE agents_service.organization_tasks
                SET status=%s,result=COALESCE(%s,result),error_code=COALESCE(%s, error_code),stop_reason=COALESCE(%s, stop_reason),
                    next_run_at=now()+(%s*interval '1 second'),updated_at=now()
                WHERE owner_id=%s AND task_id=%s{lease_clause} RETURNING *""", params).fetchone()
            if row is None:
                raise OrganizationNotFound("organization_task_not_found")
            return self._task(row)

    def task_lease_valid(self, task: OrganizationTask) -> bool:
        """Check that a delegated task still owns its worker lease."""
        with self._db() as db:
            return bool(db.execute("SELECT 1 FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id=%s AND status='processing' AND lease_token=%s AND lease_until>now()",
                                   (task.owner_id, task.task_id, task.lease_token)).fetchone())

    def stop_task(self, owner_id: UUID, task_id: UUID, reason: str = "stopped_by_owner") -> OrganizationTask:
        """Cancel a queued or processing task; worker checks this status before dispatch."""
        with self._db() as db:
            row = db.execute("UPDATE agents_service.organization_tasks SET status='stopped',stop_reason=%s,updated_at=now() WHERE owner_id=%s AND task_id=%s AND status IN ('queued','retrying','processing','waiting','waiting_employer') RETURNING *", (reason, owner_id, task_id)).fetchone()
            if row is None:
                existing = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id=%s", (owner_id, task_id)).fetchone()
                if existing:
                    return self._task(existing)
                raise OrganizationNotFound("organization_task_not_found")
            db.execute("""UPDATE agents_service.organization_turns SET status='failed',error_code='delegation_stopped',updated_at=now()
                WHERE owner_id=%s AND task_id=%s AND status IN ('queued','processing','retrying')""", (owner_id, task_id))
            return self._task(row)

    def get_context(self, owner_id: UUID) -> OrganizationContext:
        """Read current compact summary, separate from editable owner memory."""
        self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_context WHERE owner_id=%s", (owner_id,)).fetchone()
            assert row is not None
            return OrganizationContext.model_validate(dict(row))

    def get_task(self, owner_id: UUID, task_id: UUID) -> OrganizationTask:
        """Read authoritative task state and grant under owner scope."""
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id=%s",
                             (owner_id, task_id)).fetchone()
        if row is None:
            raise OrganizationNotFound("organization_task_not_found")
        return self._task(row)

    def heartbeat(self, owner_id: UUID, resource_id: UUID, lease_token: UUID, *, task: bool = False,
                  lease_seconds: int = 300) -> bool:
        """Extend only a live worker lease; stopped or reclaimed work is never resurrected."""
        table, key = ("organization_tasks", "task_id") if task else ("organization_turns", "turn_id")
        with self._db() as db:
            row = db.execute(f"UPDATE agents_service.{table} SET lease_until=now()+(%s*interval '1 second') "
                             f"WHERE owner_id=%s AND {key}=%s AND lease_token=%s AND status='processing' AND lease_until>now() RETURNING {key}",
                             (lease_seconds, owner_id, resource_id, lease_token)).fetchone()
        return row is not None

    def bind_task_turn(self, turn: OrganizationTurn, task: OrganizationTask) -> OrganizationTurn:
        """Bind a recovered turn to this worker's live task lease, fencing the prior executor."""
        with self._db() as db:
            row = db.execute("""UPDATE agents_service.organization_turns SET task_lease_token=%s
                WHERE owner_id=%s AND turn_id=%s AND lease_token=%s AND lease_until>now()
                    AND task_id=%s AND status='processing' RETURNING *""",
                             (task.lease_token, turn.owner_id, turn.turn_id, turn.lease_token, task.task_id)).fetchone()
        if row is None:
            raise OrganizationConflict("organization_lease_lost")
        return self._turn(row)

    def resume_task(self, owner_id: UUID, task_id: UUID, availability: list[dict]) -> OrganizationTask:
        """Apply a new employer schedule and wake its waiting task without changing candidate scope."""
        from .organization_delegation import AvailabilitySlot
        slots = [AvailabilitySlot.model_validate(value).model_dump(mode="json") for value in availability]
        if not slots or len(slots) > 100:
            raise ValueError("availability_required")
        with self._db() as db, db.transaction():
            task = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id=%s FOR UPDATE", (owner_id, task_id)).fetchone()
            if task is None or task["status"] != "waiting_employer":
                raise OrganizationConflict("task_not_waiting_for_employer")
            progress = dict(task["result"] or {})
            progress.pop("processes", None)
            progress["generation"] = int(progress.get("generation", 0)) + 1
            row = db.execute("""UPDATE agents_service.organization_tasks
                SET conversation_refs=jsonb_set(conversation_refs,'{availability}',%s::jsonb),status='waiting',
                    next_run_at=now(),updated_at=now(),result=%s
                WHERE owner_id=%s AND task_id=%s AND status='waiting_employer' RETURNING *""",
                             (Jsonb(slots), Jsonb(progress), owner_id, task_id)).fetchone()
            assert row is not None
            db.execute("UPDATE agents_service.organization_pending_answers SET answered=true,answer=%s,answered_at=now() WHERE task_id=%s AND chat_id=%s AND answered=false", (Jsonb({"availability": slots}), task_id, row["chat_id"]))
        return self._task(row)

    def ask_owner(self, task: OrganizationTask, prompt: str, *, availability: bool = False) -> None:
        """Persist a durable question and its global chat message once per task/context revision."""
        key = f"task:{task.task_id}:question:{(task.result or {}).get('generation',0)}:{hashlib.sha256(prompt.encode()).hexdigest()}"
        with self._db() as db, db.transaction():
            db.execute("SELECT chat_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (task.owner_id,))
            live = db.execute("""SELECT 1 FROM agents_service.organization_tasks
                WHERE owner_id=%s AND task_id=%s AND lease_token=%s
                    AND status IN ('queued','processing','retrying','waiting','waiting_employer')""",
                              (task.owner_id, task.task_id, task.lease_token)).fetchone()
            if live is None:
                raise OrganizationConflict("organization_task_lease_lost")
            existing = db.execute("SELECT message_id FROM agents_service.organization_messages WHERE chat_id=%s AND idempotency_key=%s", (task.chat_id, key)).fetchone()
            if existing:
                return
            db.execute("""INSERT INTO agents_service.organization_messages(message_id,chat_id,role,content,sequence,idempotency_key)
                VALUES(%s,%s,'assistant',%s,(SELECT COALESCE(MAX(sequence),-1)+1 FROM agents_service.organization_messages WHERE chat_id=%s),%s)""",
                       (uuid4(), task.chat_id, prompt, task.chat_id, key))
            db.execute("""INSERT INTO agents_service.organization_pending_answers(answer_id,chat_id,task_id,prompt,answer_schema)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (uuid4(), task.chat_id, task.task_id, prompt, Jsonb({"type": "object", "required": ["availability" if availability else "instruction"]})))

    def list_pending_answers(self, owner_id: UUID, *, limit: int = 32) -> list[PendingAnswer]:
        """List unanswered questions in chronological order for the owner chat."""
        chat = self.get_chat(owner_id)
        if chat is None:
            return []
        with self._db() as db:
            rows = db.execute("SELECT * FROM agents_service.organization_pending_answers WHERE chat_id=%s AND answered=false ORDER BY created_at LIMIT %s", (chat.chat_id, max(1, min(limit, 64)))).fetchall()
            return [PendingAnswer.model_validate(dict(row)) for row in rows]

    def answer_pending(self, owner_id: UUID, answer_id: UUID, value: Any) -> PendingAnswer:
        """Answer one pending question once; repeated identical reads are safe."""
        chat = self.get_chat(owner_id)
        if chat is None:
            raise OrganizationNotFound("organization_chat_not_found")
        with self._db() as db, db.transaction():
            db.execute("SELECT chat_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            row = db.execute("SELECT * FROM agents_service.organization_pending_answers WHERE answer_id=%s AND chat_id=%s FOR UPDATE", (answer_id, chat.chat_id)).fetchone()
            if row is None:
                raise OrganizationNotFound("pending_answer_not_found")
            if row["answered"]:
                if row["answer"] != _json(value):
                    raise OrganizationConflict("pending_answer_conflict")
                return PendingAnswer.model_validate(dict(row))
            if row["task_id"] is not None:
                task = db.execute("SELECT * FROM agents_service.organization_tasks WHERE owner_id=%s AND task_id=%s AND status='waiting_employer' FOR UPDATE", (owner_id, row["task_id"])).fetchone()
                if task is None:
                    raise OrganizationConflict("task_not_waiting_for_employer")
                if not isinstance(value, dict):
                    raise OrganizationConflict("pending_answer_invalid")
                refs = dict(task["conversation_refs"])
                instruction = task["instruction"]
                if "availability" in row["answer_schema"].get("required", []):
                    from .organization_delegation import AvailabilitySlot
                    slots = value.get("availability")
                    if not isinstance(slots, list) or not 1 <= len(slots) <= 100:
                        raise OrganizationConflict("availability_required")
                    refs["availability"] = [AvailabilitySlot.model_validate(slot).model_dump(mode="json") for slot in slots]
                else:
                    clarification = value.get("instruction")
                    if not isinstance(clarification, str) or not clarification.strip() or len(clarification) > 8000:
                        raise OrganizationConflict("pending_answer_invalid")
                    instruction = (instruction + "\nEmployer clarification: " + clarification)[-8000:]
                progress = dict(task["result"] or {})
                progress.pop("processes", None)
                progress["generation"] = int(progress.get("generation", 0)) + 1
                db.execute("UPDATE agents_service.organization_tasks SET status='waiting',next_run_at=now(),updated_at=now(),conversation_refs=%s,instruction=%s,result=%s,error_code=NULL WHERE task_id=%s", (Jsonb(refs), instruction, Jsonb(progress), task["task_id"]))
                import json
                db.execute("""INSERT INTO agents_service.organization_messages(message_id,chat_id,role,content,sequence,idempotency_key)
                    VALUES(%s,%s,'user',%s,(SELECT COALESCE(MAX(sequence),-1)+1 FROM agents_service.organization_messages WHERE chat_id=%s),%s)""", (uuid4(), chat.chat_id, json.dumps(value, ensure_ascii=False)[:32000], chat.chat_id, f"answer:{answer_id}"))
            row = db.execute("""UPDATE agents_service.organization_pending_answers
                SET answer=%s,answered=true,answered_at=now()
                WHERE answer_id=%s AND chat_id=%s AND answered=false RETURNING *""", (Jsonb(_json(value)), answer_id, chat.chat_id)).fetchone()
            assert row is not None
            return PendingAnswer.model_validate(dict(row))

    def set_context(self, owner_id: UUID, summary: Mapping[str, Any], *, expected_revision: int | None = None) -> OrganizationContext:
        """Replace the runtime-owned current summary and increment its revision."""
        self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("UPDATE agents_service.organization_context SET current_summary=%s,revision=revision+1,updated_at=now() WHERE owner_id=%s AND (%s::bigint IS NULL OR revision=%s) RETURNING *", (Jsonb(_json(dict(summary))), owner_id, expected_revision, expected_revision)).fetchone()
            if row is None:
                raise OrganizationConflict("context_revision_conflict")
            return OrganizationContext.model_validate(dict(row))

    def get_memory(self, owner_id: UUID) -> OrganizationMemory:
        """Read memory under owner scope."""
        self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM agents_service.organization_memory WHERE owner_id=%s", (owner_id,)).fetchone()
            assert row is not None
            return OrganizationMemory.model_validate(dict(row))

    def patch_memory(self, owner_id: UUID, expected_revision: int, value: Mapping[str, Any]) -> OrganizationMemory:
        """Apply memory only when the caller's revision is current."""
        self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("UPDATE agents_service.organization_memory SET value=%s,revision=revision+1,updated_at=now() WHERE owner_id=%s AND revision=%s RETURNING *", (Jsonb(_json(dict(value))), owner_id, expected_revision)).fetchone()
            if row is None:
                raise OrganizationConflict("memory_revision_conflict")
            return OrganizationMemory.model_validate(dict(row))

    def get_budget(self, owner_id: UUID, *, pricing_configured: bool) -> OrganizationBudget:
        """Return exact rolling seven-day ledger plus outstanding reservations."""
        self.get_or_create_chat(owner_id)
        with self._db() as db:
            row = db.execute("""SELECT
                COALESCE((SELECT SUM(total_micro_usd) FROM agents_service.organization_usage_ledger WHERE owner_id=%s AND created_at>=now()-interval '7 days'),0) AS spent,
                COALESCE((SELECT SUM(upper_bound_micro_usd) FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND status='reserved'),0) AS reserved""", (owner_id, owner_id)).fetchone()
        spent = Decimal(str(row["spent"] or 0))
        reserved = Decimal(str(row["reserved"] or 0))
        limit = Decimal(1000000)
        return OrganizationBudget(limit_micro_usd=limit, spent_micro_usd=spent, reserved_micro_usd=reserved,
                                   available_micro_usd=max(Decimal(0), limit - spent - reserved), pricing_configured=pricing_configured)

    def reserve_usage(self, owner_id: UUID, turn_id: UUID, amount: Decimal, *, input_bound: Decimal = Decimal(0),
                      output_bound: Decimal = Decimal(0)) -> UUID:
        """Reserve a safe upper bound atomically; missing capacity fails closed."""
        if amount < 0:
            raise ValueError("usage reservation cannot be negative")
        self.get_or_create_chat(owner_id)
        with self._db() as db, db.transaction():
            db.execute("SELECT owner_id FROM agents_service.organization_chats WHERE owner_id=%s FOR UPDATE", (owner_id,))
            totals = db.execute("""SELECT
                COALESCE((SELECT SUM(total_micro_usd) FROM agents_service.organization_usage_ledger WHERE owner_id=%s AND created_at>=now()-interval '7 days'),0) AS spent,
                COALESCE((SELECT SUM(upper_bound_micro_usd) FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND status='reserved'),0) AS reserved""", (owner_id, owner_id)).fetchone()
            spent = Decimal(str(totals["spent"] or 0))
            reserved = Decimal(str(totals["reserved"] or 0))
            if spent + reserved + amount > Decimal(1000000):
                raise OrganizationConflict("organization_usage_limit_exceeded")
            reservation_id = uuid4()
            db.execute("""INSERT INTO agents_service.organization_usage_reservations
                (reservation_id,owner_id,turn_id,input_upper_bound_micro_usd,output_upper_bound_micro_usd,upper_bound_micro_usd,status)
                VALUES (%s,%s,%s,%s,%s,%s,'reserved')""", (reservation_id, owner_id, turn_id, input_bound, output_bound, amount))
            return reservation_id

    def capture_usage(self, owner_id: UUID, reservation_id: UUID, *, input_tokens: int, output_tokens: int,
                      input_micro_usd: Decimal, output_micro_usd: Decimal, rate_config: Mapping[str, Any]) -> Decimal:
        """Capture exact provider usage and create one immutable ledger row."""
        total = input_micro_usd + output_micro_usd
        with self._db() as db, db.transaction():
            row = db.execute("SELECT * FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND reservation_id=%s FOR UPDATE", (owner_id, reservation_id)).fetchone()
            if row is None:
                raise OrganizationNotFound("organization_reservation_not_found")
            if row["status"] == "captured":
                saved = db.execute("SELECT total_micro_usd FROM agents_service.organization_usage_ledger WHERE reservation_id=%s", (reservation_id,)).fetchone()
                return Decimal(str(saved["total_micro_usd"])) if saved else total
            if (input_micro_usd > Decimal(str(row["input_upper_bound_micro_usd"]))
                    or output_micro_usd > Decimal(str(row["output_upper_bound_micro_usd"]))
                    or total > Decimal(str(row["upper_bound_micro_usd"]))):
                raise OrganizationConflict("provider_usage_exceeded_reservation")
            db.execute("UPDATE agents_service.organization_usage_reservations SET status='captured',updated_at=now() WHERE reservation_id=%s", (reservation_id,))
            db.execute("""INSERT INTO agents_service.organization_usage_ledger
                (ledger_id,reservation_id,owner_id,input_tokens,output_tokens,input_micro_usd,output_micro_usd,total_micro_usd,rate_config)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (uuid4(), reservation_id, owner_id, input_tokens, output_tokens, input_micro_usd, output_micro_usd, total, Jsonb(_json(dict(rate_config)))))
        return total

    def release_usage(self, owner_id: UUID, reservation_id: UUID) -> None:
        """Release a temporary reservation when a completed provider response is cached."""
        with self._db() as db:
            db.execute("UPDATE agents_service.organization_usage_reservations SET status='released',updated_at=now() WHERE owner_id=%s AND reservation_id=%s AND status='reserved'",
                       (owner_id, reservation_id))

    def provider_begin(self, owner_id: UUID, request_hash: str, reservation_id: UUID) -> Mapping[str, Any] | None:
        """Create the paid-call idempotency fence; completed responses are replayable."""
        with self._db() as db, db.transaction():
            row = db.execute("SELECT * FROM agents_service.organization_provider_calls WHERE owner_id=%s AND request_hash=%s FOR UPDATE", (owner_id, request_hash)).fetchone()
            if row is not None:
                if row["status"] == "completed" and row["response"] is not None:
                    return row["response"]
                raise OrganizationConflict("provider_call_in_flight")
            db.execute("INSERT INTO agents_service.organization_provider_calls(owner_id,request_hash,status,reservation_id) VALUES (%s,%s,'in_flight',%s)", (owner_id, request_hash, reservation_id))
            return None

    def provider_complete(self, owner_id: UUID, request_hash: str, response: Mapping[str, Any], *, input_tokens: int, output_tokens: int) -> None:
        """Persist a structurally valid provider response exactly once."""
        with self._db() as db:
            row = db.execute("UPDATE agents_service.organization_provider_calls SET status='completed',response=%s,input_tokens=%s,output_tokens=%s,updated_at=now() WHERE owner_id=%s AND request_hash=%s AND status='in_flight' RETURNING request_hash", (Jsonb(_json(dict(response))), input_tokens, output_tokens, owner_id, request_hash)).fetchone()
            if row is None:
                raise OrganizationConflict("provider_call_fence_missing")
