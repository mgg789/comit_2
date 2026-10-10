# CI — GitHub Actions на self-hosted раннерах

## Инфраструктура

mgg-server (drj-1), 4 self-hosted раннера `mgg789/comit_2` (user-systemd `github-runner@<имя>`, каталог `/home/mgg/actions-runners/<имя>`):

| Раннер | Лейблы | Роль |
|---|---|---|
| mgg-ci-1/2/3 | `ci, mgg-server` | все CI-джобы и деплой |
| mgg-regression-1 | `regression, full, mgg-server` | джоба `regression`: полный Python-набор с PostgreSQL |

Управление (без sudo): `systemctl --user status|restart github-runner@mgg-ci-1`; логи: `journalctl --user -u github-runner@<имя>`.

## ci.yml — пул-реквесты и push в dev/main

| Джоба | Контейнер | Шаги |
|---|---|---|
| `js` | node:22-bookworm | `pnpm install --frozen-lockfile` → `pnpm -r typecheck` → `pnpm -r build` |
| `api` | node:22-bookworm + Python 3.12 + isolated pgvector | все API-тесты, отдельная disposable БД на файл → c8 → line/branch gate |
| `python` | python:3.12-bookworm | matching/testing/integration/agents/verification `[dev]` → отдельный pytest для каждого сервиса, unit coverage и явные причины skip |
| `regression` | python:3.12-bookworm + service `pgvector/pgvector:pg16` | тот же набор сервисов с disposable PostgreSQL → line/branch gates каждого пакета |
| `smoke` | хост (нужен docker) | explicit `docker-compose.dev.yml --profile dev`, проект `comit-ci`, порты 38xxx, real pinned Harrier cache → `SMOKE_MODE=dev ./tests/smoke.sh` → `down -v` |

- Джобы `js`, `python` и `regression` выполняются в контейнерах — хосту не нужны node/python.
- Smoke не может идти параллельно сам с собой (порты): `concurrency: smoke-host`, без cancel.
- Smoke-гейт обязателен для мержа (AGENTS §11).
- Smoke использует `--env-file /dev/null`: defaults только явного dev compose, без `.env` продакшена.
  Все семь опубликованных API/service портов, включая agents/verification, находятся в 38xxx.
- До запуска smoke cache `comit-ci_harrier_cache` получает реальные model files из
  `comit_harrier_cache` через read-only mount, если он существует; копируется только
  `hub/models--microsoft--harrier-oss-v1-270m`, без HF credentials. При отсутствии
  exact snapshot выполняется `snapshot_download(MODEL_ID, revision=MODEL_REVISION)`
  внутри matching image. ID/revision берутся из actual `embeddings.py`; отсутствие
  exact cache ломает CI. Runtime matching остаётся offline. Только isolated CI
  volumes удаляются при teardown; production cache не изменяется.
- Этот smoke workflow синтаксически проверен; live self-hosted build/provision/smoke
  проверяется фактическим запуском Actions, не локальным config render.

## Джоба regression — полный Python-набор с PostgreSQL

Регулярная джоба `python` идёт без базы, поэтому все PostgreSQL-тесты matching-engine
(durable pipeline, combine-хранилище, гонки публикаций) в ней молча пропускаются.
Джоба `regression` на `mgg-regression-1` закрывает этот пробел:

- Поднимает disposable Postgres 16 с pgvector (`pgvector/pgvector:pg16`, нужен миграциям matching) как GitHub Actions service container; база
  `coverage_test` живёт только внутри джобы, учётка тестовая (`comit`/`comit-regression`).
- Через `COVERAGE_TEST_DATABASE_URL=...@postgres:5432/coverage_test` runner задаёт
  `PIPELINE_TEST_DATABASE_URL` и `AGENTS_TEST_DATABASE_URL` для PG-fixtures.
- Прогоняет тот же полный набор, что и `python`, — то есть всё: unit + PG.
- Раннер один, джобы на нём не пересекаются; портов на хост не публикует.

