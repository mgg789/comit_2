# Ограниченное сравнение Gemma и Qwen

По разрешению Mike выполнены ровно восемь pair-запросов: две фиксированные
validation пары × две модели × direct/reverse. Результат **stopped**: все восемь
попыток завершились HTTPError, ответов моделей нет. Качество, симметрия и
согласие с blind review **не оценены**. Учитель не выбран, обучение не начато.
[Запечатанный отчёт](../data/validation/rerank-teacher-comparison.json).

## Зафиксированный контракт

Opt-in `ComparativeTeacherProviders` использует `gemma4-vlm` и `qwen3.8-27b`
через прежний `/v1/chat/completions`, prompt `rerank-pair-comparative-v3`.
Модели получают одинаковые messages/schema; только Qwen имеет
`chat_template_kwargs.enable_thinking=false`. Temperature0, max_tokens1400,
до четырёх одновременных запросов на пару, общий существующий лимит пяти
remote HTTP-слотов сохранён. Старые Gemma v1/v2 и SystemOne identities/defaults
не изменены. Каждая попытка, включая HTTP-отказ, входит в общий pair ledger.

Обязательны `label`, короткая `rationale`, непустые `evidence_A` и `evidence_B`
из точных строк своих исходных фактов, `uncertainties` и
`salary_comparison_choice`. Ссылки обеих сторон требуются также для tie/insufficient.
Зарплатная арифметика вычисляется кодом: обе суммы, cheaper_side и delta B−A.
Ответ обязан повторить направление сравнения без coercion; неизвестная сумма
остаётся неизвестной. Это факт, не правило выбора кандидата.
Уровни и подтверждения разделены; narrative роль не заменяет формальный grade.
Расплывчатый class-only tradeoff может приводить к insufficient. Literal guard
отвергает зарплатную лексику rationale без соответствующего предпочтения;
он не доказывает семантическую правильность свободного объяснения.

## Источники и гейты

- `frontend-05` 0/1: junior React3/teamwork6/50000 и middle
  React4/teamwork5/65000. Предпочтения командности и экономии не определяют
  технический минимум или веса. Frozen assistant label — insufficient.
- `backend-05` 5/3: confirmed middle с production support и Python6/SQL5
  против pending intern без production support и Python3/SQL2. Работодатель
  явно предпочитает релевантный опыт эксплуатации. Frozen assistant label — A.

Исходники, split, mapping и review hashes проверены до HTTP. Запросы и гейты
запечатаны заранее. Модели не получают main score, R₀ или reviewer labels.
Две пары выбраны целенаправленно с уже известными прежними outputs: сравнение
не является случайной выборкой или независимой оценкой качества.
Сохранённый review выполнен другим ассистентом вслепую до прежних API outputs;
он не заменяет независимую человеческую проверку 20+20 из файла51.

Для каждой модели требовались четыре принятых ответа, две симметричные пары,
два совпадения с frozen review и отдельный разбор фактов без ошибок.
Даже прохождение этих маленьких гейтов не разрешает cohort relabel/training/
activation. Без ответов численные нули accepted/gates означают отсутствие
доступных результатов, **не нулевую accuracy или измеренную асимметрию**.

## Отказ и бюджет

Все восемь raw failure receipts сохранены. Старый transport сохранял тип
HTTPError без кода/тела; точная причина отказов неизвестна. Отдельный read-only
GET `/v1/models` с тем же ключом дал200 и наличие обоих aliases, без inference.
Это подтверждает доступ к каталогу, но не пригодность payload/structured schema.
Возможную несовместимость схемы нельзя объявлять установленной причиной.
Для будущих ошибок transport теперь сохраняет только численный HTTP-код;
тело, заголовки, URL и текст исключения не записываются. Новых POST не было.

Ledger generation28 / pair691 / grade119. Всего15из20 диагностических попыток
использованы, осталось5; общий pair остаток2309. Generation/grade не изменены.
Usage не возвращён, токены и стоимость **неизвестны**, а не равны нулю.
Повтор завершённого запуска проверен без HTTP/key reads и изменения ledger.
Исторические cache bytes сохранены. Started receipt блокирует implicit retry
после прерывания; изменение исходников или опубликованных raw receipts отвергается.

Следующий шаг — установить совместимость API и structured schema отдельной
минимальной проверкой в явно выделенном бюджете. Она не запущена. Остаток5
не позволяет повторить полное восьмизапросное сравнение. Полный quality pilot,
пересмотр объёма единой разметки и обучение остаются остановленными.

## Повтор и проверки

```sh
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_teacher_comparison
```

Команда возвращает sealed raw report; итоговое решение stopped хранится отдельно
в fact-review и tracked отчёте. Публичные API/shared/backend модели не менялись.

Проверено на этом checkpoint:

- `PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python
  -m pytest services/matching-engine/tests -q` с sourced отдельной тестовой БД:
  **493passed**, включая57новых проверок. Прежние Starlette/httpx/fork warnings.
- Из matching-engine `.venv/bin/ruff check src tests/test_rerank_pair_teacher.py
  tests/test_rerank_teacher_comparison.py` — passed.
- `pnpm typecheck`, `pnpm build` — passed.
- `pnpm --filter @comit/api test` —27passed/4skipped: отдельные auth/profile
  HTTP/PG test databases настраиваются отдельно, эти контуры проверяются там.
- `WEB_PORT=38088 API_PORT=38080 MATCHING_PORT=38101 TESTING_PORT=38102
  INTEGRATION_PORT=38103 SMOKE_TRIES=1 make smoke` —12passed после старта
  собственных кратковременных сервисов с настоящими app factories и built API.
  Первоначальный запуск без этих процессов дал8failures. Процессы остановлены
  после проверки; существующие локальные web/PG контейнеры не менялись.

Remote CI, production, полный quality pilot и learned R₀ acceptance — следующие этапы.
