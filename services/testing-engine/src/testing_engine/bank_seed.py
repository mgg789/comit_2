"""Seed item bank for the MVP skeleton: 7 families x 4 levels, hand-written.

The LLM generator with daily quotas lands in the next pass; until then this
static bank keeps the adaptive runner, storage and API fully exercisable.
Payload shapes:
- mcq: {prompt, options[4], answer_index}
- code: {prompt, function_name, tests: [{args: [...], expected}]}  (python)
- extended: {prompt, rubric: [key points]}  (judging lands in a later pass)
"""

from __future__ import annotations

import json
from pathlib import Path

FAMILIES: tuple[dict[str, str], ...] = (
    {"family": "algorithms", "group": "domain"},
    {"family": "sql", "group": "domain"},
    {"family": "rest_api", "group": "domain"},
    {"family": "python", "group": "stack"},
    {"family": "react", "group": "stack"},
    {"family": "docker", "group": "stack"},
    {"family": "teamwork", "group": "soft_skill"},
)


def _mcq(family: str, level: int, prompt: str, options: list[str], answer: int) -> dict:
    return {"family": family, "level": level, "type": "mcq",
            "prompt": prompt, "options": options, "answer_index": answer}


def _code(family: str, level: int, prompt: str, name: str,
          tests: list[dict]) -> dict:
    return {"family": family, "level": level, "type": "code",
            "prompt": prompt, "function_name": name, "tests": tests}


def _ext(family: str, level: int, prompt: str, rubric: list[str]) -> dict:
    return {"family": family, "level": level, "type": "extended",
            "prompt": prompt, "rubric": rubric}


