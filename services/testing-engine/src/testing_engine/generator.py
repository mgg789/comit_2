"""LLM item generation with a daily ledger limit, validation and rotation.

Per docs/testing-design.md §3: generation runs offline-batch into the bank
(runtime sessions never call the LLM), each produced item passes the quality
validator, the daily budget is a cumulative day-keyed ledger (rerank pattern),
and served instances rotate out via the store moratorium.

The provider is DeepSeek (same key/infra as the extended-answer judge). Every
failure degrades to "generation unavailable" - the seed bank keeps the contour
fully functional without any network access.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from testing_engine.judge import LocalPythonJudge

MIN_STOCK_PER_SLOT = 5
FAMILY_BLUEPRINTS = {
    "algorithms": ("массивы/хеш-таблицы/строки", "python-функция"),
    "sql": ("запросы, JOIN, группировки, индексы", "вопрос или запрос"),
    "rest_api": ("методы, статусы, идемпотентность, версии", "вопрос"),
    "python": ("типы, collections, генераторы, исключения", "python-функция"),
    "react": ("компоненты, state, hooks, списки", "вопрос"),
    "docker": ("образы, слои, volumes, Dockerfile", "вопрос"),
    "teamwork": ("коммуникация, конфликты, дедлайны", "сценарный вопрос"),
}


def today_key() -> str:
    return datetime.now(UTC).date().isoformat()


class GenerationLedger:
    """Cumulative day-keyed usage counters; the cap never resets history."""

    def __init__(self, path: str | Path, daily_limit: int) -> None:
        self.path = Path(path)
        self.daily_limit = daily_limit

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def used_today(self, kind: str = "generation") -> int:
        return int(self._load().get(today_key(), {}).get(kind, 0))

    def reserve(self, kind: str = "generation") -> bool:
        """Reserve one attempt; False when the daily cap is exhausted."""
        ledger = self._load()
        day = ledger.setdefault(today_key(), {})
        used = int(day.get(kind, 0))
        if used >= self.daily_limit:
            return False
        day[kind] = used + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(ledger, indent=1) + "\n", encoding="utf-8")
        return True


class GenerationUnavailable(RuntimeError):
    """Raised when the provider cannot produce an item (network/key/schema)."""


class DeepSeekGenerator:
    """Minimal DeepSeek chat-completions client bound to one item request."""

    def __init__(self, *, api_key: str | None = None, model: str = "deepseek-chat") -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model

    def available(self) -> bool:
        return bool(self.api_key)

    def generate_item(self, family: str, level: int, task_type: str) -> dict:
        """Ask for one item and return the parsed payload; raise on any failure."""
        if not self.available():
            raise GenerationUnavailable("DEEPSEEK_API_KEY is not set")
        topic, artifact = FAMILY_BLUEPRINTS[family]
        grade_word = ("intern", "junior", "middle", "senior")[level - 1]
        type_rules = {
            "mcq": 'Ровно 4 опции, ровно один защитимо верный, правдоподобные дистракторы. Верни "options" (4 строки) и "answer_index" (0..3).',
            "code": f'Задача на python-функцию "{artifact}". Верни "function_name", "prompt" с сигнатурой, "tests" (2-3 кейса: {{"args": [...], "expected": ...}}) и "reference" - эталонное решение, проходящее tests.',
            "extended": 'Верни "prompt" и "rubric" (3-5 проверяемых ключевых пунктов).',
        }[task_type]
        system = (
            f"Сгенерируй ОДНО тестовое задание уровня {grade_word} для направления {family} "
            f"(тема: {topic}). Ответ - только валидный JSON без markdown. {type_rules} "
            "Не повторяй дословно пример; конкретные числа, названия и сценарии придумай сам."
        )
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps({
                "model": self.model, "temperature": 0.8,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": "Сгенерируй задание."}],
                "response_format": {"type": "json_object"},
            }).encode("utf-8"),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"}, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        item = json.loads(content)
        item.update({"family": family, "level": level, "type": task_type})
        return item


def validate_item(item: dict, existing_prompts: set[str]) -> list[str]:
    """Structural + content validation; returns a list of problems (empty = valid)."""
    problems: list[str] = []
    prompt = " ".join(str(item.get("prompt", "")).lower().split())
    if len(prompt) < 15:
        problems.append("prompt_too_short")
    normalized = " ".join(str(item.get("prompt", "")).lower().split())
    if normalized in existing_prompts:
        problems.append("duplicate_prompt")
    task_type = item.get("type")
    if task_type == "mcq":
        options = item.get("options")
        if not isinstance(options, list) or len(options) != 4 or len(set(options)) != 4:
            problems.append("mcq_needs_4_unique_options")
        index = item.get("answer_index")
        if not isinstance(index, int) or not 0 <= index <= 3:
            problems.append("mcq_answer_index_out_of_range")
    elif task_type == "code":
        if not item.get("function_name") or not item.get("tests"):
            problems.append("code_needs_function_and_tests")
        reference = item.get("reference")
        if not isinstance(reference, str) or not reference:
            problems.append("code_needs_reference_solution")
        else:
            correct, verdict = LocalPythonJudge().judge(item, reference)
            if correct is not True:
                problems.append(f"reference_solution_failed:{verdict}")
    elif task_type == "extended":
        rubric = item.get("rubric")
        if not isinstance(rubric, list) or len(rubric) < 3:
            problems.append("extended_needs_3_rubric_points")
    else:
        problems.append("unknown_task_type")
    return problems


def refill(store, generator, *, data_dir: Path, min_stock: int = MIN_STOCK_PER_SLOT,
           daily_limit: int = 50) -> dict:
    """Retire expired instances, then generate until stock or budget runs out.

    `generator` is any callable (family, level, task_type) -> item payload -
    inject a fake in tests; the DeepSeekGenerator is the production wiring.
    """
    from testing_engine.bank_seed import FAMILIES

    ledger = GenerationLedger(Path(data_dir) / "ledger.json", daily_limit)
    retired = store.retire_expired()
    report = {"retired": retired, "generated": 0, "rejected": [],
              "budget_used": 0, "budget_limit": daily_limit, "slots": []}
    for family_row in FAMILIES:
        family = family_row["family"]
        for level in (1, 2, 3, 4):
            for task_type in ("mcq", "code", "extended"):
                active = store.count_active(family, level)
                stock = max(0, min_stock - active)
                for _ in range(stock):
                    if ledger.used_today() >= daily_limit:
                        report["slots"].append({"slot": f"{family}-L{level}-{task_type}",
                                                "action": "budget_exhausted"})
                        return report
                    if not ledger.reserve():
                        report["slots"].append({"slot": f"{family}-L{level}-{task_type}",
                                                "action": "budget_exhausted"})
                        return report
                    report["budget_used"] += 1
                    try:
                        item = generator(family, level, task_type)
                    except GenerationUnavailable as error:
                        report["slots"].append({"slot": f"{family}-L{level}-{task_type}",
                                                "action": f"unavailable:{error}"})
                        return report
                    existing = {" ".join(str(row["payload"].get("prompt", "")).lower().split())
                                for row in _bank_rows(store)}
                    problems = validate_item(item, existing)
                    if problems:
                        report["rejected"].append({"family": family, "level": level,
                                                   "type": task_type, "problems": problems})
                        continue
                    item_id = store.add_generated_item(item)
                    report["generated"] += int(item_id is not None)
    return report


def _bank_rows(store):
    path = Path(store.data_dir) / "items.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
