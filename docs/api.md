# API — Comit

`GET /api/employer/vacancy-drafts` и `GET /api/employer/vacancy-drafts/{draft_id}`
возвращают в `AgentsDraftView` nullable ISO-8601 `created_at`, `published_at`,
`closed_at`. Даты отражают реальные события жизненного цикла; отсутствующая
историческая дата возвращается как `null`, а не подменяется временем редактирования.

Статистика работодателя: `GET /api/employer/statistics?from=YYYY-MM-DD&to=YYYY-MM-DD&vacancy_id=UUID` — текущий инвентарь, этапы подбора, дневные события и предыдущий период; [контракт и определения](employer-statistics.md).

Feed поддерживает `POST /api/employer/vacancy-drafts/{draft_id}/feed/impressions`
(`FeedImpressionRequest`: exposure_id/idempotency_key) и чтение первого receipt
по `GET .../feed/impressions/{idempotency_key}`. Это клиентское подтверждение
видимости, отдельно от серверной выдачи. Rerank/Feed cards и RerankStatus добавляют
activation_revision, approval_receipt_hash и activation_kind; `manual_override`
явно обозначает исследовательский rollout при сохранённом failed gate.
Активация, dataset export и обучение выполняются операторскими CLI-процессами, отдельно от публичного API.
[Сбор карточек](real-card-learning.md), [активация](rerank-activation.md).

> Правило (ТЗ §3.5): все методы описаны через OpenAPI; эндпоинты документированы — запросы, ответы, коды ошибок.

## Письмо входа

Письмо OTP передаётся SMTP как multipart text/html: текстовый fallback сохраняет
роль и абсолютный срок действия UTC; HTML повторяет макет Figma `325:15652` с
динамическим шестизначным кодом, включая ведущие нули. Логотип и фон встроены как
PNG MIME parts с CID; внешние адреса изображений и tracking links отсутствуют.
Табличная разметка, inline CSS и сплошные фоновые цвета обеспечивают fallback для
клиентов без градиентов, фоновых картинок и пользовательских шрифтов. `pnpm
--filter @comit/api build` копирует bundled assets в `dist/auth/email-assets`.
Обязательные TLS, timeouts и безопасные категории SMTP failures не изменены.

## Подготовка ленты

Работодатель читает подготовку опубликованной вакансии по
`GET /api/employer/vacancy-drafts/{id}/pipeline` (существующий `AgentsMatchingStatus`,
upstream `GET /internal/vacancy-drafts/{id}/pipeline`) и повторяет её через
`POST .../retry` (`VacancyPipelineStatus` matching-engine). Статус
rerank доступен через `GET .../rerank` (`RerankStatus`), явное восстановление —
`POST .../rerank/retry`. Gateway проверяет сессию и принадлежность вакансии;
подробности автоматического пополнения приведены в [matching](matching.md).

## Живые спецификации

| Сервис | Спецификация |
|---|---|
| api-core | http://localhost:28080/docs/json (OpenAPI JSON), UI: http://localhost:28080/docs (через nginx: `/api/docs`) |
| matching-engine | http://localhost:28101/docs |
| testing-engine | http://localhost:8102/docs |
| integration-engine | http://localhost:8103/docs |
| agents-service | http://localhost:28104/docs |
| verification-engine | http://localhost:28105/docs |

Порты — host-порты по умолчанию (`API_PORT`, `MATCHING_PORT`, `TESTING_PORT`,
`INTEGRATION_PORT`, `AGENTS_PORT`, `VERIFICATION_PORT`, только loopback); внутри
compose сервисы слушают 8080/8101/8102/8103/8104/8105. Полный граф api-core —
249 операций (174 публичных и 75 `/internal/*`, без `/docs`) при настроенной
консоли поддержки; без `SUPPORT_EMAIL`/`SUPPORT_ACCESS_KEY` маршрутов `/support/*`
нет и операций на 7 меньше. Полный перечень — [api-reference.md](api-reference.md).

## Текущие эндпоинты

