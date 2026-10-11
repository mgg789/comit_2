"""Opt-in real-provider acceptance on synthetic owners in an isolated test database."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from agents_service.database import connect, migrate
from agents_service.organization_documents import DocumentStore
from agents_service.organization_runtime import OrganizationRuntime
from agents_service.organization_store import OrganizationStore
from agents_service.organization_tools import ToolRegistry
from agents_service.organization_workflows import (
    OrganizationWorkflows,
    WorkflowToolRegistry,
)


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["interface_help", "markdown_report", "background_clarification"])
def test_live_employer_agent(scenario):
    """Measure real tool-grounded help, durable file creation and a background owner question.

    Explicit opt-in permits paid calls. No API bridge is configured, so candidate
    messages and production business effects are impossible in these scenarios.
    Evidence contains only synthetic prompts, answers and aggregate usage.
    """
    if os.getenv("AGENT_LIVE_ACCEPTANCE") != "1":
        pytest.skip("AGENT_LIVE_ACCEPTANCE=1 is required for paid provider acceptance")
    url = os.environ["AGENTS_TEST_DATABASE_URL"]
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Live acceptance requires a local database whose name ends with _test")
    if not os.getenv("DEEPSEEK_API_KEY"):
        pytest.fail("A configured DeepSeek key is required; no simulated provider is used")
    migrate(url)
    owner, process = uuid4(), uuid4()
    store = OrganizationStore(url)
    base = ToolRegistry(url, api_url="", internal_token="")
    # Disable environment fallback explicitly: tests never connect to the API bridge.
    base.api_url, base.internal_token = "", ""
    registry = WorkflowToolRegistry(base, store)
    runtime = OrganizationRuntime(store, registry=registry, max_rounds=8, max_output_tokens=2048)
    prompts = {
        "interface_help": "Как в кабинете работодателя создать вакансию и когда она станет доступна кандидатам? Проверь справку интерфейса и объясни коротко. Ничего не создавай.",
        "markdown_report": "Создай в моём приватном хранилище файл interview-checklist.md: короткий Markdown-чек-лист для меня как работодателя и интервьюера, чтобы подготовить техническое собеседование Python-разработчика: вопросы, проверка навыков и критерии оценки. Сообщи, что сохранено. Вакансии не создавай и кандидатам не пиши.",
        "background_clarification": f"Поручаю тебе согласовать собеседование с кандидатом по процессу {process}. Сохрани фоновое поручение. Свободные интервалы я пока не указал; сначала вернись ко мне за временем, длительностью и местом. Кандидату пока не пиши.",
    }
    started = time.perf_counter()
    checks = {}
    result = None
    try:
        turn = store.create_turn(owner, prompts[scenario], str(uuid4()))
        result = runtime.run_turn(store.claim_turn_by_id(owner, turn.turn_id))
        checks["turn_succeeded"] = result.status == "succeeded"
        messages = store.list_messages(owner, limit=200).items
        tool_messages = [message for message in messages if message.role == "tool"]
        executed = []
        trace = []
        for message in tool_messages:
            with connect(url) as db:
                journal = db.execute("SELECT arguments FROM agents_service.organization_tool_journal WHERE turn_id=%s AND call_id=%s",
                                     (turn.turn_id, message.tool_call_id)).fetchone()
            if journal:
                executed.append(journal["arguments"].get("name", message.name))
                payload = json.loads(message.content)
                trace.append({"name": executed[-1], "arguments": journal["arguments"],
                              "error_code": payload.get("error_code"),
                              "result_status": payload.get("result", {}).get("status") if isinstance(payload.get("result"), dict) else None})
        if scenario == "interface_help":
            checks["read_real_help"] = "knowledge_page_read" in executed
        elif scenario == "markdown_report":
            documents = DocumentStore(url).list(owner)
            checks["file_persisted"] = any(document.name == "interview-checklist.md" and document.text_length > 50 for document in documents)
        else:
            tasks = store.list_tasks(owner)
            checks["delegation_persisted"] = len(tasks) == 1 and tasks[0].kind == "schedule_interview"
            if checks["delegation_persisted"]:
                task = store.claim_task(owner_id=owner)
                OrganizationWorkflows(store, runtime, registry).process(task)
                checks["awaiting_employer"] = store.get_task(owner, task.task_id).status == "waiting_employer"
                checks["question_persisted"] = len(store.list_pending_answers(owner)) == 1
                # Waiting for an owner has no runnable task and spends no extra model tokens.
                checks["idle_has_no_work"] = store.claim_task(owner_id=owner) is None
        answers = [message.content for message in messages if message.role == "assistant" and not message.tool_calls]
        with connect(url) as db:
            cost = db.execute("SELECT COALESCE(sum(total_micro_usd),0) AS value FROM agents_service.organization_usage_ledger WHERE owner_id=%s", (owner,)).fetchone()["value"]
            unresolved = db.execute("SELECT COALESCE(sum(upper_bound_micro_usd),0) AS value, count(*) AS calls FROM agents_service.organization_usage_reservations WHERE owner_id=%s AND status='reserved'", (owner,)).fetchone()
        evidence = {"scenario": scenario, "recorded_at": datetime.now(UTC).isoformat(),
                    "model": runtime.provider.model, "reasoning_effort": result.reasoning_effort,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "status": result.status, "error_code": result.error_code,
                    "rounds": result.rounds, "tool_calls": result.tool_calls,
                    "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                    "ledger_usd": str(cost / 1_000_000), "tools": executed, "tool_trace": trace, "checks": checks,
                    "unresolved_reservation_usd": str(unresolved["value"] / 1_000_000),
                    "unresolved_calls": unresolved["calls"],
                    "answer": answers[-1] if answers else "",
                    "scope": "Real DeepSeek + current runtime/registry + isolated PostgreSQL; synthetic owner/process; no API bridge or candidate messages."}
        output = os.getenv("AGENT_ACCEPTANCE_OUTPUT")
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{scenario}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert all(checks.values()), evidence
    finally:
        with connect(url) as db:
            for table in ("organization_documents", "organization_tool_receipts", "organization_provider_calls"):
                db.execute(f"DELETE FROM agents_service.{table} WHERE owner_id=%s", (owner,))
            db.execute("DELETE FROM agents_service.organization_chats WHERE owner_id=%s", (owner,))
