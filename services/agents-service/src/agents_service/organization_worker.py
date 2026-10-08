"""Background worker for durable organization turns and delegated workflows."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from uuid import UUID

from .organization_models import OrganizationTask, OrganizationTurn
from .organization_runtime import OrganizationRuntime, ToolRegistry
from .organization_store import OrganizationConflict, OrganizationStore
from .organization_tools import ToolRegistry as BaseToolRegistry
from .organization_workflows import OrganizationWorkflows, WorkflowToolRegistry

logger = logging.getLogger(__name__)


def _stable_error_code(error: BaseException) -> str:
    """Return a bounded durable code without persisting exception text or secrets."""
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code[:120]
    if isinstance(error, OrganizationConflict):
        return "organization_conflict"
    return "organization_workflow_failed"


class OrganizationWorker:
    """Poll durable turns and advance delegated tasks through the workflow state machine."""

    def __init__(self, store: OrganizationStore, runtime: OrganizationRuntime | None = None, *,
                 lease_seconds: int = 300, registry: ToolRegistry | None = None,
                 workflows: OrganizationWorkflows | None = None) -> None:
        self.store = store
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = min(10.0, max(0.01, lease_seconds / 3))
        base_registry = registry or BaseToolRegistry(store.database_url)
        workflow_registry = WorkflowToolRegistry(base_registry, store)
        if runtime is None:
            runtime = OrganizationRuntime(store, registry=workflow_registry)
        elif hasattr(runtime, "registry"):
            # Injected real runtimes must use the server-owned workflow wrapper;
            # simple fake runtimes used by tests need no special protocol.
            runtime.registry = workflow_registry
        self.runtime = runtime
        self.workflow_registry = workflow_registry
        self.workflows = workflows or OrganizationWorkflows(store, runtime, workflow_registry,
                                                             poll_seconds=max(1, int(self.heartbeat_interval * 3)))

    def _heartbeat_turn(self, turn: OrganizationTurn) -> bool:
        """Extend a normal employer turn lease while its provider call is running."""
        if turn.lease_token is None:
            return False
        return self.store.heartbeat(turn.owner_id, turn.turn_id, turn.lease_token,
                                    lease_seconds=self.lease_seconds)

    def _heartbeat_task(self, task: OrganizationTask) -> bool:
        """Extend a task lease and all processing turns currently attached to it."""
        if task.lease_token is None:
            return False
        if not self.store.heartbeat(task.owner_id, task.task_id, task.lease_token,
                                    task=True, lease_seconds=self.lease_seconds):
            return False
        try:
            current = self.store.get_task(task.owner_id, task.task_id)
            progress = current.result or {}
            processes = progress.get("processes", {}) if isinstance(progress, dict) else {}
            if not isinstance(processes, dict):
                return True
            seen: set[UUID] = set()
            for state in processes.values():
                if not isinstance(state, dict) or not state.get("turn_id"):
                    continue
                try:
                    turn_id = UUID(str(state["turn_id"]))
                except (ValueError, TypeError, AttributeError):
                    continue
                if turn_id in seen:
                    continue
                seen.add(turn_id)
                turn = self.store.get_turn(task.owner_id, turn_id)
                if turn is not None and turn.status == "processing" and turn.lease_token is not None:
                    self.store.heartbeat(task.owner_id, turn.turn_id, turn.lease_token,
                                         lease_seconds=self.lease_seconds)
        except Exception:  # noqa: BLE001 - heartbeat observation is a non-authoritative side path.
            # The processing runtime performs authoritative lease checks before
            # effects. A heartbeat observation failure must not fabricate task
            # completion or replace the durable error with exception text.
            logger.warning("organization_worker_heartbeat_observation_failed")
        return True

    def _heartbeat_loop(self, stop: threading.Event, heartbeat: Callable[[], bool]) -> None:
        """Refresh one resource until processing ends or its lease is fenced."""
        while not stop.wait(self.heartbeat_interval):
            try:
                if not heartbeat():
                    return
            except Exception:  # noqa: BLE001 - the runtime performs the authoritative lease check.
                # The next runtime/store lease check is authoritative.
                logger.warning("organization_worker_heartbeat_failed")
                continue

    def _run_turn(self, turn: OrganizationTurn) -> None:
        """Run one claimed turn with a bounded lease heartbeat."""
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(stop, lambda: self._heartbeat_turn(turn)), daemon=True)
        heartbeat.start()
        try:
            self.runtime.run_turn(turn)
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval * 2))

    def _run_task(self, task: OrganizationTask) -> None:
        """Advance one delegated task; workflow code owns durable status transitions."""
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(stop, lambda: self._heartbeat_task(task)), daemon=True)
        heartbeat.start()
        try:
            self.workflows.process(task)
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval * 2))

    def process_once(self) -> bool:
        """Process one turn or workflow task without synthesizing completion state."""
        turn = self.store.claim_turn(lease_seconds=self.lease_seconds)
        if turn is not None:
            try:
                self._run_turn(turn)
            except Exception as error:  # noqa: BLE001 - worker boundary converts it to a stable code.
                code = _stable_error_code(error)
                if code not in {"organization_lease_lost", "lease_lost"}:
                    try:
                        self.store.update_turn(turn.owner_id, turn.turn_id,
                                               expected_lease_token=turn.lease_token,
                                               status="retrying", error_code=code, error_message=code)
                    except Exception:  # noqa: BLE001 - a fenced lease must not be overwritten.
                        # A stale lease must never be overwritten by a worker error.
                        logger.warning("organization_worker_turn_update_failed")
            return True
        task = self.store.claim_task(lease_seconds=self.lease_seconds)
        if task is None:
            return False
        try:
            self._run_task(task)
        except Exception as error:  # noqa: BLE001 - worker boundary converts it to a stable code.
            code = _stable_error_code(error)
            if code in {"organization_lease_lost", "delegation_lease_lost"}:
                return True
            try:
                self.store.update_task(task.owner_id, task.task_id, status="retrying",
                                       error_code=code, lease_token=task.lease_token)
            except Exception:  # noqa: BLE001 - a fenced lease must not be overwritten.
                # A stale lease must never be overwritten by a worker error.
                logger.warning("organization_worker_task_update_failed")
        return True

    def run(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        """Poll until graceful shutdown while retaining current durable leases."""
        while not stop.is_set():
            if not self.process_once():
                stop.wait(poll_seconds)
