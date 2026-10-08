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
| `python` | python:3.12-bookworm | `pip install -e` matching/testing/integration engines `[dev]` → `pytest` их `tests/` (без БД: PG-тесты скипаются) |
| `regression` | python:3.12-bookworm + service `pgvector/pgvector:pg16` | тот же набор сервисов → `pytest` с `PIPELINE_TEST_DATABASE_URL` |
| `smoke` | хост (нужен docker) | изолированный compose-проект `comit-ci`, порты 38xxx → `./tests/smoke.sh` → `down -v` |

- Джобы `js`, `python` и `regression` выполняются в контейнерах — хосту не нужны node/python.
- Smoke не может идти параллельно сам с собой (порты): `concurrency: smoke-host`, без cancel.
- Smoke-гейт обязателен для мержа (AGENTS §11).

## Джоба regression — полный Python-набор с PostgreSQL

Регулярная джоба `python` идёт без базы, поэтому все PostgreSQL-тесты matching-engine
(durable pipeline, combine-хранилище, гонки публикаций) в ней молча пропускаются.
Джоба `regression` на `mgg-regression-1` закрывает этот пробел:

- Поднимает disposable Postgres 16 с pgvector (`pgvector/pgvector:pg16`, нужен миграциям matching) как GitHub Actions service container; база
  `matching_test` живёт только внутри джобы, учётка тестовая (`comit`/`comit-regression`).
- Через `PIPELINE_TEST_DATABASE_URL=...@postgres:5432/matching_test` активирует
  integration-тесты (`pytest.mark.integration`), помеченные этим env.
- Прогоняет тот же полный набор, что и `python`, — то есть всё: unit + PG.
- Раннер один, джобы на нём не пересекаются; портов на хост не публикует.

Парная быстрый контур — golden-регрессия скоринга комбайна
(`services/matching-engine/tests/test_combine_regression.py`): она офлайн,
без PG и модели, выполняется уже в обычной джобе `python`. Протокол обновления
снимка — [matching.md](matching.md#регрессия-скоринга-golden).

Тесты `services/verification-engine` в CI-джобы пока не входят; их запускает `make regression`.

Локально: `make regression` (полный набор, включая verification-engine; с экспортированным
`PIPELINE_TEST_DATABASE_URL` — вместе с PG-тестами), `make regression-golden-update`
(перегенерация golden).

## deploy.yml — push в main (и workflow_dispatch)

Сборка prod-стека (`infra/docker-compose.prod.yml`) прямо на сервере и health-проверка — `docs/deploy.md`.

## Секреты

- Секретов в репозитории нет; на сервере — `/home/mgg/comit/.env` (git-ignored).
- Если появятся секреты Actions — только GitHub Environment secrets, никогда в коде/логах/памяти.

## Известные упрощения каркаса

- Нет кэша pnpm/pip между прогонами (первое время ок; добавить `actions/cache` или registry при замедлении).
- Образы не публикуются в registry — сборка на месте; при росте времени сборки перейти на GHCR.
