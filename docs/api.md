# API — Comit

Статистика работодателя: `GET /api/employer/statistics?from=YYYY-MM-DD&to=YYYY-MM-DD&vacancy_id=UUID` — текущий инвентарь, этапы подбора, дневные события и предыдущий период; [контракт и определения](employer-statistics.md).

Feed поддерживает `POST /api/employer/vacancy-drafts/{draft_id}/feed/impressions`
(`FeedImpressionRequest`: exposure_id/idempotency_key) и чтение первого receipt
по `GET .../feed/impressions/{idempotency_key}`. Это клиентское подтверждение
видимости, отдельно от серверной выдачи. Rerank/Feed cards и RerankStatus добавляют
activation_revision, approval_receipt_hash и activation_kind; `manual_override`
явно обозначает исследовательский rollout при сохранённом failed gate.
Публичной активации, dataset export и обучения нет.
[Сбор карточек](real-card-learning.md), [активация](rerank-activation.md).

> Правило (ТЗ §3.5): все методы описаны через OpenAPI; эндпоинты документированы — запросы, ответы, коды ошибок.

## Живые спецификации

| Сервис | Спецификация |
|---|---|
| api-core | http://localhost:8080/openapi.json, UI: http://localhost:8080/docs (через nginx: `/api/docs`) |
| matching-engine | http://localhost:8101/docs |
| testing-engine | http://localhost:8102/docs |
| integration-engine | http://localhost:8103/docs |

## Текущие эндпоинты

### api-core (`apps/api`)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/healthz` | liveness: `{status, service, version}` |
| GET | `/ready` | пока статический ответ `{status, service, version}` без проверки БД |

Аутентификация уже подключена в `apps/api`: пароль не используется. Код на email
ставится в PostgreSQL outbox, а после успешной проверки создаются собственный HS256
JWT и отзываемая DB-сессия. Нормализованный email глобально принадлежит ровно одному
аккаунту и одной неизменяемой роли (`candidate` или `employer`).

| Метод | Путь | Контракт / результат |
|---|---|---|
| POST | `/auth/{role}/code` | `{email}` → 202 `{challenge_id,expires_at,resend_after}`; 202 означает постановку отправки |
| POST | `/auth/{role}/verify` | `{email,challenge_id,code,consent?}` → аккаунт, Bearer JWT, 7-дневный expiry и role cookie |
| GET | `/auth/{role}/me` | текущий аккаунт из JWT/cookie |
| POST | `/auth/{role}/logout` | отзыв текущей DB-сессии и очистка cookie |
| POST | `/auth/{role}/logout-all` | отзыв всех DB-сессий аккаунта |

Первый аккаунт требует versioned consent на обработку аккаунта; consent на публикацию
профиля в пуле остаётся отдельным условием candidate pipeline. Обязательное согласие
не превращает регистрацию в публикацию профиля.

### Candidate source profile

Профиль кандидата хранится в API-core как private account-owned source. Браузерные
`GET/PUT/PATCH /candidate/profile` требуют candidate JWT или role cookie. PUT
заменяет editable snapshot, PATCH применяет shallow patch; оба метода принимают
`expected_revision` и возвращают `409 profile_revision_conflict` при устаревшей
ревизии. Нулевая ревизия создаёт профиль. Поля proof, grade result, candidate ID,
email и серверное время согласия запрещены в mutation.

Editable narrative claims получают стабильный UUID на сервере. `facts` (домены,
стеки, длительность, роль, production support) — структурированная декларация,
а ссылка сама по себе не является evidence. Evidence возвращается отдельно и
содержит canonical input SHA-256, status, verified value, provenance и
`checked_at`; кандидат не может подменить этот канал.

Профильное согласие использует `profile-v1`; клиент передаёт processing/pool/version,
а `accepted_at` ставится сервером транзакционно. Account consent и pool consent
не смешиваются. `POST /candidate/profile/projection/retry` повторяет только
локальную terminal `failed` projection. `GET /candidate/pipeline` и
`GET /candidate/resume.pdf` используют account ID из сессии. `GET /candidate/taxonomy`
отдаёт кандидату текущую strict-schema (`StrictSchema`) из matching-engine.

