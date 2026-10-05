# Runbook — типовые операции

## Локально

| Задача | Команда |
|---|---|
| Поднять стек | `make up` |
| Smoke-гейт | `make smoke` |
| Логи | `make logs` |
| Остановить и снести | `make down` |
| Пересобрать всё | `make build` |
| JS typecheck/build | `pnpm -r typecheck && pnpm -r build` |
| Python-тесты | `pip install -e 'services/matching-engine[dev]' -e 'services/testing-engine[dev]' -e 'services/integration-engine[dev]' && pytest services/*/tests` |
| Dev-сервер одного аппа | `pnpm --filter @comit/web-candidate dev` |
| OpenAPI api-core | http://localhost:8080/docs (или `/api/docs` через nginx) |

## Сервер (mgg-server)

| Задача | Команда |
|---|---|
| Статус раннеров | `systemctl --user status github-runner@mgg-ci-1` |
| Логи раннера | `journalctl --user -u github-runner@mgg-ci-1 -f` |
| Статус стека | `docker ps --filter name=comit-` |
| Логи сервиса | `docker logs -f comit-api-1` |
| Ручной deploy | `gh workflow run deploy.yml --repo mgg789/comit_2` |

## Типовые проблемы

| Симптом | Причина / решение |
|---|---|
| `make smoke` красный по web | nginx отдаёт 404 на `/candidate/` — проверь, что `pnpm -r build` собрал все аппы (Dockerfile собирает 4) |
| Контейнер engines перезапускается | смотри `docker logs`; чаще всего отсутствие `DATABASE_URL`/порта |
| CI-джоба висит в `queued` | раннеры заняты или офлайн: `gh api repos/mgg789/comit_2/actions/runners` |
| Smoke конфликтует с деплоем | один `smoke-host` concurrency на все прогоны; не убирай его |
| 409/странные порты на сервере | занят `WEB_PORT`: смени в `/home/mgg/comit/.env` и redeploy |
