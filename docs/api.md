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

## Воронка из собранных данных в пул (цикл 1)

Внутренний контракт для доверенного источника уже собранного профиля. Эти методы
требуют **`X-Internal-Token`**, сверяемый с `CANDIDATE_PIPELINE_TOKEN`.
Токен сервисный: не отдавать его браузеру и не заменять им кандидатский JWT.
Без настройки токена — 503; отсутствующий/неверный токен — 401.
Fastify проксирует только перечисленные методы в matching-engine, с таймаутом 10 секунд.

| Метод | Путь | Контракт / результат |
|---|---|---|
| PUT | `/internal/candidates/{candidate_id}/profile` | `CandidateIntake` → `CandidatePipelineStatus` |
| GET | `/internal/candidates/{candidate_id}` | текущая ревизия, состояние, недостающие требования |
| POST | `/internal/candidates/{candidate_id}/retry` | повтор failed/retrying → queued; иначе 409 |
| GET | `/internal/strict-schema` | версия, хеш, упорядоченные координаты |
| POST | `/internal/strict-schema/coordinates` | `{group:domain\|stack\|soft_skill,key}` → новая схема и backfill |
| GET | `/internal/pool?limit=100&offset=0` | `PoolPage`: только готовые записи активной версии; limit 1–500 |
| GET | `/internal/pool/{candidate_id}/resume.pdf` | обезличенный PDF; 404 после withdrawal; `Cache-Control: no-store` |

Имена полей — snake_case; общие TS DTO в `packages/shared/src/candidate-pipeline.ts`.
Полная Pydantic-схема — OpenAPI matching-engine, включая ограничения длины и enum.
`apps/api/src/contracts/candidate-pipeline.openapi.json` **генерируется**, не редактируется вручную:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python tests/generate_candidate_contract.py
```

Обязателен монотонный `source_revision >= 1`. Повтор того же снимка с той же
ревизией идемпотентен. Меньшая ревизия либо другой снимок с прежней ревизией — 409.
PUT заменяет полный снимок; частичное заполнение допускается, но это не PATCH.
`domains`, `stacks`, `soft_skills` содержат `{key,level:0..7,status,provenance[]}`.
Статусы всех трёх групп: confirmed/partial/self_reported/pending/rejected.
Проекты, опыт и достижения: `{title,description,status,details,provenance}`;
дополнительные исходные поля сохраняются в `details`/`metadata` как JSON.

Условия очереди: непустые имя и город, подтверждённый email, зарплатные границы,
primary_direction, approved grade_result с UUID сессии и временем принятия,
оба согласия с версией и aware timestamp. `consents.accepted_at/version` относятся
к общей версии снимка обоих согласий; индивидуальный audit остаётся у источника.
Неполный профиль сохраняется как draft с чек-листом, без публикации.

Грейд уже принят кандидатом во входном потоке: pipeline не проводит тест и не
понижает грейд самостоятельно. Источник отвечает за достоверность этого результата.
Уровни и все статусы сохраняются; rejected имеет нулевую координату, а исходный
уровень остаётся в `strict_evidence`. Неподтверждённые навыки не исключаются.

Private owner PDF существует только во внутреннем matching-engine по
`GET /internal/candidates/{id}/resume.pdf`; API-core его **не проксирует**.
До появления кандидатской авторизации и проверки владельца этот PDF не подключать к UI.
Остальные internal mutation-методы предназначены только доверенной серверной интеграции.
Входная валидация Fastify возвращает 400, Pydantic/неизвестная координата — 422.
Хранилище или upstream недоступны — 503 без DSN/исходных исключений.

Контракты регистрации/подтверждения email, проведения теста, CRUD исходных claims,
кандидатского JWT/ownership и доставки outbox в комбайн пока отсутствуют и входят
в соседние этапы. В рамках этой задачи их результаты поступают через intake.
