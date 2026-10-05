# Пилот Gemma как учителя, 06.10.2026

Пилот **остановлен на формате ответа**, до проверки качества разметки.
Отправлены пять из максимум двадцати запросов; остальные пятнадцать не запускались.
Обучение, смена учителя основного датасета и активация R₀ не выполнялись.
Исходные факты, prompts, raw responses, blind review, hashes и расход сохранены в
[отчёте](../data/validation/rerank-gemma-teacher-pilot.json).

## Что зафиксировано до API

Десять пар из существующих validation/calibration-семейств, без новой генерации:

| Семейство | Пары | Особенность |
|---|---|---|
| frontend-05, validation | 0/1, 0/2, 0/3 | Исходные проблемные пары SystemOne; class-only |
| backend-05, validation | 5/3, 5/8, 2/3, 7/3 | Явные требования, предпочтение production support, пустой bio |
| backend-06, calibration | 5/3, 1/2, 0/4 | Зарплатные компромиссы без заданного технического минимума |

Профили и предпочтения перенесены без изменения фактов. Golden не использовался.
Выборка целевая диагностическая, не репрезентативная оценка качества найма.
Случаи и A/B для отдельного critic перемешаны. Он получил только исходные факты,
без истории чата, scores комбайна и ответов учителя. Шесть решающих оценок и четыре
`insufficient` сохранены **до API**. Ошибочная формулировка проверяющего о
«confirmed» production для self-reported профиля исправлена по источнику до
запросов, без показанных прогнозов. Это оценка ассистента, не независимого человека.

Гейты до вызовов: симметрия ≥8/10; точное согласие с blind оценкой ≥8/10;
минимум шесть решающих blind случаев и ≥80% согласия на них; отсутствие фактических
ошибок. `tie` и `insufficient` сравниваются как отдельные метки, не бинарные победы.
Успех численных проверок требует последующей проверки текста объяснений:
runtime статус `needs_fact_review` сам по себе не разрешает обучение/активацию.

## Что произошло

Модель `gemma4-vlm`, endpoint `https://ai.sverk.tech/v1/chat/completions`,
prompt `rerank-gemma-pair-v1`, temperature 0, max_tokens 1400. Запрос содержит
JSON Schema с label/rationale/evidence/uncertainties. Каждая ссылка на факт должна
содержать точное исходное значение и тип; решающая метка — ссылки на обе стороны.
Контактов, реальных кандидатов, scoring features и токенов в данных нет.

Пять запросов первой партии завершились за 13.71с:

- Четыре ответа имеют `finish_reason=length`: оборванные строки и повторяющиеся
  пробельные последовательности. Каждый израсходовал 1400 output tokens.
- Один ответ имеет `finish_reason=stop`, но цитирует числовые уровни как строки
  (`"5"` вместо `5`). Валидатор отвергает его без приведения типов.
- Ноль валидных ответов, ноль завершённых сравниваемых пар. Симметрия и согласие
  **не оценены**; нулевые counters не означают измеренную accuracy 0%.
- Actual usage: 3534 input + 5968 output = **9502 tokens**. Shared ledger:
  generation28 / pair681 / grade119. Генерация и grade не вызывались.

Сложная схема со смешанными primitive types — гипотеза причины, а не доказанный
дефект сервера или модели. Обрывки решений нельзя принимать за валидные labels
или основание оценки качества. Серверные настройки не изменялись. Повторы,
исправляющие вызовы и новый prompt-пилот не запускались.

## Адаптер и воспроизведение

`rerank_gemma_teacher.py` — отдельный pair-only адаптер. Входные факты/версии
входят в cache identity; старый SystemOne default и его cohort остаются прежними.
HTTP использует прежний lazy credential reader, запрет redirects, общий ledger
и максимум пять remote slots. Исключения сети не раскрывают ключи. Generation
и grade через этот адаптер запрещены до резервирования бюджета.

```sh
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_gemma_pilot prepare
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_gemma_pilot run
```

`prepare` только запечатывает manifest, blind packet/map и точные request hashes.
`run` требует sealed blind receipt и двадцать доступных pair slots до старта.
Работа идёт партиями до пяти запросов: после ошибки уже отправленная партия
дожидается завершения, следующая не отправляется. Все попытки учитываются.
Запечатываются также отвергнутые raw responses. Завершённый stopped report
возвращается из кэша; crash оставляет started receipt и запрещает implicit retry.
Изменённые исходники, prompts, request/response hashes и receipts отвергаются.
В реальном пилоте проверены повтор без HTTP/key reads и неизменность ledger.
Исторические cache bytes сохранены. Raw receipts отдельно запечатаны после
первичного разбора невалидных ответов; этот более поздний момент не скрывается.

## Что требуется дальше

Сначала отдельная проверка двух фиксированных direct/reverse запросов с новой
версией промпта и простой схемой, без смешанных типов внутри evidence.
Потратить можно часть оставшихся пятнадцати диагностических запросов; старая
разметка и расход сохраняются. Это предложение, запуск не выполнен.
Даже успешный format probe не заменяет полный quality pilot и ручные проверки.