Browser profile projection выдаёт `self_reported` без выдуманного UUID тестовой
сессии. Контракт предусматривает будущий trusted producer, который заменит его на
legacy `approved` с реальными `test_session_id` и `approved_at`; этот producer и
его replacement contour текущим browser/API проходом не реализованы. Будущий
producer обязан обновить authoritative evidence API и enqueue новый exact snapshot;
прямой approved intake в matching не заменяет source. Self reported
grade допускается в пул при выполнении остальных требований, получает badge в
public PDF, а в policy `main-v2` confidence `0.6` уменьшает только его rating
contribution. Pending/rejected grade остаются блокирующими.
Готовая выдача проверяется против текущего policy fingerprint; смена policy
делает старый run obsolete до нового отбора. Ту же проверку использует реранкер.

### testing-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/blueprints` | список blueprint'ов (шаблонов заданий) |
| GET | `/blueprints/{id}` | один blueprint; 404 если нет |

### integration-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/fsp/participants/{fsp_id}` | профиль участника ФСП из мок-реестра; **404** = истории нет (обязательный случай по ТЗ — не ошибка платформы) |
| POST | `/fsp/verify` | сверка заявленного рейтинга с реестром: `{fsp_id, claimed_rating}` → `{fsp_id, verified, actual_rating}`; нет в реестре → `verified=false`, `actual_rating=null` |

## Правила контрактов

- Новые эндпоинты регистрируются в этом файле в том же коммите.
- Ответы типизируются схемой Fastify (api) / pydantic (engines) — схема = документация.
- Коды ошибок: 400 (валидация), 401/403 (роли), 404 (нет ресурса), 409 (конфликт статусов), 422 (pydantic).
- Авторизация: `Authorization: Bearer <JWT>` или HttpOnly role cookie; claims JWT
  Keycloak-совместимы (`sub`, `sid`, `roles`, `email_verified`, `iss`, `aud`, `iat`, `exp`).
  Сессия дополнительно проверяется по hash `sid` в PostgreSQL и может быть отозвана.

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
primary_direction, `approved` или `self_reported` grade_result, оба согласия с
версией и aware timestamp. Approved требует UUID сессии и время принятия;
self_reported не получает выдуманный UUID. `consents.accepted_at/version` относятся
к общей версии снимка обоих согласий; индивидуальный audit остаётся у источника.
Неполный профиль сохраняется как draft с чек-листом, без публикации.

Грейд уже принят кандидатом во входном потоке: pipeline не проводит тест и не
понижает грейд самостоятельно. Источник отвечает за достоверность этого результата.
Уровни и все статусы сохраняются; rejected имеет нулевую координату, а исходный
уровень остаётся в `strict_evidence`. Неподтверждённые навыки не исключаются.

Private owner PDF существует только во внутреннем matching-engine по
`GET /internal/candidates/{id}/resume.pdf`; public API проксирует его только для
аутентифицированного кандидата и подставляет `account.id`, игнорируя чужой ID.
Остальные internal mutation-методы предназначены только доверенной серверной интеграции.
Входная валидация Fastify возвращает 400, Pydantic/неизвестная координата — 422.
Хранилище или upstream недоступны — 503 без DSN/исходных исключений.

Регистрация/подтверждение email, candidate JWT/ownership и source-profile CRUD
разделены с matching projection. Проведение теста остаётся trusted upstream
контуром; API не принимает от браузера `grade_result` или evidence и не делает
самоотчёт подтверждённым.

## Подготовка вакансии к комбайну (цикл 1)

Та же серверная граница `X-Internal-Token` действует для matching-engine. Employer
JWT и проверка владельца в public gateway реализованы для draft/agent paths, где
`X-Owner-ID` сервер устанавливается из `account.id`; internal методы по-прежнему
доступны только доверенному источнику.

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
Outbox vacancy.ready/withdrawn сохраняется атомарно и доставляется combine-worker.

## Структурированные условия кандидата для strict-фильтра

`CandidateProfile` дополнен `work_types` (список onsite/remote/hybrid или null),
`relocation_ready` (строгий bool или null), `relocation_cities` (список городов).
Null означает неизвестно, пустой work_types — ни один формат не принят.
Relocation_ready=true с пустым списком допускает любой город; непустой список
ограничивает направления переезда. Эти поля не выводятся из preferences/bio.
Они не меняют eligibility исходного пула, но комбайн исключает неизвестный формат.
Новая `city` вакансии обязательна для ready любого формата. Legacy ready без
города отзывается миграцией 003; исходные данные сохраняются как draft.

## Основной отбор и доставка событий