### api-core (`apps/api`)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/healthz` | liveness: `{status, service, version}` |
| GET | `/ready` | `{status:"ok", service, version}` только при валидной auth-конфигурации, доступной БД и применённых миграциях `021_support_console.sql`, `021_weekly_challenges.sql`, `022_yandex_oauth.sql` и `023_system_chat_attachments.sql`; иначе 503 `{status:"unavailable"}` |
| GET | `/metrics` | Prometheus-метрики процесса ([api-operations.md](api-operations.md)) |

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
| DELETE | `/candidate/account` | окончательное удаление authenticated candidate account и его private graph |

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

`fsp_id`/`fsp_claim_id` — server-managed поля: mutation профиля их отклоняет
(`server_managed_field`). ФСП ID привязывается только демо-контуром
`POST /candidate/profile/fsp/demo/authorize` → `/candidate/profile/fsp/demo/callback`;
импортированные достижения реестра получают `confirmed`, сама привязка — evidence
`partial` (демо-реестр). Отсутствие истории ФСП ничего не блокирует. Контур и
коды ошибок — [verification.md](verification.md), [integration.md](integration.md).

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

`DELETE /candidate/account` доступен только authenticated candidate и выполняется
после явного подтверждения в UI. Gateway сначала ставит matching tombstone и
удаляет candidate-owned projection под pipeline lock, затем purges testing-engine
sessions/media under its owner tombstone, и только после этого в API transaction
отзывает sessions и удаляет source profile, recruitment processes/receipts,
notifications и account. При недоступном matching-engine source account остаётся,
чтобы публичная projection не пережила удаление источника.

`GET /candidate/skills/catalog` возвращает `{version, schema_hash, skills}`.
Каждый элемент `skills`: `{group, key, label, test_subject, confirmed_level,
next_attempt_at, upgrade_at}`; даты — UTC ISO8601 или null, группа — `domains`,
`stacks`, `soft_skills`. Registry берётся из `/internal/strict-schema` с cache
30 секунд, результаты и cooldown — только текущего аккаунта из accepted ledger;
ответ `no-store`. `401` — нет кандидатской сессии, `503` — каталог недоступен.
Нет отдельного запроса preflight для каждого навыка: он вызывается перед запуском
выбранного теста. Новый unknown coordinate → `400 unknown_skill_coordinate`;
старые неизменённые значения допускают сохранение остальных полей.
Подробности уровней, восстановления evidence и точного соответствия предметов —
[candidate-profile.md](candidate-profile.md).

Общая категория `declared_grade` остаётся `self_reported`, пока отдельный trusted
результат направления не подтвердит её; browser не передаёт UUID тестовой сессии.
Внутренний `/internal/testing/session-result` обновляет evidence и exact outbox
атомарно. Семь предметных тестов подтверждают только собственную skill-координату,
не общую профессию; legacy trusted результаты направлений читаются совместимо.
Прямой approved intake в matching не заменяет source. Self reported
grade допускается в пул при выполнении остальных требований, получает badge в
public PDF, а в policy `main-v2` confidence `0.6` уменьшает только его rating
contribution. Pending/rejected grade остаются блокирующими.
Готовая выдача проверяется против текущего policy fingerprint; смена policy
делает старый run obsolete до нового отбора. Ту же проверку использует реранкер.

### testing-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/healthz`, `/ready` | liveness/readiness |
| GET | `/blueprints` | список blueprint'ов (шаблонов заданий) |
| GET | `/blueprints/{id}` | один blueprint; 404 если нет |
| GET | `/testing/preflight` | доступность попытки, cooldown, согласия и готовность судей (внутренний токен + владелец) |
| POST | `/testing/sessions` | создать попытку |
| GET | `/testing/sessions/{id}/next`, `/result` | следующее задание, итог |
| POST | `/testing/sessions/{id}/answer`, `/supervision`, `/media`, `/finish`, `/abandon` | ответ, агрегаты наблюдения, запись, завершение, прерывание |

Entry point запускает `create_testing_app` с авторизацией внутренних маршрутов
`/testing/sessions/*`; браузер работает через API proxy ниже. Контракт и граница
изолированного judge — [testing-engine.md](testing-engine.md).

