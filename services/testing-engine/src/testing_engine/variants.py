"""Offline task blueprints with changing inputs and computed answer keys.

Templates exercise reasoning rather than merely decorating a static prompt.
Difficulty labels are design targets, not empirically calibrated IRT values.
"""
from __future__ import annotations

import random
import secrets

FAMILIES = ("algorithms", "python", "sql", "docker", "react", "rest_api", "teamwork")


def instantiate(family: str, level: int, *, seed: int | None = None) -> dict:
    """Create a reproducible variant; production seeds use system entropy."""
    rng = random.Random(secrets.randbits(128) if seed is None else seed)
    import os
    from pathlib import Path

    import yaml
    directory = Path(os.getenv("BLUEPRINT_DIR", Path(__file__).resolve().parents[2] / "blueprints"))
    blueprints = yaml.safe_load((directory / "reasoning.yaml").read_text(encoding="utf-8"))
    blueprint = next((row for row in blueprints if row["specialization"] == family), None)
    if blueprint is None:
        raise ValueError("unsupported_blueprint_family")
    a = rng.randint(*blueprint["params"]["a"]["range"])
    b = rng.randint(*blueprint["params"]["b"]["range"])
    prompt = blueprint["statement_ru"].format(a=a, b=b, stop=a * b + b)
    result = {"a": a, "multiply": a * b, "add": a + b}[blueprint["answer_operation"]]
    # Level 3/4 add a second operation; the answer changes with both parameters.
    if level >= 3:
        prompt += f" Затем от числового результата вычтите {b}. Введите итоговое число."
        result -= b
    else:
        prompt += " Введите число."
    return {"family": family, "level": level, "type": "short_answer",
            "blueprint_id": family + "-reasoning-v1", "variant_seed": str(seed) if seed is not None else None,
            "prompt": prompt, "expected_answers": [str(result)]}


def instantiate_checked(family: str, level: int, task_type: str) -> dict | None:
    """Replenish required code/text slots with concrete, checked input variants."""
    from testing_engine.bank_seed import initial_items
    source = next((item for item in initial_items() if item["family"] == family
                   and item["level"] == level and item["type"] == task_type), None)
    if source is None:
        return None
    rng = random.SystemRandom()
    marker = rng.randint(1000, 999999999)
    if task_type == "extended":
        return {**source, "prompt": source["prompt"] + f" Контекст: команда поддерживает {marker} записей; объясните влияние масштаба на решение.",
                "blueprint_id": family + "-scenario-v1"}
    name = source["function_name"]
    if name == "two_sum":
        tests = [{"args": [[marker, 2, 7], marker + 7], "expected": [0, 2]},
                 {"args": [[3, marker, 8], marker + 3], "expected": [0, 1]}]
    elif name == "flatten":
        tests = [{"args": [[[marker], [], [2, 7]]], "expected": [marker, 2, 7]},
                 {"args": [[[], [marker, marker]]], "expected": [marker, marker]}]
    elif name == "word_counts":
        word = "word" + str(marker)
        tests = [{"args": [word + " " + word.upper() + " test"], "expected": {word: 2, "test": 1}},
                 {"args": [""], "expected": {}}]
    elif name == "is_palindrome":
        word = str(marker)
        tests = [{"args": [word + word[::-1]], "expected": True},
                 {"args": [word + "x"], "expected": False}]
    else:
        return None
    # Retain original edge cases, not just a changed numeric happy path.
    return {**source, "tests": source["tests"] + tests,
            "prompt": source["prompt"] + f" Обработайте входные значения, включая {marker}, без привязки к примерам.",
            "blueprint_id": name + "-inputs-v1"}
