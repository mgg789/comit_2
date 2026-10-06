# Deploy — mgg-server

> Решение 2026-10-05: деплой в `/home/mgg/comit` под пользователем `mgg` (без sudo), сам сборщик — deploy-workflow на self-hosted раннере.

## Схема

```
push main ──► deploy.yml (runner mgg-ci-*, контейнер docker на сервере)
                ├─ cp /home/mgg/comit/.env .env  (+ *_PORT из него в env smoke)
                ├─ docker compose -p comit --env-file .env -f infra/docker-compose.prod.yml up -d --build
                ├─ ./tests/smoke.sh  (порты из серверного .env)
                └─ docker image prune -f
```

- Стек живёт в Docker с `COMPOSE_PROJECT_NAME=comit` (имена `comit-web-1`, `comit-api-1`, ...), checkout раннера — временный, контейнеры от него не зависят.
- Секреты: `/home/mgg/comit/.env` (создаётся один раз вручную по `.env.example`; `POSTGRES_PASSWORD` и `JWT_SECRET` обязательны — prod compose падает без них намеренно).
- Порты на сервере (из `/home/mgg/comit/.env`, задеваем чужие): web `8088`, api `28080`, matching `28101`, testing `8102`, integration `8103` — 8080/8101 на хосте заняты другими контейнерами (kan-web и пр.). Deploy-workflow подхватывает эти порты в smoke автоматически. Наружу публикуется только web; api и engines в prod compose привязаны к `127.0.0.1`, postgres не публикуется.

## Операции

| Действие | Команда (на mgg-server) |
|---|---|
| Статус | `docker compose -f /home/mgg/comit/infra/docker-compose.prod.yml ps` — точнее: `docker ps --filter name=comit-` |
| Логи | `docker logs -f comit-web-1` / `comit-api-1` / ... |
| Рестарт сервиса | `docker compose -p comit -f /home/mgg/comit/infra/docker-compose.prod.yml restart api` |
| Полная пересборка | push в `main` или `gh workflow run deploy.yml` |
| Откат | `git checkout <prev-tag/commit>` на main + повторный deploy |

## Правила

- Интерактивные операции на сервере — только по явной команде пользователя (AGENTS §13).
- `down -v` (снос volumes) на prod — запрещён без явной команды: там данные демо.
- Деплой обязан заканчиваться зелёным smoke — красный деплой откатывается.