### integration-engine

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/fsp/participants/{fsp_id}` | профиль участника ФСП из мок-реестра; **404** = истории нет (обязательный случай по ТЗ — не ошибка платформы) |
| POST | `/fsp/verify` | сверка заявленного рейтинга с реестром: `{fsp_id, claimed_rating}` → `{fsp_id, verified, actual_rating}`; нет в реестре → `verified=false`, `actual_rating=null` |
| GET | `/fsp/participants` | список учебных аккаунтов демо-реестра |
| GET | `/fsp/participants/{fsp_id}/achievements/{claim_id}` | одно достижение участника |
| GET, POST | `/fsp/demo/authorize`; POST `/fsp/demo/token` | демо-привязка: страница согласия, одноразовый код и его обмен ([fsp-link.md](fsp-link.md)) |
| GET | `/healthz`, `/ready` | liveness/readiness |

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
контуром (testing-engine через `/internal/testing/session-result`); API не
принимает от браузера `grade_result` или evidence и не делает самоотчёт
подтверждённым.

Employer gateway публикует только явно проверенные пары HTTP-метод + internal-путь
из отдельных allowlist agents-service и matching-engine в
`apps/api/src/routes/accounts.ts`: редактор вакансий, agent sessions/jobs,
organization-agent, feed, saved/visited, preferences, hypotheses и showcase-likes API. Новая операция во внутреннем OpenAPI сама по себе не
создаёт публичный маршрут. `POST /employer/candidate-resume/parse` и
`POST /employer/candidate-recommendations/generate` отсутствуют (404), в том числе
для действующей employer-сессии; обращения к upstream не происходит. Эти worker
операции остаются только в доверенном внутреннем контуре. Разрешённые маршруты
сохраняют проверку сессии, origin для изменений и серверный `X-Owner-ID`.

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
| POST `/internal/vacancy-drafts/{id}/enrich` | `{expected_revision}` → JobView; только форма/Jev |
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
Auth/JWT/accounts, owner-scoped draft gateway, employer UI и CRUD опубликованной
вакансии (`/employer/vacancies*`, [vacancy-crud.md](vacancy-crud.md)) реализованы;
описанные выше employer selection routes owner-scoped и проверяют актуальность
источников.
Подробности: [agents.md](agents.md).

Проверка доступности AI различает HTTP503 с `detail`: `agent_disabled_manual_available`
означает manual mode или отсутствие ключа; `agent_temporarily_unavailable` — временный
сбой проверки провайдера; `agent_provider_rejected` — HTTP401/403 провайдера и необходимость
проверки конфигурации. Временный отрицательный probe кешируется 3 секунды, успешный и
отказ авторизации — 30 секунд. Проверка одной функции не опрашивает другую optional-зависимость.


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
| GET `/candidate/recruitment`, `/employer/recruitment` | limit + offset или opaque cursor → own RecruitmentPage ([cursor-pagination.md](cursor-pagination.md)) |
| GET `/{role}/recruitment/{id}` | own RecruitmentView |
| POST `/{role}/recruitment/{id}/actions` | RecruitmentAction → RecruitmentView |
| GET `/employer/recruitment/{id}/contacts` | accepted/current consent → ContactsView |
| GET `/{role}/recruitment-events` | after_sequence/limit → RecruitmentEventPage |
| GET `/{role}/recruitment-events/stream` | SSE-поток тех же событий ([chat-events.md](chat-events.md)) |

Action union: view/accept/decline/withdraw/reject, interview_propose/accept/decline/
cancel/complete, offer_send/accept/decline. CAS expected_revision и UUID key
обязательны, 409 на stale revision/conflicting key/state. Receipt повтора
не содержит контактов и не обещает текущее состояние. Контакты читаются отдельно
с no-store, до принятия403, чужой процесс404, нетprocessingconsent409.
Saved-членство для invitation/rejection читается через приватный matching
`GET /internal/vacancies/{vacancy_id}/saved/{candidate_id}` с token+serverowner;
он возвращает SavedCard, при stale источнике card:null, без удаления истории.
Без настройки чата chat_available:false. Схемы всех маршрутов включены в Fastify
OpenAPI; DTO живут в `packages/shared/src/recruitment.ts` и используются api-client.

## QGramm chat backend

Симметричные role-auth маршруты `/{role}/recruitment/{id}/chat/messages`
(GET history, POST SendChatMessage), `/files` (POST UploadChatFile) и
`/files/{file_id}` (GET DownloadChatFile), `/read` (POST ChatReadRequest →
ChatReadStatus) описаны в Fastify OpenAPI. GET recruitment одного процесса и
списков дополняется текущим participant-scoped `chat_unread_count`: число
входящих сообщений или `null`, если метадата недоступна.
DTO в `packages/shared/src/chat.ts`, типизированные методы в api-client.
SSE `GET /{role}/recruitment/{id}/chat/events` — [chat-events.md](chat-events.md).
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

## Внешняя верификация

Кандидатские `POST /candidate/profile/verifications/fsp`, `/repository` и `/hh` требуют authenticated candidate session и CAS `expected_revision`; account id не передаётся provider-сервису. Demo FSP flow: `POST /candidate/profile/fsp/demo/authorize` → GET/POST integration `/fsp/demo/authorize` → `POST /candidate/profile/fsp/demo/callback`; state candidate-bound, 120 секунд, callback allowlist и code одноразовые. Callback bulk-upsert-ит fixture achievements и binding `fsp_id`/`fsp_claim_id`. Account identity и ownership не выводятся из demo registry. Facts возвращаются в private profile view и доставляются matching через source outbox. Контракт провайдеров и деградация HH описаны в [verification.md](verification.md).

## Глобальный агент работодателя

`POST /employer/organization-agent/messages` принимает `message`, `idempotency_key`
и необязательный `reasoning_effort` (`off`, `medium`, `max`; default `off`).
Возвращаемый turn содержит сохранённый режим. Повтор ключа с другим режимом,
сообщением или task возвращает 409. Приватный `reasoning_content` исключён из
сериализованного provider receipt; история сообщений его не содержит.

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

## Отзыв, закрытие и удаление вакансии

`POST /employer/vacancies/{draft_id}/withdraw`, `POST .../close` и `DELETE
/employer/vacancies/{draft_id}` принимают `{expected_revision}` и возвращают
`DraftView`. Удалять можно только черновики и закрытые вакансии; закрытие
необратимо. Отзыв переводит вакансию в черновик, сохраняя лайки и процессы.
API проверяет актуальную публикацию при изменении процесса, отправке сообщения
и загрузке файла с обеих сторон, включая вызовы агента. Неактивные процессы
читаются, но их `allowed_actions` пуст и `chat_writable=false`;
`chat_available=true` продолжает разрешать чтение истории. Предложенные и назначенные интервью
отменяются; повторная публикация разрешает новые действия, не восстанавливая
отменённые интервью.

Миграция API `011_vacancy_lifecycle.sql` хранит durable-команды координации.
`012_chat_writable_receipts.sql` добавляет безопасный read-only флаг в ранее
сохранённые ответы команд; актуальный флаг клиент получает при чтении процесса.
Запись фиксируется до удалённого CAS; reconciliation каждые 5 секунд повторяет
неподтверждённые шаги по точным service receipts. Пока команда не завершена,
новая выдача, изменения и повторная публикация заблокированы. Ошибка
`vacancy_lifecycle_pending` означает незавершённую координацию и допускает
повтор того же действия; `vacancy_lifecycle_conflict` означает отклонённый CAS.
Gateway не меняет таблицы других сервисов напрямую.

Удаление очищает matching/editorial-записи, API recruitment graph и receipts,
а для приглашённых процессов вызывает приватный `POST /internal/chat/purge`:
QGramm удаляет зашифрованные сообщения, файлы, устройства и операции процесса.
Остаются только минимальные ID/revision tombstones против запоздалых запросов.
При недоступном chat provider удаление с приглашёнными процессами остаётся
pending; успешный ответ не выдаётся до подтверждения всех шагов.


### Очистка общей истории AI

`DELETE /employer/organization-agent/messages` принимает JSON
`{expected_context_revision: number}` из предварительного
`GET /employer/organization-agent/context`; успешный HTTP200 возвращает
`AgentsOrganizationContext` с пустым `current_summary` и новой ревизией.
Owner определяется сессией; чужой `X-Owner-ID` не даёт доступа. Cookie-запрос
требует разрешённый Origin. Очистка не требует доступного LLM и не вызывает его.

В одной транзакции удаляются сообщения, вопросы владельцу и завершённые turns
с их tool journal, сбрасывается текущий summary. Общая память, документы, задания,
вакансии, переписка с кандидатами и финансовые записи сохраняются. Терминальные
leases заданий инвалидируются; запоздалые ответы по удалённым turns и заданиям
отклоняются. Сохранённые provider receipts остаются финансовым/повторным fence;
авторитетная ревизия контекста включена в новый provider request, поэтому старый
ответ не используется для нового диалога после очистки. Это очистка рабочей
истории, а не полное удаление аккаунта или всех аудитных сведений.

HTTP409 `context_revision_conflict` означает, что после подтверждения контекст
изменился: требуется перечитать его и подтвердить заново. HTTP409
`organization_chat_busy` запрещает очистку при queued/processing/retrying turn,
незавершённом задании (включая waiting/stand-in/waiting_employer), reserved usage,
in-flight provider call или started tool journal. Активная работа автоматически
не отменяется; неразрешённый исход операции требует восстановления/разбора.
После HTTP200 frontend сбрасывает сообщения, выбранные turn и курсор опроса;
при ошибке локальная история сохраняется. Подтверждение выполняется модалкой
пользователя; в tool registry очистка не предлагается модели.

Клиент `getCandidatePipeline()` читает существующий `GET /api/candidate/pipeline` как `CandidatePipelineStatus`. Готовность относится к `profile_revision`; подтверждение доставки исходного профиля не равнозначно состоянию `ready`.


### Внутренний combine: обязательный контекст владельца

`/internal/vacancies/{vacancy_id}/combine` (POST/GET), `/combine/retry`,
`/selections` и `/selection-state/{candidate_id}` требуют UUID `X-Owner-ID`
вместе с `X-Internal-Token`. Matching всегда проверяет владельца вакансии,
отсутствующий или неверный UUID отклоняется до обращения к подбору.
Internal gateway сохраняет проверенный заголовок, browser API берёт владельца
из сессии. На внешнем nginx `/api/internal` и `/api/internal/*` закрыты (404);
сервисные клиенты используют прямой внутренний URL. Схема OpenAPI генерируется
из matching routes командой `python tests/generate_candidate_contract.py`.


### Общая публичная витрина

`GET /api/public/showcase/vacancies` и `GET /api/public/showcase/candidates` —
read-only списки без сессии и без detail/PDF/contact-доступа. Краткие карточки,
фильтры, opaque cursor и fences описаны в [контракте витрины](public-showcase.md).


## Профиль компании, сроки и уведомления

`GET/PATCH /api/employer/profile`, `GET /api/{candidate|employer}/system-chat`, `POST /api/{role}/system-chat/read`, `POST /api/{role}/system-chat/messages`, `POST/GET /api/{role}/system-chat/files` и `GET /api/{role}/chat-unread` используют владельца из сессии. DTO, CAS, пагинация, вложения, read receipts и fallback счётчика описаны в [system-inbox.md](system-inbox.md). Живая служба поддержки оператора — [support-console.md](support-console.md). API client использует те же контракты.

`RecruitmentView` дополнен `deadline` и terminal состоянием `expired`; closure допускает `actor=system`, категории feedback. `company_name` в InviteRequest стал необязательным совместимым полем: авторитетное название берётся из employer profile; несовпадение отклоняется. Причины отказа требуют содержательного текста и категорий. Полная таблица переходов — [recruitment.md](recruitment.md).

`GET /api/candidate/recommendations/latest` возвращает только собственную недельную рекомендацию либо `item=null`, включая честные insufficient_data/provider_unavailable. Источники, внутренние endpoint и leased worker описаны в [recommendations.md](recommendations.md). API автоматически возобновляет недельную очередь каждую минуту; уникальность кандидат/неделя предотвращает повторную генерацию. CLI `pnpm --filter @comit/api recommendations:worker` использует тот же durable store и атомарное уведомление.

Недельные рекомендации требуют нового явного согласия `training-v2`. Версия
`training-v1` совместима с прежним ранжированием, но не включает рекомендации.
Миграция API `017_recommendation_consent_version.sql` расширяет допустимые версии
и очищает результаты без нового согласия. Обновления резюме сохраняют выбранную версию.

### Расходы в статистике работодателя

`GET /employer/statistics` возвращает `expenses`: `currency: RUB`, `total_kopeks`, четыре `categories` с `amount_kopeks` и количеством ненулевых начислений `events`, заполненные UTC-дни `daily`, тарифы `tariffs`. Тарифы содержат `publication_kopeks`, полную `invitation_kopeks`, части приглашения `invitation_send_kopeks` и `invitation_accept_kopeks`, `deadline_penalty_kopeks`, `ai_daily_allowance_kopeks` и `ai_usd_rub`. Суммы — целые копейки. Период и владение совпадают с остальной статистикой; при `vacancy_id` расходы ИИ не включаются, потому что provider usage не имеет достоверной привязки к конкретной вакансии. Чтение не создаёт начислений. Ответ не содержит личности кандидата, содержимого ОС или provider payload.

Политика `expense-v1` сохраняется для старых записей: первая публикация вакансии 199 ₽, приглашение 49 ₽ при отправке, просрочка дедлайна 199 ₽. `expense-v2` действует только для новых источников: первая публикация — 398 ₽; приглашение — 196 ₽ суммарно (49 ₽ при отправке и 147 ₽ при принятии); просрочка дедлайна — 398 ₽. ИИ остаётся по фактической стоимости провайдера сверх 10 ₽ за московский календарный день, при переводе 100 ₽/USD. Дневная сумма округляется вверх до копейки один раз на накопленном превышении, а не для каждого вызова. Каждый source row содержит снимок своей политики; уже записанные расходы не пересчитываются. Принятие приглашения, отправленного по `expense-v1`, не создаёт доплату.

Некачественная ОС не определяется автоматически. Штраф 398 ₽ за некачественную ОС возникает исключительно после ручного решения поддержки через закрытую DB-функцию `record_support_feedback_expense(owner,vacancy,decision,explanation)`: идентификатор решения обязателен, повтор идемпотентен, функция отозвана у PUBLIC. Endpoint работодателя для этого не существует.


### История подтверждённых тестов кандидата

`GET /candidate/testing-history` доступен только с сессией кандидата. Владелец определяется сервером; чужой идентификатор не принимается. Ответ `CandidateTestingHistory`: `timezone: Europe/Moscow`, `kind: confirmed_results`, включительный `period: {from,to}`, 30 элементов `daily: [{date,count}]` и сумма `total`. Период включает сегодняшний московский день и 29 предыдущих. Будущие записи исключены. Отсутствие результатов даёт нулевой календарь; ошибка хранилища — 503 `testing_history_unavailable`, а не пустую успешную историю.

Доверенный producer `POST /internal/testing/session-result` принимает `X-Internal-Token`, сверяемый с `TESTING_ENGINE_TOKEN`, и поля `candidate_id`, `session_id`, `specialization`, `decided_grade`, `tasks_answered`, необязательный `supervision_badge`. Browser JWT не даёт права отправлять результаты. Запись результата, подтверждённое свидетельство `grade:trusted`, revision профиля и outbox проекции изменяются одной транзакцией. Повтор идентичного результата возвращает прежний результат без повторного начисления активности; повтор session_id с другим владельцем или содержимым — 409. Повторный тест разрешён через 7 дней, повышение оценки — через 30 дней.

Ответ 200: `{recorded:true, grade, decided_at}`; без `TESTING_ENGINE_TOKEN` — 503 `testing_not_configured`, неверный токен — 401, cooldown — 409 `retake_cooldown`/`grade_upgrade_cooldown`. Publisher адаптивного контура (`services/testing-engine/src/testing_engine/publisher.py`) отправляет именно этот контракт и берёт `tasks_given` (число оценённых адаптивных ответов) для wire `tasks_answered`. Compose передаёт testing-engine `TESTING_API_URL` и токен; entrypoint запускает адаптивный контур и trusted publisher ([testing-engine.md](testing-engine.md)). Незавершённые попытки и отсутствующая история не восстанавливаются искусственно.

`/ready` возвращает 503 при отсутствующей auth-конфигурации, недоступной БД или неприменённых миграциях `021_support_console.sql` и `021_weekly_challenges.sql`; `/healthz` остаётся liveness. После объединения с dev trusted publisher берёт `tasks_given` (число подтверждённых адаптивных ответов) для wire `tasks_answered`; повтор результата идемпотентен.

## Кандидатский тест: браузерный API

Все маршруты `/candidate/testing` требуют действующую кандидатскую cookie/Bearer-сессию; мутации требуют разрешённый Origin. `GET /preflight?specialization=<family>&mode=retake|upgrade` возвращает eligible, next_attempt_at, upgrade_at, active_session_id, supported_specializations, recording_enabled, max_questions. `POST /sessions` принимает specialization, optional declared_grade/mode, consents {version: testing-media-v1, processing: true, screen_recording: boolean, video_analysis: boolean}. Попытка создаётся только этим запросом.

`GET /sessions/:id/next` возвращает безопасное задание; `POST .../answer` — {item_id, answer: number|string}. `POST .../supervision` принимает ограниченные total_seconds/in_frame_seconds/focus_lost_seconds/multi_face_events/camera. `POST .../media` — {kind: screen|webcam, sequence, data_base64, mime_type?}; максимум base64 3 млн символов. `POST .../finish` вызывается после загрузки записи и supervision; `GET .../result` содержит grade_publication и next_attempt_at. `POST .../abandon` прерывает попытку без доверенного результата. ID кандидата никогда не принимается от браузера. Типы — `packages/shared/src/testing.ts`. Внутренний `POST /internal/testing/session-result` сохранён совместимым.

В подтверждённом внутреннем результате optional `camera_status` передаётся как granted/denied/unavailable и сохраняется в grade-evidence. Старые результаты без явного выбора камеры получают unknown, а не «отказ». Защищённый detail работодателя `/employer/candidates/:id` добавляет только `testing_results` (subject, grade, supervision_badge, camera_status), после проверки актуальности публичной проекции; запись, внутренние идентификаторы и сырые счётчики не раскрываются. Preflight возобновления содержит active_session_consents, active_supervision, next_sequence и active_session_status. Накопительные supervision checkpoints не могут уменьшаться, а отказ камеры сохраняется при возобновлении. Capture_finalized делает повтор завершения после потерянного ответа безопасным.

Согласие processing обязательно для новых попыток. screen_recording соответствует режиму на старте: true для full, false для off. Режим фиксируется в попытке; active_recording_enabled возвращает его при возобновлении, а recording_enabled — текущую настройку для новых попыток. Старые попытки без сохранённого режима не принимают запись; legacy processing в preflight может отсутствовать и не восстанавливается задним числом. Сервер ограничивает total_seconds фактическим временем с начала попытки + 5 секунд; без сохранённого video_analysis всегда принудительно camera=denied. Эти клиентские показатели — вспомогательный сигнал присутствия; решение о добросовестности остаётся за человеком.

## Дополнения: импорт, ФСП и практика

Новые owner-scoped маршруты и DTO: [импорт PDF/фото](resume-import.md), [привязка демо-ФСП](fsp-link.md), [недельные задания](weekly-challenges.md). Jev использует существующий enrich/job контракт: [vacancy-suggestions.md](vacancy-suggestions.md).

В полном локальном disabled-режиме testing preflight возвращает
`unavailable_reason: "testing_disabled"` и `eligible: false`; все операции
над testing session возвращают `503 {"detail":"testing_disabled"}`.
Фасад сохраняет этот reason; обязательная авторизация и owner scope действуют.

## Яндекс OAuth

`GET /auth/{candidate|employer}/providers` → `{yandex: boolean}` (без кеширования). `POST /auth/{role}/yandex/start` принимает `{consent:{processing:true,version:"account-v1"}}`, требует доверенный Origin и возвращает `{authorization_url:string}` с browser-binding cookie. `GET /auth/{role}/yandex/callback?code=…&state=…` обменивает одноразовый code на сервере, создаёт обычную revocable session и перенаправляет в кабинет; ошибки уходят в безопасный `#oauth-error=<category>`. Не настроенный провайдер отвечает 503 при попытке старта. Контракт применяется только при полной production-конфигурации. Подробная [настройка Яндекс ID](yandex-oauth.md).

## Завершение теста без оценки и отказ публикации

Testing result допускает `next_attempt_at:null` и `failure_reason:"judge_unavailable"`: сессия `abandoned` после трёх неоценённых ответных проверок не публикует грейд и не добавляет кулдаун. `grade_publication` сохраняет `retryable`, `attempts`, `detail`, `retry_at`, `next_retry_at` для bounded retries и понятного отказа. Внутренний `/internal/testing/session-result` при 409 `grade_upgrade_cooldown` возвращает `upgrade_at`, при `retake_cooldown` — `next_attempt_at` (UTC ISO8601). Исходные сроки и политика повышения не меняются.
