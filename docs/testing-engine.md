# Testing engine — механика тестирования

> Статус (11.10.2026): в коде есть два FastAPI-приложения. Развёрнутый контейнер
> (`python -m testing_engine`, порт 8102) обслуживает только blueprint-реестр
> (`create_app`). Адаптивный контур грейд-теста (`create_testing_app`: сессии,
> банк, прокторинг-агрегаты, публикация результата в API) реализован и покрыт
> тестами, но entrypoint сервиса его пока не запускает, поэтому в compose он
> недоступен. UI тестирования не публикуется (`/testing/` → 404).
> Целевой дизайн — [testing-design.md](testing-design.md), канон —
> `context/60-testing-engine.md`.

## Две анти-цели ТЗ и наш ответ

| Проблема ТЗ | Решение |
|---|---|
| Единый набор заданий → утечка | Параметризованные семейства, перемешивание опций MCQ, мораторий показанных экземпляров и ротация банка |
| Нейрогенерация под каждого → несопоставимая сложность | Уровень (L1–L4) задаётся банком, а не сессией; LLM генерирует варианты только офлайн в банк с валидацией, рантайм сессии LLM не вызывает |

## Развёрнутое приложение: blueprint-реестр

- `blueprints/*.yaml` — шаблоны (специализация, тема, тип, параметры, сложность); сейчас один файл `algo-hash-lookup.yaml`.
- `src/testing_engine/blueprints.py` — загрузка и валидация; каталог из `BLUEPRINT_DIR` (по умолчанию `./blueprints`).
- Маршруты: `GET /healthz`, `GET /ready`, `GET /blueprints`, `GET /blueprints/{id}` (404, если нет).
- Порт — `PORT` (по умолчанию 8102).

## Адаптивный контур (`create_testing_app`)

