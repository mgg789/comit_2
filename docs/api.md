# API — Comit

> Правило (ТЗ §3.5): все методы описаны через OpenAPI; эндпоинты документированы — запросы, ответы, коды ошибок.

## Живые спецификации

| Сервис | Спецификация |
|---|---|
| api-core | http://localhost:8080/openapi.json, UI: http://localhost:8080/docs (через nginx: `/api/docs`) |
| matching-engine | http://localhost:8101/docs |
| testing-engine | http://localhost:8102/docs |
| integration-engine | http://localhost:8103/docs |

## Текущие эндпоинты (каркас)

### api-core (`apps/api`)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/healthz` | liveness: `{status, service, version}` |
| GET | `/ready` | readiness (проверка БД подключается с миграциями) |

### testing-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/blueprints` | список blueprint'ов (шаблонов заданий) |
| GET | `/blueprints/{id}` | один blueprint; 404 если нет |

### integration-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/fsp/participants/{fsp_id}` | профиль участника ФСП из мок-реестра; **404** = истории нет (обязательный случай по ТЗ — не ошибка платформы) |
| POST | `/fsp/verify` | сверка заявленного рейтинга с реестром: `{fsp_id, claimed_rating}` → `{verified, actual_rating}` |

## Правила контрактов

- Новые эндпоинты регистрируются в этом файле в том же коммите.
- Ответы типизируются схемой Fastify (api) / pydantic (engines) — схема = документация.
- Коды ошибок: 400 (валидация), 401/403 (роли), 404 (нет ресурса), 409 (конфликт статусов), 422 (pydantic).
- Авторизация: `Authorization: Bearer <JWT>`; структура claims — Keycloak-совместимая (context/80, Q7).

## Дорожная карта API (S1–S4)

Auth (register/verify/login) → профиль и верификация полей → опрос + грейд-тест → вакансии → Selection с explanation factors → тиндер-фидбек → приглашения и статусы → пост-отбор.
