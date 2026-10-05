# Диагностика SystemOne на frontend-05, 06.10.2026

Расширение и обучение остаются остановленными. Исправленный pair-промпт v2
устранил асимметрию трёх исследованных пар, но не прошёл весь ограниченный pilot.
Полные исходные факты, ответы, distributions, blind receipt, hashes и расход:
[rerank-frontend05-diagnosis.json](../data/validation/rerank-frontend05-diagnosis.json).

## Что проверено в исходном кэше

Все шесть requests/response hashes проверены. В обратном запросе меняются только
A/B: вакансия, предпочтения и каждый факт совпадают по JSON-значениям.
Новые v1 HTTP-запросы не выполнялись; cached ответы не перезаписывались.

| Пара | Прямой выбор / confidence | Обратный выбор / confidence | Симметрия |
|---|---|---|---|
| 0/1 | B / 0.3684 | A / 0.5399 | Да |
| 0/2 | A / 0.1284 | A / 0.5496 | Нет |
| 0/3 | A / 0.1940 | A / 0.4176 | Нет |

Confidence — нормированная высота максимума распределения, а не вероятность
правильного сравнения. Формула `(p_max - 1/4) / (1 - 1/4)` в
[Kev API](https://github.com/jaredpalmer/kev/blob/main/kev/api.py) воспроизводит
наблюдаемые значения с учётом округления. В паре 0/2 прямые `p(A)=0.3463`,
`p(B)=0.3308`: преимущество всего 0.0155. V1 адаптер считал любой единственный
максимум решающим A/B; confidence проверялся на корректность, но не на надёжность.

Вакансия class-only, без явных технических minima; bio упоминает интерфейсы,
API и SQL. Employer preference: teamwork и экономичная зарплата при приемлемом
техническом соответствии, без численного определения «приемлемого» и веса экономии.
Кандидат0: junior formal grade, React3/API2/SQL1/teamwork6, pending, salary50000;
структурированные duration/production неизвестны. Более сильные профили:
1 — middle, React4/API3/SQL2, self_reported, salary65000;
2 — senior, React5/API4/SQL3/teamwork7, confirmed, salary90000;
3 — intern formal grade, React6/API5/SQL4, partial, salary120000.

Описание3 начинается с «Senior», описание1 говорит «Junior», описание2 — «средний».
Формальный результат теста и самоописание роли — разные оси. Это семантическое
напряжение, которое v1 не объяснял; фактическая ложь из него не следует.
Unknown duration не равна нулю; self_reported/pending не обнуляют hard skills.

## Слепая проверка ассистентом

Отдельный critic получил только пакет исходных фактов, без истории чата, ответов
teacher, features, ratings или scores комбайна. A/B и порядок трёх случаев
перемешаны; packet/hash и mapping сохранены. После уточнения семантики grade/bio
и статусов, также без показанных прогнозов, он предпочёл профиль1, профиль2,
профиль3 над0. Уверенность в качественном выборе — medium; доказанных factual
errors нет. Его начальная трактовка grade/bio как factual errors была исправлена.

Это субъективные tradeoffs при неизвестных технических minima и весе экономии.
Проверка ассистентом не является независимой человеческой оценкой, не заменяет
disjoint20 Mike/20 assistant и не доказывает accuracy teacher. Два A/A exact swaps
всё же нарушают требуемую симметрию независимо от предпочтительного кандидата.
API не выдаёт внутреннее объяснение; позиции и слабые распределения — наблюдения,
а роль неоднозначного запроса — гипотеза. Несколько уточнений тестировались вместе.

## Исправленный диагностический pilot

Опциональный `pair_prompt_version=rerank-systemone-pair-v2`: контекст вакансии и
preferences перед кандидатами, явные semantics grade/evidence/unknown facts,
симметричные criteria, запрет придуманных minima/зарплатных порогов. Salary влияет
на предпочтение лишь при явном указании работодателя. V1 payload/hash/default
и независимый grade остаются прежними; результаты v2 не включены в cohort.

V2 сохраняет raw answers; derived label становится `insufficient` при
confidence<0.2 или top-two probability gap<0.05. Это явная некалиброванная
эвристика. Она не исправляет raw выбор и не может маскировать провал pilot.

До запросов заморожены10пар из тех же10профилей frontend-05/validation:
3 исследованных +7 дополнительных, по direct/reverse. Пара0/5 отдельно
проверяет salary tradeoff при одинаковых уровнях; grade/experience различаются,
поэтому это контроль без изоляции причинного эффекта зарплаты. Новых профилей,
generation, grade и Luna вызовов нет. Максимум20pair attempts, без automatic retry.

Гейты: raw и derived consistency≥8/10, direct decisive≥6; все3 исходных пары
должны оставаться решающими и симметричными и в raw, и после abstention.

- Исходные3: raw B/A во всех случаях, confidence0.5766/0.629,
  0.5266/0.6524, 0.2037/0.5112; их gate пройден.
- Весь pilot: raw7/10, derived8/10, direct decisive5/10 — **stopped**.
- Остались расхождения0/4 (`tie/A`), 0/8 (`A/A`, низкая уверенность),
  1/2 (`tie/A`). Abstention скрыла лишь raw расхождение0/8; raw gate всё равно красный.
- Actual usage13824input/1515output tokens, 10.68с; shared ledger28generation /
  676pair /119grade. Все старые cache bytes сохранены. Повтор completed stopped
  pilot проверен без HTTP/key reads и без изменения ledger.

Другой pilot, новая версия или relabeling автоматически не запускались.
Default teacher, frozen dataset, training и activation не изменены.

## Воспроизведение

```sh
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_diagnostics audit
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_diagnostics pilot
```

`audit` читает cache и сохраняет immutable fact/blind receipts. `pilot` требует
sealed blind assistant receipt; существующий stopped report возвращается без
новых вызовов. Прерванный процесс оставляет started receipt и запрещает новый
запуск без operator review. Несовместимые hashes/изменённые inputs отвергаются.

Дополнительно найдено неточное поле `split=validation` в историческом mixed
generator pilot для frontend-06. Замороженный manifest всегда относил его к
calibration. Исправление метаданных записано в новом diagnosis report с hash
старого; dataset assignments и старый отчёт не перезаписаны.

## Проверки реализации

`PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python
-m pytest services/matching-engine/tests -q` с отдельным PostgreSQL
`PIPELINE_TEST_DATABASE_URL` —356passed. `pnpm --filter @comit/api test` —14passed;
`pnpm typecheck`, `pnpm build` —зелёные. Из matching-engine:
`.venv/bin/ruff check src tests/test_rerank_systemone.py tests/test_rerank_diagnostics.py`
—зелёный; прежние4I001 в нетронутых tests не исправлялись.
`WEB_PORT=38088 API_PORT=38080 MATCHING_PORT=38101 TESTING_PORT=38102
INTEGRATION_PORT=38103 SMOKE_TRIES=1 make smoke` —12проверок зелёные.
Production, remoteCI, image build и полный learned R₀ не проверялись.