def initial_items() -> list[dict]:
    """28 items: every family at every level, honest minimal content."""
    items: list[dict] = []
    a = "algorithms"
    items += [
        _mcq(a, 1, "Что такое сложность линейного поиска по массиву из n элементов?",
             ["O(1)", "O(log n)", "O(n)", "O(n^2)"], 2),
        _mcq(a, 2, "Какая структура даёт поиск за O(1) в среднем?",
             ["Связный список", "Хеш-таблица", "Куча", "Двоичное дерево поиска"], 1),
        _mcq(a, 3, "Сложность быстрой сортировки в среднем случае?",
             ["O(n)", "O(n log n)", "O(n^2)", "O(log n)"], 1),
        _mcq(a, 4, "Что верно для хеш-таблицы при плохой хеш-функции?",
             ["Всегда O(1)", "Операции вырождаются в O(n)", "Память O(1)",
              "Поиск становится O(log n)"], 1),
        _code(a, 2, "Реализуйте функцию two_sum(nums, target), возвращающую индексы пары.",
              "two_sum",
              [{"args": [[1, 2, 3], 5], "expected": [1, 2]},
               {"args": [[2, 7, 11], 9], "expected": [0, 1]}]),
        _code(a, 3, "Реализуйте is_palindrome(s): True, если строка - палиндром (без регистра и пробелов).",
              "is_palindrome",
              [{"args": ["А роза упала на лапу Азора"], "expected": True},
               {"args": ["abc"], "expected": False}]),
    ]
    q = "sql"
    items += [
        _mcq(q, 1, "Какая команда выбирает данные из таблицы?",
             ["INSERT", "SELECT", "UPDATE", "DELETE"], 1),
        _mcq(q, 2, "Что делает LEFT JOIN?",
             ["Только совпадения", "Все строки левой таблицы + совпадения",
              "Все строки правой таблицы", "Декартово произведение"], 1),
        _mcq(q, 3, "Чем HAVING отличается от WHERE?",
             ["Ничем", "HAVING фильтрует после группировки",
              "WHERE фильтрует после группировки", "HAVING только для INSERT"], 1),
        _mcq(q, 4, "Что такое индекс в СУБД?",
             ["Копия таблицы", "Структура для ускорения поиска",
              "Ограничение внешнего ключа", "Тип JOIN"], 1),
        _mcq(q, 3, "Какой индекс быстрее для префиксного поиска 'abc%'?",
             ["B-tree", "Полный перебор", "Хеш-индекс", "Индекс не влияет"], 0),
        _ext(q, 2, "Напишите SQL-запрос: имена пользователей с более чем 5 заказами.",
             ["SELECT имени", "GROUP BY пользователя", "HAVING COUNT(*) > 5", "корректный JOIN с заказами"]),
    ]
    r = "rest_api"
    items += [
        _mcq(r, 1, "Какой HTTP-метод создаёт ресурс?",
             ["GET", "POST", "PUT", "DELETE"], 1),
        _mcq(r, 2, "Какой статус кода означает успешное создание?",
             ["200", "201", "204", "404"], 1),
        _mcq(r, 3, "Зачем нужен идемпотентный ключ в POST?",
             ["Ускорение", "Защита от повторного создания при ретрае",
              "Аутентификация", "Пагинация"], 1),
        _mcq(r, 4, "Что верно для 429 Too Many Requests?",
             ["Клиент должен повторить позже (Retry-After)",
              "Ошибка сервера навсегда", "Ресурс не найден", "Требуется оплата"], 0),
        _ext(r, 2, "Спроектируйте эндпоинт создания приглашения: метод, тело, коды ответов.",
             ["POST + путь", "валидация тела (зарплата обязательна)", "201 при создании",
              "409 при дубликате", "идемпотентный ключ"]),
    ]
    p = "python"
    items += [
        _mcq(p, 1, "Какой тип данных неизменяемый?",
             ["list", "dict", "tuple", "set"], 2),
        _mcq(p, 2, "Что выведет len({1, 1, 2})?",
             ["3", "2", "1", "Ошибку"], 1),
        _mcq(p, 3, "Зачем нужен __init__.py в пакете?",
             ["Для скорости", "Помечает пакет и задаёт публичный интерфейс",
              "Обязателен для import в Python 3", "Содержит тесты"], 1),
        _mcq(p, 4, "Что вернёт [i*i for i in range(3) if i][-1]?",
             ["4", "1", "0", "Ошибку"], 0),
        _code(p, 2, "Реализуйте flatten(nested): плоский список из списка списков.",
              "flatten",
              [{"args": [[1, 2], [3]], "expected": [1, 2, 3]},
               {"args": [[]], "expected": []}]),
        _code(p, 3, "Реализуйте word_counts(text): словарь частот слов в нижнем регистре.",
              "word_counts",
              [{"args": ["а б а"], "expected": {"а": 2, "б": 1}},
               {"args": [""], "expected": {}}]),
    ]
    f = "react"
    items += [
        _mcq(f, 1, "Что такое JSX?",
             ["CSS-фреймворк", "Синтаксис расширения JS для описания UI",
              "Тип состояния", "Роутер"], 1),
        _mcq(f, 2, "Зачем key в списках React?",
             ["Стилизация", "Идентификация элементов между рендерами",
              "Сортировка", "Валидация"], 1),
        _mcq(f, 3, "Что делает useEffect с пустым массивом зависимостей?",
             ["Запускается при каждом рендере", "Один раз после монтирования",
              "Никогда", "Только при ошибке"], 1),
        _mcq(f, 4, "Чем опасен мутабельный state напрямую (this.state.x = 1)?",
             ["Ничем", "React не увидит изменение и не перерендерит",
              "Ускоряет рендер", "Ломает типы"], 1),
        _ext(f, 2, "Опишите, как показать список задач с фильтром по статусу на React.",
             ["state со списком задач", "компонент списка с key", "фильтр как derived state", "обработка пустого списка"]),
    ]
    d = "docker"
    items += [
        _mcq(d, 1, "Что такое образ (image) в Docker?",
             ["Запущенный процесс", "Неизменяемый шаблон для контейнеров",
              "Том для данных", "Сеть"], 1),
        _mcq(d, 2, "Какая инструкция Dockerfile копирует файлы?",
             ["RUN cp", "COPY", "ADD_URL", "FROM"], 1),
        _mcq(d, 3, "Зачем multi-stage build?",
             ["Красивые логи", "Меньший финальный образ без инструментов сборки",
              "Больше слоёв", "Автотесты"], 1),
        _mcq(d, 4, "Что произойдёт с данными в контейнере после его удаления без volume?",
             ["Останутся в образе", "Будут потеряны", "Перейдут в другой контейнер", "Сожмутся"], 1),
    ]
    t = "teamwork"
    items += [
        _mcq(t, 1, "Коллега не успел к дедлайну. Первый шаг?",
             ["Сообщить руководителю факт и план", "Молча переделать за него",
              "Игнорировать", "Уволить"], 0),
        _mcq(t, 2, "Что лучше при конфликте по архитектуре?",
             ["Обсудить критерии и сравнить варианты", "Настоять на своём",
              "Сдаться без обсуждения", "Эскалация сразу"], 0),
        _mcq(t, 3, "Как правильно сообщить о риске срыва срока?",
             ["Как можно позже", "Заранее: факт, влияние, варианты",
              "Только после срыва", "Через третьих лиц"], 1),
        _mcq(t, 4, "Вам делегировали задачу без контекста. Что сделаете?",
             ["Сделаю как понял", "Уточню цель, критерии и ограничения",
              "Откажусь", "Ждать подробностей неделями"], 1),
        _ext(t, 2, "Опишите, как вы организуете передачу задачи между разработчиками.",
             ["письменный контекст", "критерии приёмки", "демо/передача знаний", "ответственный на вопрос"]),
    ]
    # Exact short answers complement choice questions without executing user code.
    for family, prompt, expected in (
        ("algorithms", "Сколько сравнений в худшем случае нужно линейному поиску в массиве из 8 элементов? Введите число.", ["8"]),
        ("sql", "Введите SQL-команду, которая выбирает строки из таблицы (одно слово).", ["select"]),
        ("rest_api", "Какой HTTP-код означает, что ресурс не найден? Введите число.", ["404"]),
        ("python", "Чему равно len([1, 2, 3]) в Python? Введите число.", ["3"]),
        ("react", "Введите имя хука React для локального состояния компонента.", ["usestate"]),
        ("docker", "Введите инструкцию Dockerfile, задающую базовый образ (одно слово).", ["from"]),
        ("teamwork", "Как называется регулярная встреча Scrum для обсуждения того, что улучшить в процессе команды?", ["ретроспектива", "retrospective"]),
    ):
        items.append({"family": family, "level": 2, "type": "short_answer", "prompt": prompt, "expected_answers": expected})
    return items


def load_or_seed(path: Path) -> list[dict]:
    """Read the bank JSON if present, else write and return the initial bank."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    items = initial_items()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return items