Полная единая переразметка 599 профилей новым учителем по старому протоколу
потребует ещё 2871 pair и 599 grade: итоги 3552/718 превышают лимиты 3000/600.
Вариант восьми профилей на каждое из прежних 60 семейств даёт 1860 новых pair,
480 новых grade: итоги 2541/599. Однако generation120 без запаса на сбои и всего
одна grade-попытка запаса делают такой план ненадёжным. Он не утверждён и не запущен.
Нужны preflight исходников, запас на сбои и явное новое решение об объёме;
диагностические labels не попадают в train, старые teacher versions не смешиваются.
Разбиение, ancestry, golden thresholds, 20+20 reviews и условия хвоста сохраняются.

## Проверки

Из корня: `PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python
-m pytest services/matching-engine/tests -q` с отдельной тестовой PostgreSQL БД;
**410passed** (включая54новые проверки); имеются прежние предупреждения
Starlette/httpx и multiprocessing fork, тесты не пропускались.
из matching-engine: `.venv/bin/ruff check src tests/test_rerank_gemma_teacher.py
tests/test_rerank_gemma_pilot.py`. Typecheck/build: `pnpm typecheck`, `pnpm build`.
API: `pnpm --filter @comit/api test` —22passed/2skipped, отдельные auth HTTP/PG
тесты требуют `AUTH_TEST_DATABASE_URL`, этот контур не запускался.
Smoke: `WEB_PORT=38088 API_PORT=38080 MATCHING_PORT=38101 TESTING_PORT=38102
INTEGRATION_PORT=38103 SMOKE_TRIES=1 make smoke` —12проверок зелёные.
Production, remote CI и реальная learned R₀ acceptance не проверялись.

## Разрешённая проверка простой схемы

После отдельного разрешения Mike выполнены ровно два последовательных запроса
на первой frozen паре frontend-05 0/1: A/B и B/A. Prompt
`rerank-gemma-pair-v2-format`, прежняя модель/endpoint/temperature/max_tokens.
Поле evidence теперь массив строк из enum исходных ссылок, например
`A.levels.react[int]=3` или `A.duration_months[null]=null`. Тип и значение факта
входят в точную строку; источник проверяется без coercion. Старый v1 не изменён.
Одна короткая rationale и максимум четыре ссылки уменьшают объём ответа.
Схема, инструкция краткости и фактическая последовательность вызовов изменились
вместе, поэтому отдельно доказать причину прежних обрывов эта проверка не может.

- Оба ответа complete JSON, `finish_reason=stop`, 99/101 output tokens.
  Все ссылки точно совпадают с источником; прежних обрывов/type errors нет.
- Оба решающих ответа приводят четыре ссылки только на победителя: direct B,
  reverse A. Валидатор требует факты обеих сторон и отвергает обе метки.
  `valid_responses=0` здесь означает ноль **полностью принятых** ответов,
  а complete JSON responses=2. Raw B/A симметричны, но не становятся разметкой.
- Frozen blind label этой пары — `insufficient`. Raw выбор не совпал с ней;
  это не измеренная accuracy учителя на полноценной выборке.
- В direct rationale выбранный B назван более экономичным: salary_min B=65000,
  A=50000. Такое сравнительное объяснение исходные факты не поддерживают.
  Этот разбор сделан ассистентом после просмотра outputs, не является blind
  review или независимой человеческой оценкой. Фактическое замечание не закрыто.
- Actual1966input+200output=2166tokens, 7.61с. Ledger28/683/119; всего семь
  вызовов из первоначального диагностического allowance20, остаётся13.
  Shared pair budget оставляет2317попыток; generation/grade не изменены.

Результат — **stopped**, quality/training/promotion/activation не разрешены.
Никаких дальнейших запросов или automatic repairs не отправлено. Исходные
cache bytes и оба raw ответа сохранены. Проверен повтор без HTTP/key reads,
изменения ledger; crash запрещает implicit retry, tamper receipts отвергается.
[Новый отчёт](../data/validation/rerank-gemma-format-probe.json) содержит отдельные
format/evidence/prose результаты и hashes; старый отчёт не перезаписан.

```sh
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_gemma_format
```

До rebase реализация и16новых regression checks:426Python/PGtests passed; из
matching-engine `.venv/bin/ruff check src tests/test_rerank_gemma_format.py`
зелёный. `pnpm typecheck`, `pnpm build` и прежняя команда `make smoke` с ports38xxx
зелёные. Публичные API/shared контракты в format-пробе не менялись; отдельные
API/auth tests до rebase не повторялись. Production/remote CI не проверялись.

После переноса checkpoint на параллельный dev `c742bf3` повторены полные checks:
436Python/PGtests passed, Ruff/typecheck/build/smoke12 зелёные.
`pnpm --filter @comit/api test` —27passed/4skipped: отдельные auth/profile HTTP/PG
tests требуют своих test database переменных и не выполнялись в этом контуре.
Изменения профилей кандидата и freshness сохранены; matching format-probe
не использует scores/main-v2 и не меняет исходные frozen synthetic факты.
