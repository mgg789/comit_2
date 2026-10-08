# Статус mvp-prod-ready для R₀ (operator receipt)

> Решение оператора (Mike, 2026-10-08): порог активации пересмотрен, работа
> над реранкером завершается на текущем артефакте `c18f4bc1` со статусом
> **mvp-prod-ready** — рабочий вариант в рамках хакатонного MVP, **не**
> производственная сертификация.
> Receipt: неймспейс `rerank-mvp-status-v1`, модуль
> `matching_engine.rerank_mvp_status`, запечатан в
> `tests/artifacts/reranker/rerank-mvp-status-v1/status.json`.

## Что это значит

- Артефакт `r0-c18f4bc1.json` (content hash `c18f4bc1…`) продолжает свой
  аудированный manual sort-only rollout. Runtime и подача не менялись;
  статус — операторская метка поверх существующего состояния, не promotion.
- Receipt пересчитывает каждую строку gate из **запечатанных** источников
  (hash-верификация): sealed golden gate внутри самого артефакта, sealed
  [оценка сложного бенчмарка](rerank-hard-benchmark.md), sealed
  assistant20/user20 сравнения repair-v4. Ничего не вписано руками.
- Исторические запечатанные отчёты (training gate `passed=false`, benchmark
  `passed=false`) сохранены как история и не переписываются.

## Пересмотр порога: обоснование

Старый порог (ΔNDCG ≥ 0.02 с положительными bootstrap-границами) недостижим
по построению на обоих измерительных инструментах: потолок идеального порядка
на sealed v9 golden — **0.0041**, на предзарегистрированном сложном бенчмарке —
**0.0130**. Пересмотренный gate требует измеримого реального выигрыша
(≥ 0.01 NDCG на независимом бенчмарке ≈ 77% его потолка и ×4 к фактически
достигнутому на sealed-корпусе), сохраняет полы отсутствия регрессии и
безопасность срезов и требует обе человеческие проверки без нерешённых
фактических ошибок. Pair accuracy на близких парах остаётся диагностикой, не
gate: swap-аудиты учителя там 26/36 — сигнал на полу его шума.

## Gate: все строки PASS (пересчитано из sealed evidence)

| ID | Правило | Значение | Итог |
|---|---|---|---|
| G1 | sealed: ΔNDCG ≥ 0, CI low ≥ −0.005 | +0.00265 / 0.0 | PASS |
| G2 | sealed: pair acc ≥ 0.65, Δ ≥ 0.03 | 0.9586 / +0.0398 | PASS |
| G3 | hard: ΔNDCG ≥ 0.01, CI low ≥ 0 | +0.01072 / 0.0 | PASS |
| G4 | hard: мин. supported-slice ΔNDCG ≥ −0.03 | 0.0 | PASS |
| G5 | assistant 20/20, user ≥ 15, factual issues 0 | 20 / 17 / 0 / 0 | PASS |

Команды (из `services/matching-engine`, каталог artifacts — основной чекаут):

```sh
PYTHONPATH=src .venv/bin/python -m matching_engine.rerank_mvp_status assign \
  --directory tests/artifacts/reranker
PYTHONPATH=src .venv/bin/python -m matching_engine.rerank_mvp_status verify \
  --directory tests/artifacts/reranker
```

`verify` пересчитывает gate заново и падает при любом расхождении с sealed
payload — статус можно перепроверить в любой момент.

## Ограничения статуса (запечатаны в receipt)

Синтетические учительские метки (реальные предпочтения работодателей не
вали)·pair accuracy на близких вызовах 0.635 (CI −0.091, шум учителя)·
sort-only без отсечения хвоста (tail gate никогда не калибровался — не хватило
релевантной поддержки)·никаких production/юридических/152-ФЗ процессов этот
статус не сертифицирует. Активация артефакта остаётся manual decision;
обычный `promote()` по-прежнему требует старых численных gates.
