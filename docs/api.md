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

## Подготовка вакансии к комбайну (цикл 1)

Та же серверная граница `X-Internal-Token`; employer JWT и проверка владельца
в браузерном API ещё не реализованы. Internal методы доступны доверенному источнику.

| Метод | Путь | Контракт / результат |
|---|---|---|
| PUT | `/internal/vacancies/{vacancy_id}` | полный `VacancyInput` → `VacancyPipelineStatus` |
| GET | `/internal/vacancies/{vacancy_id}` | бизнес-статус, состояние обработки, checklist, безопасный error_code |
| POST | `/internal/vacancies/{vacancy_id}/retry` | повтор failed/retrying; иначе 409 |
| GET | `/internal/vacancies/ready?limit=100&offset=0` | `VacancyReadyPage`, только актуальные готовые снимки |
| GET | `/internal/vacancies/{vacancy_id}/ready` | `VacancyReady`; 404 до готовности или после отзыва |

TS DTO: `packages/shared/src/vacancy-pipeline.ts`; OpenAPI генерируется той же
командой выше. `source_revision` обязателен, целое 1…9007199254740991
(Number.MAX_SAFE_INTEGER); зарплаты ограничены тем же максимумом. Это сохраняет
точность JSON при прохождении JavaScript-шлюза. Все остальные поля
можно не передать при сохранении неполного снимка. `owner_id` — UUID; после
назначения его нельзя менять/удалять. Правила ревизий и идемпотентности как у кандидата.
Исходные wire значения сохраняются в raw_input, канонические — в profile.

Готовность требует owner_id, непустой city (включая remote), zp_min и zp_max (целые рубли ≥0, min≤max),
непустой daily, type, status=open и хотя бы class **или** непустой domens/stacks.
Техническое state=ready выдаётся только после успешной сборки артефактов.
Бизнес-статусы: draft/open/pending/closed/done; переходы задаёт источник.
Type: onsite/remote/hybrid; принимаются очно/дистант/гибрид. Class — общий
справочник направлений, включая самостоятельный ml; aliases back/front/ML.

Domens, stacks и soft_skills — словари `{ключ: целое 0…7}`. Ключ должен быть
из соответствующей группы общего реестра strict-schema; неизвестный — 422.
Bio (до 10000 символов) и любые группы могут быть пустыми. Входной bio=null
нормализуется в пустую строку; исходный null остаётся в raw_input. Soft-only и bio-only
сохраняются с checklist, но не квалифицируют вакансию для комбайна.
Metadata — JSON, до 100 ключей и 64 KiB. Неизвестные верхнеуровневые поля,
bool вместо числового уровня и выход за диапазон отклоняются.

Ready содержит все канонические данные, revision/source_revision,
build_mode, strict_vector, requirement_mask, strict schema version/hash,
nullable semantic_vector/model_version/space_id и semantic_available.
Пустой bio означает явно отсутствующий мягкий вектор; class-only остаётся допустим.
Если bio задан, ошибка Harrier приводит к retry/failed, а не к потере текста.
Изменение любых данных или уход из open немедленно отзывает старый ready.
Outbox vacancy.ready/withdrawn сохраняется атомарно; его доставка и сам комбайн
относятся к следующему этапу.

## Структурированные условия кандидата для strict-фильтра

`CandidateProfile` дополнен `work_types` (список onsite/remote/hybrid или null),
`relocation_ready` (строгий bool или null), `relocation_cities` (список городов).
Null означает неизвестно, пустой work_types — ни один формат не принят.
Relocation_ready=true с пустым списком допускает любой город; непустой список
ограничивает направления переезда. Эти поля не выводятся из preferences/bio.
Они не меняют eligibility исходного пула, но комбайн исключает неизвестный формат.
Новая `city` вакансии обязательна для ready любого формата. Legacy ready без
города отзывается миграцией 003; исходные данные сохраняются как draft.