Хранилище — атомарные JSON-файлы в `TESTING_DATA_DIR` (по умолчанию `./data/testing`;
в compose — volume `testing-data`, `/data/testing`): `items.json` (банк) и
`sessions/<id>.json`. SQL-схемы у контура пока нет.

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/testing/sessions` | `{candidate_key?, specialization?, declared_grade?, consents?:{video_analysis, screen_recording}}` → `{session_id, status, tasks_given}` |
| GET | `/testing/sessions/{id}/next` | следующее задание (без ответа); 409 `session_finished`/`session_complete`/`bank_exhausted`, 404 `session_not_found` |
| POST | `/testing/sessions/{id}/answer` | `{item_id, answer}` → `{correct, judge_status, answered, decided_grade, status}` |
| POST | `/testing/sessions/{id}/supervision` | только агрегаты прокторинга → бейдж присутствия; 400 на некорректные/завышенные счётчики |
| POST | `/testing/sessions/{id}/media` | контур полной записи: 409 `recording_disabled`, пока `TESTING_RECORDING_MODE` ≠ `full`; 403 без согласия на запись экрана |
| GET | `/testing/sessions/{id}/result` | статус, грейд, постериор по четырём грейдам, ответы, бейдж, согласия |

- **Банк.** При старте засеивается `bank_seed.initial_items()`: 37 заданий семи
  семейств (`algorithms`, `sql`, `rest_api`, `python`, `react`, `docker`,
  `teamwork`) — 29 MCQ, 4 code, 4 extended, уровни L1–L4. Семейство берётся из
  `specialization`, неизвестное → `algorithms`.
- **Адаптивность** (`adaptive.py`): старт с L2, байесовский постериор над
  intern/junior/middle/senior, следующий уровень — граница максимальной
  неопределённости. Ранняя остановка: лидер ≥ 0.70 и второй грейд отстаёт более
  чем в 2 раза (`DECISION_PROBABILITY`, `FLIP_RATIO`); потолок — 30 заданий.
  В дизайне указан порог 0.8 — в коде сейчас 0.70.
- **Проверка ответов** (`judge.py`): MCQ — точное серверное сравнение; code —
  только через внешний сервис code-judge (`CODE_JUDGE_URL`), локального
  исполнения кандидатского кода в testing-engine нет (issue #18). Без сервиса
  ответ записывается как `code_judge_unavailable` и не влияет на грейд;
  развёрнутые ответы записываются как `manual` и не оцениваются.
  `LocalPythonJudge` (allowlist импортов, запрет dunder, сравнение JSON в
  родительском процессе) включается только флагом `TESTING_LOCAL_JUDGE=1` для
  офлайн-фикстур и валидатора генератора.
- **Ротация и генерация** (`store.py`, `generator.py`, `rotate.py`): сгенерированные
  экземпляры выводятся из оборота после 3 показов или по возрасту (1 день);
  seed-задания не выводятся; показанное сегодня выдаётся повторно только если
  других активных вариантов уровня нет (relaxed-fallback, наименьший exposure first).
  `PYTHONPATH=src python -m testing_engine.rotate [--dry-run] [--daily-limit 50] [--min-stock 5]`
  добирает банк через DeepSeek (`DEEPSEEK_API_KEY`) с дневным ledger-лимитом
  (`ledger.json`) и валидатором (4 уникальные опции MCQ, эталонное решение
  проходит тесты, ≥3 пунктов рубрики, без дублей). Без ключа выполняется только
  вывод из оборота и отчёт о дефиците.
- **Прокторинг** (`supervision.py`): бейдж из агрегатов (≥95 % в кадре —
  «хороший», 85–95 % — «нормальный», 70–85 % или мульти-лицо — «средний», ниже —
  «низкий», без камеры — «не определён»); кадры не покидают устройство.
  Reference-клиент: `static/proctoring-client.js`. Носители режима `full`
  хранятся 90 дней (`maintenance()`/`cleanup_expired_media`).
- **Публикация результата** (`publisher.py`): при завершении сессии с
  определённым грейдом отправляет `POST /internal/testing/session-result`
  (`X-Internal-Token` = `TESTING_ENGINE_TOKEN`, URL — `TESTING_API_URL`) с
  `candidate_id` (UUID из `candidate_key`), `session_id`, `specialization`,
  `decided_grade`, `supervision_badge`, `tasks_answered`. Без настройки или при
  ошибке результат остаётся только в testing-engine. Cooldown пересдачи (7 дней)
  и повышения (30 дней) проверяет API ([api.md](api.md)).

## Сервис code-judge

`services/code-judge` — отдельный FastAPI-сервис (`python -m code_judge`, порт
`CODE_JUDGE_PORT`, по умолчанию 8105; данные — `CODE_JUDGE_DATA_DIR`, по умолчанию
`./data/code-judge`). Маршруты: `GET /healthz`, `GET /ready` (`runner: not_installed`,
состояние DMOJ-моста), `POST /submissions` (только `python`, код до 100 000 символов),
`GET /submissions/{id}`, `GET /submissions?item_id=&limit=` (1–100),
`POST /problems/{item_id}/package` — упаковка code-задания банка в каталог задачи
DMOJ. Песочный runner ещё не установлен: отправки хранятся со статусом `stored`
и не получают вердикта; живой DMOJ-мост выключен (env `DMOJ_JUDGE_ADDRESS`).
Сервис не входит в compose, Dockerfiles и CI; тесты: `services/code-judge/tests`.

## Правила грейд-теста (ТЗ)

- Грейд не понижается принудительно; не прошёл — тест на уровень ниже; прошёл уверенно — предложение повысить.
- Смена грейда ограничена по времени: повтор теста не раньше 7 дней, подтверждение повышения — не раньше 30 дней (проверяет API); история подтверждённых результатов видна кандидату (`GET /candidate/testing-history`).
- Категория (специализация × грейд) присваивается только тестом; подтверждённый результат превращает `self_reported` грейд профиля в `approved`.

## Валидация тестирования (обязательна к сдаче)

1. Test-retest: повторное прохождение на новых вариантах → стабильный грейд.
2. Дискриминативность: синтетические слабые/сильные сессии → разделяет ли скор классы.
3. Экспертная разметка: решения с эталоном → совпадение с автоскором.
4. Утечка: «слитый» вариант не решает следующий вариант того же семейства.

Прогон `services/testing-engine/tests/validation_suite.py` и цифры — в
[testing-design.md](testing-design.md#валидация-выполнена-10102026); отчёт
`data/validation/testing-validation.json` (итоговый флаг `passed=false` из-за
частичного преимущества при утечке на тонком банке).

## Проверки

```sh
cd services/testing-engine && python -m pytest -q   # 25 тестов
cd services/code-judge && python -m pytest -q       # 5 тестов
```
