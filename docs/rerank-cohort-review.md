# Слепая проверка расширенной выборки R₀

Модуль `matching_engine.rerank_cohort_review` готовит два отдельных пакета
по 20 синтетических пар: для ассистента и пользователя. В каждом представлены
восемь направлений и четыре split. Пары между пакетами не пересекаются;
выбор определяется исходными семействами и индексами, без меток учителя.
Полные исходные профили, вакансии и policy сохраняются без переинтерпретации.
Признаки, main scores и прогнозы модели в пакет не попадают.

Подготовка требует все 60 принятых источников и проверяет hashes manifest/source.
Dataset и признаки при выборе пар не читаются. Разметка остальных данных может
продолжаться независимо; подготовка пакета не является блокировкой расширения.
JSON и русскоязычный Markdown записываются неизменяемо в
`tests/artifacts/reranker/rerank-grounded-cohort-v9/reviews/`.

Из корня репозитория:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_review prepare --directory tests/artifacts/reranker --cohort-id rerank-grounded-cohort-v9
```

Файлы: `assistant-packet.json`, `assistant-packet.md`, `user-packet.json`,
`user-packet.md`. Каждый JSON содержит `packet_hash`, исходные hashes и 20 cases.
В ответе нужно скопировать поле `packet_hash` из JSON: это канонический seal
содержимого без самого hash-поля, а не SHA-256 байтов Markdown/JSON-файла.
`pair_id`, `family_id`, `a_index`, `b_index` связывают ответ с точными исходными
профилями. Description доступно как исходный текст; оно не устанавливает
длительность, эксплуатацию, роль или другие типизированные факты. Уровни 0–7
и статусы навыков независимы; null означает неизвестное.

Ответ сохраняют отдельным JSON с точным хешем своего пакета и **всеми 20**
идентификаторами. Пример структуры одной строки, не готовая разметка:

```json
{
  "packet_hash": "<хеш своего пакета>",
  "reviewer_kind": "user",
  "claimed_human_review": true,
  "labels": [
    {
      "pair_id": "<точный pair_id>",
      "label": "insufficient",
      "reason": "Причина выбранной метки по исходным фактам и policy.",
      "factual_issue": false
    }
  ]
}
```

Допустимы A/B/tie/insufficient. `reason` обязателен; `factual_issue` — необязательный
булев флаг обнаруженной ошибки фактов. Пропуски, лишние строки, повторные или
чужие pair IDs, неизвестные поля и некорректные метки отвергаются.
`reviewer_kind=assistant` никогда не обозначает человеческую оценку.
`claimed_human_review=true` для user — только декларация происхождения файла:
модуль не проверяет автора и не удостоверяет независимость человеческой оценки.

После завершения dataset:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_review compare --directory tests/artifacts/reranker --cohort-id rerank-grounded-cohort-v9 --review-file /absolute/path/user-review.json
```

Сравнение проверяет sealed dataset и семейства, исходные hashes, точные факты
пакета и соответствие всех 20 пар. Отчёт `*-comparison-<review_hash>.json`
показывает agreement, preference_reversal, decisive_vs_tie,
decisive_vs_insufficient и tie_vs_insufficient, причины и число неразрешённых
ошибок фактов. Метки учителя и dataset не меняются автоматически.
Каждый `factual_issue=true` остаётся неразрешённым и блокирует активацию.
Даже полное согласие не разрешает активацию: отдельно нужны подтверждение
человеческой проверки и quality gates обучения из
[контекста реранкера](../context/51-reranker-and-feed-learning.md).

Модуль не вызывает API, не читает credentials и не обучает модель.
Существующий v7 manifest/code hash не меняется: это отдельный операторский инструмент.

## Выполненная user20 проверка repair-v4 (08.10.2026)

Слепая пользовательская проверка по policy-corrected пакету
`rerank-grounded-pair-repair-v4/reviews/user-packet.json`
(packet_hash `0f8314c5…`, base — v9 user-packet `1da2247e…`) выполнена Mike
и запечатана: `user-comparison-6d149ab0….json` (report_hash `9fc71db7…`).

Результат: **17/20 согласий с учителем, 0 factual issues** —
2 preference_reversal (mobile-07: user B / teacher A; data-02: user A / teacher B)
и 1 decisive_vs_tie (qa-01: user B / teacher tie), каждая с reason проверяющего.
Ассистентская проверка того же пакета — 20/20. Формальные метки датасета
не менялись; disagreements остаются в отчёте как расхождение человека с учителем.

Это закрывает человеческую leg валидации разметки repair-v4. Численные
activation gates (ΔNDCG ≥ 0.02 и положительные bootstrap-границы) по-прежнему
не выполнены — артефакт `c18f4bc1` продолжает sort-only rollout;
[дополнительный сложный бенчмарк](rerank-hard-benchmark.md) показывает, что
сама калибровка NDCG-порога требует решения оператора.
