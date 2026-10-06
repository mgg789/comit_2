# CI — GitHub Actions на self-hosted раннерах

## Инфраструктура

mgg-server (drj-1), 4 self-hosted раннера `mgg789/comit_2` (user-systemd `github-runner@<имя>`, каталог `/home/mgg/actions-runners/<имя>`):

| Раннер | Лейблы | Роль |
|---|---|---|
| mgg-ci-1/2/3 | `ci, mgg-server` | все CI-джобы и деплой |
| mgg-regression-1 | `regression, full, mgg-server` | тяжёлые валидационные прогоны (будущее) |

Управление (без sudo): `systemctl --user status|restart github-runner@mgg-ci-1`; логи: `journalctl --user -u github-runner@<имя>`.

## ci.yml — пул-реквесты и push в dev/main

| Джоба | Контейнер | Шаги |
|---|---|---|
| `js` | node:22-bookworm | `npm install -g pnpm@9.14.2` → `pnpm install --frozen-lockfile` → `pnpm -r typecheck` → `pnpm -r build` |
| `python` | python:3.12-bookworm | `pip install -e` для matching-, testing- и integration-engine `[dev]` → `pytest` по их `tests/` |
| `smoke` | хост (нужен docker) | chown workspace → изолированный compose-проект `comit-ci`, порты 38xxx → `./tests/smoke.sh` → `down -v` |

- Джобы `js` и `python` выполняются в контейнерах — хосту не нужны node/python.
- `services/agents-service` в CI не устанавливается и не тестируется; его проверки — локально (`docs/agents.md`).
- Новый прогон на той же ref отменяет предыдущий (`concurrency: ci-<ref>`, `cancel-in-progress`).
- Smoke не может идти параллельно сам с собой (порты): `concurrency: smoke-host`, без cancel.
- Smoke-гейт обязателен для мержа (AGENTS §11).

## deploy.yml — push в main (и workflow_dispatch)

Сборка prod-стека (`infra/docker-compose.prod.yml`) прямо на сервере и health-проверка — `docs/deploy.md`.

## Секреты

- Секретов в репозитории нет; на сервере — `/home/mgg/comit/.env` (git-ignored).
- Если появятся секреты Actions — только GitHub Environment secrets, никогда в коде/логах/памяти.

## Известные упрощения каркаса

- Нет кэша pnpm/pip между прогонами (первое время ок; добавить `actions/cache` или registry при замедлении).
- Образы не публикуются в registry — сборка на месте; при росте времени сборки перейти на GHCR.
