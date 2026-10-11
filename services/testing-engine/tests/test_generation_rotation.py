"""Offline tests of the generation ledger, validator and rotation mechanics."""

import json
from pathlib import Path

from testing_engine.generator import (
    GenerationLedger,
    validate_item,
)
from testing_engine.store import TestingStore


def _item(family: str = "algorithms", level: int = 2, prompt: str = "Какая структура даёт O(1) поиска?",
          **extra) -> dict:
    item = {"family": family, "level": level, "type": "mcq", "prompt": prompt,
            "options": ["список", "хеш-таблица", "куча", "граф"], "answer_index": 1}
    item.update(extra)
    return item


def test_ledger_enforces_daily_cap_and_keeps_history(tmp_path: Path) -> None:
    """Reserves succeed up to the cap, then refuse; counters never reset."""
    ledger = GenerationLedger(tmp_path / "ledger.json", daily_limit=2)
    assert ledger.reserve() is True
    assert ledger.reserve() is True
    assert ledger.reserve() is False
    assert ledger.used_today() == 2
    saved = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert saved[next(iter(saved))]["generation"] == 2


def test_validator_rejects_duplicate_and_malformed(tmp_path: Path) -> None:
    """Duplicate prompts and broken MCQ shapes are rejected with reasons."""
    existing = {"какая структура даёт o(1) поиска?"}
    problems = validate_item(_item(), existing)
    assert "duplicate_prompt" in problems
    broken = _item(prompt="Совершенно другой вопрос про очереди",
                   options=["a", "a", "b", "c"], answer_index=7)
    problems = validate_item(broken, existing)
    assert "mcq_needs_4_unique_options" in problems
    assert "mcq_answer_index_out_of_range" in problems
    assert validate_item(_item(prompt="Совершенно другой вопрос про очереди"),
                         existing) == []


def test_generated_items_rotate_out_per_moratorium(tmp_path: Path) -> None:
    """Generated items retire by exposure; seed items stay; served-today rotate."""
    store = TestingStore(tmp_path / "data")
    from testing_engine.bank_seed import initial_items

    store.seed_items(initial_items())
    generated = _item(prompt="Совершенно другой вопрос про очереди и стеки")
    item_id = store.add_generated_item(generated)
    assert item_id is not None
    store.mark_served(item_id)
    for _ in range(3):
        store.bump_exposure(item_id)
    assert store.retire_expired(max_exposure=3) == 1
    bank = json.loads((tmp_path / "data" / "items.json").read_text(encoding="utf-8"))
    retired = next(row for row in bank if row["item_id"] == item_id)
    assert retired["retired"] is True


def test_refill_stops_on_budget_and_dedupes(tmp_path: Path) -> None:
    """Refill honors the daily budget, dedupes prompts and rejects bad items."""
    store = TestingStore(tmp_path / "data")
    from testing_engine.bank_seed import initial_items

    store.seed_items(initial_items())
    calls = []

    def fake_generator(family: str, level: int, task_type: str) -> dict:
        calls.append((family, level, task_type))
        counter = len(calls)
        if task_type == "code":
            name = "solve_" + family
            reference = "def " + name + "(*args):\n    return args[0]"
            return {**_item(family, level, prompt=f"Кодовая задача {family} уровень {level}: решите функцией {name}."),
                    "function_name": name,
                    "tests": [{"args": [1], "expected": 1}],
                    "reference": reference}
        if counter == 1:
            first = _item(family, level, prompt=f"Уникальный вопрос про очереди номер {counter}")
            fake_generator.first_prompt = first["prompt"]  # type: ignore[attr-defined]
            return first
        if counter % 3 == 1:
            return _item(family, level, prompt=fake_generator.first_prompt)  # type: ignore[attr-defined]
        return _item(family, level, prompt=f"Уникальный вопрос номер {counter} про очереди")

    from testing_engine.generator import refill

    report = refill(store, fake_generator, data_dir=tmp_path, min_stock=2, daily_limit=6)
    assert report["generated"] >= 1
    assert any("duplicate_prompt" in row["problems"] for row in report["rejected"])
    assert report["budget_used"] <= 6
    assert report["budget_used"] == len(calls)


def test_store_counts_active_excluding_served_today(tmp_path: Path) -> None:
    """count_active hides items served today but keeps them in the bank."""
    store = TestingStore(tmp_path / "data")
    item_id = store.add_generated_item(_item(family="sql", level=2))
    assert store.count_active("sql", 2) == 1
    store.mark_served(item_id)
    assert store.count_active("sql", 2) == 0
    assert all(row["item_id"] != item_id for row in store.list_items("sql", 2)), "burned instance must not return"