Все internal-маршруты защищены тем же `X-Internal-Token` и опубликованы в OpenAPI
FastAPI/Fastify; DTO находятся в `packages/shared/src/matching.ts`. Public employer
gateway требует employer JWT/session, проверяет owner draft и затем передаёт
server-derived `X-Owner-ID` upstream. Чужой draft, отсутствующий draft или
matching vacancy возвращают единый 404 owner boundary.

| Метод и путь | Назначение |
|---|---|
| POST `/internal/vacancies/{id}/combine` | `{top_k?:1..500,idempotency_key?:UUID}` → CombineStatus |
| GET `/internal/vacancies/{id}/combine` | текущий run, стадия main_selection и состояние |
| POST `/internal/vacancies/{id}/combine/retry` | повтор failed/retrying либо обновление obsolete |
| GET `/internal/vacancies/{id}/selections?limit=100&offset=0` | SelectionPage только одного актуального ready run |
| PUT `/internal/vacancies/{id}/selection-state/{candidate_id}` | `{state:eligible\|visited\|pending\|attractive}` → SelectionState |
| GET `/internal/matching/events` | DeliveryStatus: pending обоих streams, ACK, queued, retry/quarantine |
| POST `/internal/matching/events/retry` | `{source:candidate\|vacancy,event_id:positive integer}` → DeliveryStatus |