Парная быстрый контур — golden-регрессия скоринга комбайна
(`services/matching-engine/tests/test_combine_regression.py`): она офлайн,
без PG и модели, выполняется уже в обычной джобе `python`. Протокол обновления
снимка — [matching.md](matching.md#регрессия-скоринга-golden).

Тесты `services/agents-service` и `services/verification-engine` теперь входят в обе Python-джобы.
Matching PG-fixture `test_recommendations_integration.py` читает API-owned канонические
consent-таблицы. Runner запускает этот fixture последним в отдельной disposable БД, применяя настоящие
`apps/api/migrations/*.sql` с checksum/lock и `--cov-append`, без фальшивых таблиц
и исходных пользовательских строк. Остальные matching PG-тесты используют свои
схемы consent; оба запуска входят в итоговый отчёт и обязательный gate.

Сервисы запускаются отдельными процессами: одноимённые `test_api.py` и импорты helpers
в старых тестах несовместимы с единым pytest-процессом.

Локально: `make regression` (полный набор, включая verification-engine; с экспортированным
`PIPELINE_TEST_DATABASE_URL` — вместе с PG-тестами), `make regression-golden-update`
(перегенерация golden).

## Измерение покрытия и ограничения гейтов

`tests/coverage-gates/thresholds.json` хранит нижние пороги **для всего source-пакета**,
по строкам и ветвям отдельно. Неисполненные модули включены (`c8 --all`,
`pytest --cov=<package> --cov-branch`); фильтров для искусственного повышения процентов нет.
Это защита от регрессии покрытия, а не заявление о достаточной полноте тестов.
Первичные пороги округлены вниз до пяти процентных пунктов от наблюдённой базы;
новые тесты не понижают эти floors автоматически. Низкое
покрытие verification остаётся явным техническим долгом. Unit matching отдельно дал
68.75 / 57.48%, 1098 passed, 142 skipped; unit не заменяет PG gate.

| Пакет | Наблюдённые строки / ветви, 2026-10-11 | Floor строки / ветви | Контур |
|---|---:|---:|---|
| API | 89.98 / 78.34% | 85 / 75% | PG, реальный checkout, БД на файл: 121 passed, 1 explicit chat skip, gate passed |
| matching-engine | 83.79 / 71.74% | 80 / 70% | PG: 1237 passed, 3 explicit operational skips, полный gate passed |
| agents-service | 80.95 / 59.70% | 80 / 55% | PG: 139 passed, gate passed |
| integration-engine | 84.55 / 65.00% | 80 / 60% | 10 passed, gate passed |
| verification-engine | 52.22 / 27.42% | 50 / 25% | 9 passed, gate passed |
| testing-engine (backend) | 76.46 / 62.75% | 75 / 60% | 23 passed, gate passed; frontend/e2e testing в этом покрытии не участвует |

`tests/coverage-gates/check.py` проверяет реальные числители/знаменатели без округления.
Отсутствующий, пустой или некорректный отчёт и любой провал одного порога дают exit 1.
Runner удаляет старый отчёт перед запуском и не проверяет coverage после красных тестов.
Регрессии самого checker: `pnpm coverage:test` (синтетические fixtures checker никогда
не считаются доказательством продуктового покрытия).

API runner сначала собирает `@comit/shared`, чтобы использовать production export map
с JavaScript из `dist/`. После общей JS-сборки CI дополнительно запускает
`node --test apps/api/tests/runtime-shared.test.mjs` без TypeScript loader, затем
существующие tests ui-kit/employer/candidate. Smoke ownership repair допускает только
`mgg-ci-[123]` и монтирует исключительно checkout `_work/comit_2`.

API-файлы с PG-fixtures имеют собственную БД `hiring_test_<timestamp>_<random>`.
Runner создаёт и удаляет их только внутри явно переданного disposable PostgreSQL
с `COVERAGE_TEST_DATABASE_URL` (имя основной БД должно оканчиваться `_test`).
Это необходимо, поскольку старые fixtures вручную создают/удаляют схемы и не могут
безопасно разделять одну БД даже при последовательном запуске файлов.
Для bridge-теста применяется настоящая agents migration; Python должен иметь
установленный `agents-service` (`COVERAGE_SCHEMA_PYTHON` задаёт его интерпретатор).
Тестовая роль должна иметь право создавать и удалять БД внутри этого disposable instance.
Runner выставляет `COMIT_DISPOSABLE_DATABASE_TESTS=1`; deadline fixture дополнительно
проверяет точное имя `hiring_test_<digits>_<hex>` и loopback/CI-host `postgres`,
а не фиксированный порт живого PostgreSQL.

Полный gate допускает только перечисленные исключения: API chat с внешним chat-service
(`invitation chat: two authenticated participants, files, polling, retries, consent and closure`)
и три matching consent acceptance-теста с отдельно засеиваемыми operational cards
(`LEARNING_ACCEPTANCE_DATABASE_URL`). Любой новый неожиданный skip ломает gate.
Эти исключения не являются проверенным покрытием внешнего chat или operational consent:
для них нужны отдельные acceptance-контуры. Обычная unit-джоба выводит все skips и
**не** выдаёт PG acceptance или полный coverage gate.

Локальные команды после установки dev dependencies:

```sh
pnpm coverage:api -- --unit
python tests/coverage-gates/python.py matching-engine --unit
# Только специально созданный disposable PostgreSQL, не DATABASE_URL приложения:
COVERAGE_TEST_DATABASE_URL=... COVERAGE_SCHEMA_PYTHON=... pnpm coverage:api
COVERAGE_TEST_DATABASE_URL=... python tests/coverage-gates/python.py matching-engine
```

Отчёты: `coverage/unit/` и `coverage/backend/` (git-ignored), JSON + LCOV/XML и
JUnit, публикуются CI как artifacts даже после падения. Обычный `pnpm -r build`
не доказывает тестовое покрытие. GitHub Actions workflow описан и синтаксически
проверен локально; успешный запуск remote CI отдельно подтверждается результатом Actions.

## deploy.yml — push в main (и workflow_dispatch)

Сборка prod-стека (`infra/docker-compose.prod.yml`) прямо на сервере и health-проверка — `docs/deploy.md`.

## Секреты

- Секретов в репозитории нет; на сервере — `/home/mgg/comit/.env` (git-ignored).
- Если появятся секреты Actions — только GitHub Environment secrets, никогда в коде/логах/памяти.

## Известные упрощения каркаса

- Нет кэша pnpm/pip между прогонами (первое время ок; добавить `actions/cache` или registry при замедлении).
- Образы не публикуются в registry — сборка на месте; при росте времени сборки перейти на GHCR.
