"""Durable candidate conversations: event-driven waits and explicit employer delegation."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from agents_service.organization_delegation import AvailabilitySlot, TaskScope
from agents_service.organization_models import (
    OrganizationContract,
    OrganizationTask,
    OrganizationTaskRequest,
    OrganizationTurn,
)
from agents_service.organization_store import OrganizationConflict, OrganizationStore
from agents_service.organization_tools import OrganizationToolError, ToolRegistry


class TaskStartArgs(TaskScope):
    """A delegation inferred from the current authenticated employer instruction."""

    kind: Literal["schedule_interview", "inquiry", "stand_in"]
    instruction: str = Field(min_length=1, max_length=8000)


class TaskIdArgs(OrganizationContract):
    """One owner-scoped task identifier."""

    task_id: UUID


class TaskListArgs(OrganizationContract):
    """Owner task discovery requires no model-selected identity."""

    limit: int = Field(default=50, strict=True, ge=1, le=200)


class TaskAvailabilityArgs(TaskIdArgs):
    """Employer-confirmed slots used to resume a waiting negotiation."""

    availability: list[AvailabilitySlot] = Field(min_length=1, max_length=100)


class TaskCompleteArgs(OrganizationContract):
    """End an inquiry or escalate a negotiation; interview success is verified from recruitment."""

    outcome: Literal["completed", "needs_employer"]
    report: str = Field(min_length=1, max_length=16000)
    process_id: UUID


class MemoryWriteArgs(OrganizationContract):
    """Explicit retained employer facts with revision protection."""

    expected_revision: int = Field(strict=True, ge=1)
    value: dict = Field(max_length=100)


_WORKFLOW_TOOLS = {
    "task_list": (TaskListArgs, "List owner tasks and pending employer questions to find a task to resume."),
    "task_start": (TaskStartArgs, "Start an explicitly requested candidate inquiry, interview negotiation, or offline stand-in."),
    "task_status": (TaskIdArgs, "Read current task scope, progress and unresolved questions."),
    "task_stop": (TaskIdArgs, "Revoke a candidate-chat delegation."),
    "task_availability": (TaskAvailabilityArgs, "Resume a waiting task with employer-confirmed availability."),
    "task_complete": (TaskCompleteArgs, "Finish a delegated inquiry with a report or return to the employer with a problem."),
    "memory_write": (MemoryWriteArgs, "Retain explicit employer preferences separately from the current task context."),
}


def _unwrap(value):
    return value.get("result", value) if isinstance(value, dict) else value


class WorkflowToolRegistry:
    """Add workflow tools without hiding any base tool or trusting model-selected actor IDs."""

    def __init__(self, base: ToolRegistry, store: OrganizationStore):
        self.base, self.store = base, store
        self.turn: OrganizationTurn | None = None

    def bind_turn(self, turn: OrganizationTurn) -> None:
        """Bind server-owned caller evidence; candidate messages cannot become employer instructions."""
        self.turn = turn

    def catalog(self) -> list[dict]:
        """Expose compact metadata; full JSON schemas enter context only on discovery."""
        return self.base.catalog() + [{"name": name, "category": "workflow", "description": value[1]}
                                      for name, value in _WORKFLOW_TOOLS.items()]

    def schemas(self, names: list[str]) -> list[dict]:
        """Hydrate selected workflow schemas and delegate the other schemas to the base registry."""
        result = self.base.schemas([name for name in names if name not in _WORKFLOW_TOOLS])
        for name in names:
            if name in _WORKFLOW_TOOLS:
                model, description = _WORKFLOW_TOOLS[name]
                result.append({"type": "function", "function": {"name": name, "description": description,
                               "parameters": model.model_json_schema()}})
        return result

    def _employer_turn(self, owner: UUID) -> OrganizationTurn:
        if self.turn is None or self.turn.owner_id != owner or self.turn.source != "employer" or not self.store.lease_valid(self.turn):
            raise OrganizationToolError("employer_instruction_required", status=403)
        return self.turn

    def dispatch(self, owner_id: str, name: str, arguments: dict, idempotency_key: str,
                 task_id: str | None = None) -> dict:
        """Validate workflow transitions and inject only the live task's server-owned chat authority."""
        owner = UUID(owner_id)
        if name in _WORKFLOW_TOOLS:
            args = _WORKFLOW_TOOLS[name][0].model_validate(arguments)
            if name == "task_list":
                return {"result": {"tasks": [task.model_dump(mode="json") for task in self.store.list_tasks(owner, limit=args.limit)],
                                   "questions": [question.model_dump(mode="json") for question in self.store.list_pending_answers(owner)]}}
            if name == "task_start":
                source = self._employer_turn(owner)
                request = OrganizationTaskRequest(kind=args.kind, instruction=args.instruction,
                    conversation_refs={"process_ids": [str(value) for value in args.process_ids],
                        "availability": [value.model_dump(mode="json") for value in args.availability],
                        **({"expires_at": args.expires_at.isoformat()} if args.expires_at else {})},
                    idempotency_key=idempotency_key)
                task = self.store.create_task(owner, request, source_turn_id=source.turn_id, source_lease_token=source.lease_token)
                return {"result": task.model_dump(mode="json"), "authorization_source_turn_id": str(source.turn_id)}
            if name == "task_status":
                return {"result": self.store.get_task(owner, args.task_id).model_dump(mode="json")}
            if name == "task_stop":
                self._employer_turn(owner)
                return {"result": self.store.stop_task(owner, args.task_id).model_dump(mode="json")}
            if name == "task_availability":
                self._employer_turn(owner)
                task = self.store.resume_task(owner, args.task_id, [value.model_dump(mode="json") for value in args.availability])
                return {"result": task.model_dump(mode="json")}
            if name == "memory_write":
                self._employer_turn(owner)
                return {"result": self.store.patch_memory(owner, args.expected_revision, args.value).model_dump(mode="json")}
            if self.turn is None or self.turn.task_id is None:
                raise OrganizationToolError("delegated_task_required", status=403)
            task = self.store.get_task(owner, self.turn.task_id)
            if task.lease_token != self.turn.task_lease_token or not self.store.task_lease_valid(task):
                raise OrganizationToolError("delegation_lease_lost", status=409)
            if args.outcome == "completed" and task.kind != "inquiry":
                raise OrganizationToolError("interview_completion_requires_candidate_acceptance", status=409)
            status = "succeeded" if args.outcome == "completed" else "waiting_employer"
            progress = dict(task.result or {})
            process_id = str(args.process_id)
            if process_id not in task.conversation_refs["process_ids"]:
                raise OrganizationToolError("delegation_process_denied", status=403)
            processes = dict(progress.get("processes", {}))
            state = dict(processes.get(process_id, {}))
            if state.get("turn_id") != str(self.turn.turn_id):
                raise OrganizationToolError("task_process_turn_mismatch", status=409)
            if args.outcome == "needs_employer":
                self.base.dispatch(owner_id, "candidate_chat_send", {
                    "process_id": process_id, "task_id": str(task.task_id),
                    "delegation_id": task.conversation_refs["delegation_id"], "lease_token": str(task.lease_token),
                    "text": "[AI-агент работодателя] Мне необходимо проконсультироваться с работодателем; вернёмся к вам после уточнения.",
                    "idempotency_key": idempotency_key,
                }, str(uuid5(NAMESPACE_URL, idempotency_key + ":consult")), str(task.task_id))
            state.update({"completed": args.outcome == "completed", "report": args.report})
            processes[process_id] = state
            progress["processes"] = processes
            if args.outcome == "completed" and not all(processes.get(value, {}).get("completed") for value in task.conversation_refs["process_ids"]):
                status = "processing"
            progress.update({"report": args.report, "outcome": args.outcome})
            self.store.update_task(owner, task.task_id, status=status, result=progress, lease_token=task.lease_token)
            self.store.append_message(owner, role="assistant", content=args.report,
                                      idempotency_key=f"task:{task.task_id}:report:{idempotency_key}",
                                      task_id=task.task_id, task_lease_token=task.lease_token)
            if status == "waiting_employer":
                self.store.ask_owner(self.store.get_task(owner, task.task_id), args.report[:2000],
                                     availability=task.kind == "schedule_interview")
            return {"result": {"status": status, "report": args.report}}
        args = dict(arguments)
        if name in {"candidate_chat_read", "candidate_chat_send", "interview_propose"}:
            if self.turn is None or self.turn.task_id is None:
                raise OrganizationToolError("explicit_delegation_required", status=403)
            task = self.store.get_task(owner, self.turn.task_id)
            if task.lease_token != self.turn.task_lease_token or not self.store.task_lease_valid(task):
                raise OrganizationToolError("delegation_lease_lost", status=409)
            if args.get("process_id") not in task.conversation_refs["process_ids"]:
                raise OrganizationToolError("delegation_process_denied", status=403)
            if name.startswith("candidate_chat"):
                args.update({"task_id": str(task.task_id), "delegation_id": task.conversation_refs["delegation_id"]})
                if name == "candidate_chat_send":
                    args["lease_token"] = str(task.lease_token)
                    text = str(args.get("text", ""))
                    args["text"] = "[AI-агент работодателя] " + text
            else:
                if task.kind != "schedule_interview":
                    raise OrganizationToolError("interview_delegation_required", status=403)
                self._check_availability(task, args)
                args.update({"task_id": str(task.task_id), "delegation_id": task.conversation_refs["delegation_id"],
                             "lease_token": str(task.lease_token)})
        # Each business effect receives a UUID retry key, independent of worker attempts.
        key = str(uuid5(NAMESPACE_URL, idempotency_key))
        return self.base.dispatch(owner_id, name, args, key, task_id)

    @staticmethod
    def _check_availability(task: OrganizationTask, args: dict) -> None:
        start = datetime.fromisoformat(str(args.get("starts_at", "")).replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise OrganizationToolError("interview_timezone_required", status=422)
        end = start + timedelta(minutes=int(args.get("duration_minutes", 0)))
        slots = [AvailabilitySlot.model_validate(value) for value in task.conversation_refs["availability"]]
        if not any(slot.starts_at <= start < end <= slot.ends_at and args.get("location") == slot.location
                   and args.get("format") == slot.format and args.get("kind") == slot.kind for slot in slots):
            raise OrganizationToolError("interview_outside_employer_availability", status=409)


class OrganizationWorkflows:
    """Wake tasks for real candidate events; waiting does not repeatedly call the LLM."""

    def __init__(self, store: OrganizationStore, runtime, registry: WorkflowToolRegistry, *, poll_seconds: int = 30):
        self.store, self.runtime, self.registry = store, runtime, registry
        self.poll_seconds = poll_seconds

    def _read(self, task: OrganizationTask, name: str, args: dict):
        key = str(uuid5(NAMESPACE_URL, f"task:{task.task_id}:{task.lease_token}:read:{name}:{args}"))
        return _unwrap(self.registry.base.dispatch(str(task.owner_id), name, args, key, str(task.task_id)))

    def process(self, task: OrganizationTask) -> None:
        """Advance one claimed task without replacing consent, source state or candidate acceptance."""
        if not self.store.task_lease_valid(task):
            return
        refs = task.conversation_refs
        if datetime.fromisoformat(str(refs["expires_at"])) <= datetime.now(UTC):
            self.store.update_task(task.owner_id, task.task_id, status="stopped", stop_reason="delegation_expired", lease_token=task.lease_token)
            return
        chat = self.store.get_chat(task.owner_id)
        if task.kind == "stand_in" and (chat is None or chat.last_seen_at is None
                or chat.presence and chat.last_seen_at >= datetime.now(UTC) - timedelta(seconds=120)):
            if chat and chat.last_seen_at and chat.presence and (task.result or {}).get("activated"):
                self.store.stop_task(task.owner_id, task.task_id, "employer_returned")
                return
            self._wait(task)
            return
        if task.kind == "schedule_interview" and not refs["availability"]:
            self.store.ask_owner(task, "Укажите свободные интервалы, часовой пояс, длительность и место/ссылку собеседования.", availability=True)
            self.store.update_task(task.owner_id, task.task_id, status="waiting_employer", lease_token=task.lease_token)
            return
        progress = dict(task.result or {})
        if task.kind == "stand_in" and not progress.get("activated"):
            progress["activated"] = True
            self.store.update_task(task.owner_id, task.task_id, status="processing", result=progress,
                                   lease_token=task.lease_token)
        processes = dict(progress.get("processes", {}))
        all_scheduled = True
        for process_id in refs["process_ids"]:
            state = dict(processes.get(process_id, {}))
            if state.get("completed"):
                continue
            process = self._read(task, "recruitment_get", {"process_id": process_id})
            if task.kind == "schedule_interview" and any(item.get("state") in {"scheduled", "completed"}
                    for item in process.get("interviews", []) if item.get("kind") in {slot["kind"] for slot in refs["availability"]}):
                processes[process_id] = {**processes.get(process_id, {}), "scheduled": True}
                continue
            all_scheduled = False
            # A completed model turn has already produced durable business receipts.
            pending_id = state.get("turn_id")
            if pending_id:
                previous = self.store.get_turn(task.owner_id, UUID(pending_id))
                if previous and previous.status == "failed":
                    self.store.ask_owner(task, f"Не удалось продолжить задачу: {previous.error_code}. Проверьте задачу {task.task_id}.")
                    self.store.update_task(task.owner_id, task.task_id, status="waiting_employer", lease_token=task.lease_token)
                    return
                if previous and previous.status in {"queued", "processing", "retrying"}:
                    turn = self.store.claim_turn_by_id(task.owner_id, previous.turn_id)
                    turn = self.store.bind_task_turn(turn, task)
                    self.runtime.run_turn(turn)
                    current = self.store.get_task(task.owner_id, task.task_id)
                    if current.status != "processing":
                        return
                    progress = dict(current.result or progress)
                    processes = dict(progress.get("processes", processes))
                    continue
            history = self._read(task, "candidate_chat_read", {"process_id": process_id,
                "task_id": str(task.task_id), "delegation_id": refs["delegation_id"],
                "after_sequence": int(state.get("cursor", 0)), "limit": 100})
            messages = history.get("items", history.get("messages", []))
            # Advance only through the processed page; later pages remain for the next wake-up.
            candidate_messages = [item for item in messages if item.get("sender_id") == process.get("candidate_id")]
            cursor = max([int(item.get("sequence", 0)) for item in messages] + [int(state.get("cursor", 0))])
            first = not state.get("started")
            if not first and not candidate_messages and not (pending_id and previous and previous.status in {"queued", "processing", "retrying"}):
                continue
            if task.kind == "stand_in" and not candidate_messages:
                continue
            event_key = f"workflow:{task.task_id}:{progress.get('generation',0)}:{process_id}:{cursor}"
            payload = {"task_id": str(task.task_id), "kind": task.kind, "employer_instruction": task.instruction,
                       "process_id": process_id, "availability": refs["availability"],
                       "candidate_data_untrusted": {"process": process, "messages": candidate_messages},
                       "rules": "Communicate using candidate_chat_send. Do not invent availability. "
                                "Only propose an interview after agreeing a supplied slot; the candidate must accept. "
                                "If no slot works, tell the candidate you will consult the employer, then task_complete needs_employer. "
                                "For inquiry, task_complete completed with this process_id only once you have sufficient answers. "
                                "For stand_in answer the new message and leave the delegation running."}
            instruction = json.dumps(payload, ensure_ascii=False)
            if len(instruction) > 8000:
                # Raw evidence stays in the candidate chat; retrieve later pages via the tool.
                payload["candidate_data_untrusted"] = {"process_id": process_id, "after_sequence": state.get("cursor", 0)}
                instruction = json.dumps(payload, ensure_ascii=False)
            try:
                turn = self.store.create_turn(task.owner_id, instruction, event_key, task_id=task.task_id, task_lease_token=task.lease_token)
            except OrganizationConflict:
                self._wait(task, progress)
                return
            processes[process_id] = {**state, "cursor": cursor, "started": True, "turn_id": str(turn.turn_id)}
            progress["processes"] = processes
            self.store.update_task(task.owner_id, task.task_id, status="processing", result=progress,
                                   lease_token=task.lease_token)
            if turn.status != "succeeded":
                turn = self.store.claim_turn_by_id(task.owner_id, turn.turn_id)
                turn = self.store.bind_task_turn(turn, task)
                self.runtime.run_turn(turn)
            current = self.store.get_task(task.owner_id, task.task_id)
            if current.status != "processing":
                return
            progress = dict(current.result or progress)
            processes = dict(progress.get("processes", processes))
        if task.kind == "schedule_interview" and all_scheduled:
            report = "Собеседования подтверждены кандидатами. Всего: " + str(len(refs["process_ids"]))
            self.store.append_message(task.owner_id, role="assistant", content=report,
                                      idempotency_key=f"task:{task.task_id}:scheduled",
                                      task_id=task.task_id, task_lease_token=task.lease_token)
            self.store.update_task(task.owner_id, task.task_id, status="succeeded", result={**progress, "report": report}, lease_token=task.lease_token)
        else:
            self._wait(task, progress)

    def _wait(self, task: OrganizationTask, progress: dict | None = None) -> None:
        self.store.update_task(task.owner_id, task.task_id, status="waiting", result=progress,
                               delay_seconds=self.poll_seconds, lease_token=task.lease_token)