Для работодателя те же операции доступны только после `created` + accepted
publication draft:

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/employer/vacancy-drafts/{draft_id}/ready` | актуальный ready snapshot vacancy |
| POST | `/employer/vacancy-drafts/{draft_id}/combine` | queue main selection |
| GET | `/employer/vacancy-drafts/{draft_id}/combine` | durable combine status |
| POST | `/employer/vacancy-drafts/{draft_id}/combine/retry` | retry failed/obsolete run |
| GET | `/employer/vacancy-drafts/{draft_id}/selections` | owner-scoped current selection page |
| GET | `/employer/vacancy-drafts/{draft_id}/candidates/{candidate_id}/resume.pdf` | PDF выбранного кандидата |

Selection gateway повторно сверяет текущий source revision, consent и выбранный
candidate перед выдачей каждой карточки/PDF. Падение projection кандидата вне
запрошенной страницы не блокирует её; устаревший source watermark хотя бы одной
карточки запрошенной страницы даёт 409 до пересборки, а
отсутствующий кандидат в текущем selection — 404. `ACK` source outbox означает
только доставку snapshot, не готовность vectors/PDF/pool.

Top_k по умолчанию 50; ручной idempotency_key сохраняет исходный run,
повтор с другими vacancy/top_k → 409. Автоматический consumer использует ту же
очередь и настройки. State queued/processing/retrying/failed/ready/obsolete —
технический статус, не бизнес-статус вакансии. ACK события не означает ready.

Карточка содержит анонимное имя, city, зарплату, category/rank, public PDF URL,
положительные filter_factors и числовые explanation (raw rating, grade_status,
grade confidence, strict/semantic, top matched features со статусами подтверждения).
Контактов и private profile нет.
Нет готовой вакансии → 404; нет актуального завершённого batch → 409.
Изменения входов сразу закрывают чтение старых карточек до нового отбора.
Visited необратим; pending/attractive можно явно вернуть в eligible.

## Baseline rerank (internal token)

`POST /internal/vacancies/{id}/rerank` принимает необязательный UUID
`idempotency_key`; `GET` того же пути возвращает durable статус.
`POST /internal/vacancies/{id}/rerank/retry` повторяет терминальное задание.
`GET /internal/vacancies/{id}/rerank/selections?kind=recommended|reserve&limit=100&offset=0`
возвращает отдельную страницу выбранного режима из одного актуального запуска.
Нет актуального main/готового rerank →409; неготовая/закрытая вакансия →404.

Карточка сохраняет исходные explanation и `main_position`; добавляет
`rerank_position`, nullable `rerank_score`, вклады признаков, disposition и
`artifact_hash`. Статус содержит feature/tail hashes, `mode`, `fallback_reason`,
число рекомендованных, резерва и оставшихся вне top-k. При fallback нет обученного
score, rerank_position=null, весь main top-k остаётся рекомендованным.
Обучение и активация доступны только operator CLI, без HTTP endpoint.

## Создание вакансии: форма и агент

Контракт `agents-service` генерируется из FastAPI в
`apps/api/src/contracts/agents.openapi.json` и `packages/shared/src/agents.ts`
командой `PYTHONPATH=services/agents-service/src python tests/generate_agents_contract.py`.
Fastify использует тот же OpenAPI для allowlist, body/response/UUID schemas.
Все нижеперечисленные internal-пути требуют `X-Internal-Token=AGENTS_SERVICE_TOKEN`
и `X-Owner-ID=UUID`. Public `/employer/*` gateway получает UUID из проверенного JWT/
cookie и сам передаёт его upstream; клиентский owner header игнорируется. Это не
заменяет internal token. Реквизиты владельца не принимаются из JSON; чужая запись
возвращает 404.

| Метод и путь | Тело/результат |
|---|---|
| GET `/internal/agents/taxonomy` | Три группы действующего строгого реестра |
| POST `/internal/vacancy-drafts` | `{fields?: DraftFields}` → DraftView |
| GET `/internal/vacancy-drafts?limit=50&offset=0` | Owner-scoped DraftView[], limit 1–100 |
| GET `/internal/vacancy-drafts/{id}` | Общий черновик, provenance, missing, summary, publication |
| PATCH `/internal/vacancy-drafts/{id}` | `{expected_revision,fields}`: частичный патч, группы заменяются целиком |
| POST `/internal/vacancy-drafts/{id}/enrich` | `{expected_revision}` → JobView; только форма/Laya |
| POST `/internal/vacancy-drafts/{id}/approve` | `{expected_revision}` → DraftView в summary |
| POST `/internal/vacancy-drafts/{id}/create` | `{expected_revision,summary_hash}` → создаваемая/созданная публикация |
| GET `/internal/vacancy-drafts/{id}/pipeline` | MatchingStatus после принятия intake |
| GET `/internal/agents/jobs/{id}` | Durable job, попытки/состояние/безопасная ошибка |
| POST `/internal/agents/jobs/{id}/retry` | JobView; failed/retrying, сохранённые эффекты не повторяются |
| POST `/internal/agents/sessions` | `{draft_id}` → AgentSession |
| GET `/internal/agents/sessions/{id}` | Сессия, последний turn, актуальный черновик, сообщения и интеракции |
| POST `/internal/agents/sessions/{id}/turns` | `{expected_revision,idempotency_key,message}` → JobView |
| POST `/internal/agents/sessions/{id}/interactions/{interaction_id}` | `{expected_revision,value}` → continuation JobView |

`approve` доступен форме и агенту без дополнительного подтверждения и не публикует
вакансию. Create подтверждает точный summary. Любая правка до Create удаляет старый
summary. Обязательны title, zp_min/zp_max, daily, city, type и class **или** domens/stacks;
bio/soft skills необязательны. Уровни 0–7, неизвестный ключ → отказ. Class aliases
`back/front/ML`, форматы `очно/дистант/гибрид` нормализуются в общие enum.
После Create данные заморожены; повтор с тем же hash/revision идемпотентен.

Agent interactions содержат `draft_revision`, typed option values, `field_target`:
поле или `domens.key|stacks.key|soft_skills.key`. Slider меняет одну координату;
multi-select группы объединяет выбранные непересекающиеся объекты `{key:level}`.
Устаревший контрол не перезаписывает новую ручную правку. Опции валидируются сервером;
повтор уже принятого ответа с тем же значением возвращает тот же continuation job.

401 — нет доверенного токена; 400/422 — неверный контракт; 404 — нет записи данного
владельца; 409 — CAS/state/hash/schema конфликт; 503 — недоступная зависимость.
Технические ошибки inference передаются через job state и безопасный error code.
Auth/JWT/accounts и owner-scoped draft gateway реализованы. UI, public matching
gateway routes and CRUD опубликованной вакансии остаются отдельными этапами;
описанные выше employer selection routes уже owner-scoped и проверяют актуальность
источников.
Подробности: [agents.md](agents.md).


## Лента работодателя и история решений

Контур и область наборов описаны в [постановке](../context/51-reranker-and-feed-learning.md#контур-выдачи-и-решений-работодателя--уточнение-06102026).
Порция фиксирует готовый top-k реранкера; like сохраняет кандидата, dislike
создаёт структурный снимок и блокирует новый подбор для этой вакансии.
`visited_updated` сохраняет историю и снимает блокировку только при N > K.
Политика и снимок последнего dislike неизменяемы; новая карточка после достаточного
обновления несёт метаданные прежнего просмотра и объяснение изменений.
Текст, контакты и изменения порядка массивов не участвуют в метрике.


Публичные маршруты под `/api/employer/vacancy-drafts/{draft_id}` требуют сессию
работодателя и опубликованную собственную вакансию:

| Метод и суффикс | Назначение |
|---|---|
| POST `/feed` | Открыть порцию; `{include_reserve:false, limit:50}` |
| GET `/feed/{session_id}?cursor=...&limit=50` | Продолжить фиксированную порцию |
| GET `/feed/exposures/{exposure_id}` | Проверить конкретный показ |
| GET `/feed/exposures/{exposure_id}/resume.pdf` | Получить redacted PDF с проверкой ревизии и согласия до и после чтения |
| POST `/feed/decisions` | `{exposure_id, action:"like"|"dislike", idempotency_key}` |
| GET `/feed/decisions/{idempotency_key}` | Прочитать сохранённую квитанцию |
| GET `/saved?limit=50&offset=0` | Набор оставленных кандидатов этой вакансии |
| GET `/visited?limit=50&offset=0` | История просмотренных с текущим состоянием блокировки |

Приватный matching API использует аналогичные суффиксы под
`/internal/vacancies/{vacancy_id}` и требует одновременно service token и
`X-Owner-ID`. Клиентский owner header не определяет владельца в публичном API.
Идемпотентность имеет область owner + vacancy + key; другой payload для того же
ключа и повторное противоположное действие на один показ возвращают409.
Курсор непрозрачен: клиент передаёт `next_cursor` без самостоятельной арифметики.
Новая порция из неготового или устаревшего реранкера возвращает409.

Порция и новые реакции действуют 24 часа. Сохранённый набор живёт независимо
от этого срока: PDF оставленной карточки доступен через её исходный exposure
при актуальной ревизии, наличии в пуле и согласии кандидата. При обновлении или
отзыве профиля запись остаётся в наборе, но устаревшая карточка возвращается
как `card:null`, а PDF блокируется. Сохранение не открывает контакты кандидата.

## Backend после like

Контракт и state/consent matrix: [recruitment](recruitment.md). Все маршруты
под `/api`, с существующим role-bound JWT/cookie, ownership из сессии.

| Метод / маршрут | Request → Response |
|---|---|
| POST `/employer/vacancy-drafts/{draft_id}/invitations` | InviteRequest → RecruitmentView |
| POST `/employer/vacancy-drafts/{draft_id}/recruitment/reject` | RejectSavedRequest → employer-only RecruitmentView |
| GET `/employer/vacancy-drafts/{draft_id}/recruitment` | limit/offset → RecruitmentPage |
| GET `/candidate/recruitment` | limit/offset → own RecruitmentPage |
| GET `/{role}/recruitment/{id}` | own RecruitmentView |
| POST `/{role}/recruitment/{id}/actions` | RecruitmentAction → RecruitmentView |
| GET `/employer/recruitment/{id}/contacts` | accepted/current consent → ContactsView |
| GET `/{role}/recruitment-events` | after_sequence/limit → RecruitmentEventPage |

Action union: view/accept/decline/withdraw/reject, interview_propose/accept/decline/
cancel/complete, offer_send/accept/decline. CAS expected_revision и UUID key
обязательны, 409 на stale revision/conflicting key/state. Receipt повтора
не содержит контактов и не обещает текущее состояние. Контакты читаются отдельно
с no-store, до принятия403, чужой процесс404, нетprocessingconsent409.
Saved-членство для invitation/rejection читается через приватный matching
`GET /internal/vacancies/{vacancy_id}/saved/{candidate_id}` с token+serverowner;
он возвращает SavedCard, при stale источнике card:null, без удаления истории.
Без настройки чата chat_available:false. Схемы всех маршрутов включены в Fastify
OpenAPI; DTO живут в packages/shared/recruitment.ts и используются api-client.

## QGramm chat backend

Симметричные role-auth маршруты `/{role}/recruitment/{id}/chat/messages`
(GET history, POST SendChatMessage), `/files` (POST UploadChatFile) и
`/files/{file_id}` (GET DownloadChatFile) описаны в Fastify OpenAPI.
DTO в `packages/shared/chat.ts`, типизированные методы в api-client.
Участники и комната определяются API из приглашения, не из body/header клиента.
Чат доступен до accept, контакты — по прежнему правилу после accept.
[Лимиты, ошибки, курсор и протокол публикации файлов](chat.md).

## Управление предпочтениями вакансии

В matching добавлены owner-scoped `GET /internal/vacancies/{vacancy_id}/preferences`
и `POST .../preferences/{retry,reset,rollback}`. Token и X-Owner-ID обязательны.
Штатный employer gateway публикует их как
`/api/employer/vacancy-drafts/{draft_id}/preferences...`, выводя owner из session.
POST принимает `PreferenceCommand`: UUID idempotency_key и expected_revision.
Статус содержит счётчики/worker job/model hash и явную оговорку selection bias,
без feature/model payloads. Reset вводит cutoff; rollback выключает обучение.
409 означает конфликт CAS/контекста, 404 — отсутствие собственной вакансии.

FeedCard дополнен preference revision/model hash/score/adjustment/contributions,
preference position/state и delivery order revision; FeedPage содержит текущий
order_revision. Shared, generated engine OpenAPI, gateway и api-client обновлены
синхронно. [Доставка и управление](preference-learning.md#доставка-и-управление).

## Проверка гипотез вакансии

GET `/api/employer/vacancy-drafts/{draft_id}/hypotheses` и POST `.../hypotheses/{pause|resume|reset}` проксируют owner-scoped matching контракты; тело команд — `PreferenceCommand` (UUID/CAS). `HypothesisStatus` содержит10 `HypothesisSummary`, context/policy/revision и bounded search report без участников/фактов. `FeedCard` дополнен optional pair/family/role/parameters, conditional pair selection/order probabilities, scope и exploration_added. Старые карточки имеют null/default значения. Вероятности не называются global propensity. [Семантика](hypothesis-learning.md).

Общая витрина работодателя добавляет POST `/employer/candidates/search`,
GET `/employer/candidates/:candidate_id`, публичный PDF точной ревизии и
POST `/employer/vacancy-drafts/:draft_id/showcase-likes`. Поиск доступен без
вакансии; лайк требует собственной ready/open вакансии. Несоответствия
строгим условиям возвращают структурированное предупреждение409; повтор с
source-bound mismatch_token разрешает ручной выбор. Source/ownership guards
не допускают override.
SearchRequest/Page, PublicCandidateDetail и ShowcaseLikeRequest/Receipt
синхронизированы в Pydantic, generated OpenAPI, shared и api-client.
[Поля, фильтры, cursor, повторы и выбор вакансии](candidate-showcase.md).

## Отдельное согласие на обучение (2026-10-07)

CandidateProfileMutation.consent дополняется optional `training:boolean` и
`training_version:"training-v1"`. Разрешение не подразумевается из pool/processing;
старый клиент без новых полей не включает обучение. True без правильной версии
или processing/pool отклоняется с 400. False передаётся без training_version.
CandidateProfileView.consents возвращает training, training_version/null и
training_accepted_at/null. Internal Consent содержит эти же поля; устаревшие
снимки получают training=false. OpenAPI gateway перегенерирован из FastAPI.
Отказ не исключает кандидата из подбора; source watermark по текущей ревизии
применяется при каждом изменении профиля, как и прежде.

[Документы для тестового контура](legal/README.md).

[Проверка кандидатского backend и воспроизведение результатов](candidate-backend-audit.md).
# Внешняя верификация

Кандидатские `POST /candidate/profile/verifications/fsp`, `/repository` и `/hh` требуют authenticated candidate session и CAS `expected_revision`; account id не передаётся provider-сервису. Demo FSP flow: `POST /candidate/profile/fsp/demo/authorize` → GET/POST integration `/fsp/demo/authorize` → `POST /candidate/profile/fsp/demo/callback`; state candidate-bound, 120 секунд, callback allowlist и code одноразовые. Callback bulk-upsert-ит fixture achievements и binding `fsp_id`/`fsp_claim_id`. Account identity и ownership не выводятся из demo registry. Facts возвращаются в private profile view и доставляются matching через source outbox. Контракт провайдеров и деградация HH описаны в [verification.md](verification.md).

## Глобальный агент работодателя

Публичные account-scoped маршруты находятся под `/employer/organization-agent`;
gateway получает владельца из employer session. Внутренние маршруты имеют
префикс `/internal/organization-agent` и требуют `AGENTS_SERVICE_TOKEN`.
Pydantic является источником DTO; `tests/generate_agents_contract.py` обновляет
shared и OpenAPI, которые использует gateway. [Операционный контракт](agents.md).

Файлы загружаются в `/documents` и читаются по ID; base64-транспорт ограничен
7 MiB на gateway и 5 MiB исходных байтов. TXT/MD/PDF никогда не становятся
публичными файлами кандидатов. Business bridge
`POST /internal/organization-agent/tools/{tool}` принимает только доверенный
сервисный вызов с owner scope и не выдаёт права принять интервью за кандидата.
