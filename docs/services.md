# Обзор сервисов

Короткая карточка каждого процесса: назначение, порт, основные маршруты,
переменные окружения, данные и локальный запуск. Источник — `infra/docker-compose.yml`,
`infra/docker-compose.dev.yml`, `infra/docker-compose.judge.yml` и код сервисов.
Подробные контракты — по ссылкам в каждой карточке; схема связей —
[architecture-diagrams.md](architecture-diagrams.md).

| Сервис | Стек | Порт в контейнере | Порт на хосте (loopback) | Документ |
|---|---|---|---|---|
| web (nginx) | nginx + статические сборки 5 SPA | 80 | `WEB_PORT`, 8088 (наружу) | [deploy.md](deploy.md) |
| api-core | Node.js 22, Fastify | 8080 | `API_PORT`, 28080 | [api.md](api.md), [api-reference.md](api-reference.md) |
| matching-engine | Python, FastAPI | 8101 | `MATCHING_PORT`, 28101 | [matching.md](matching.md) |
| testing-engine | Python, FastAPI | 8102 | `TESTING_PORT`, 8102 | [testing-engine.md](testing-engine.md) |
| integration-engine | Python, FastAPI | 8103 | `INTEGRATION_PORT`, 8103 | [integration.md](integration.md) |
| agents-service | Python, FastAPI | 8104 | `AGENTS_PORT`, 28104 | [agents.md](agents.md) |
| verification-engine | Python, FastAPI | 8105 | `VERIFICATION_PORT`, 28105 | [verification.md](verification.md) |
| code-judge | Python, FastAPI + Docker | 8106 | 127.0.0.1:8106 на отдельной VM | [deploy.md](deploy.md#изолированная-проверка-кода) |
| qgramm-chat | Go-мост QGramm | внутренняя сеть `chat-private` | не публикуется | [chat.md](chat.md) |
| postgres | PostgreSQL 16 + pgvector | 5432 | только в dev: 127.0.0.1:5433 | [data.md](data.md) |

Все HTTP-сервисы отвечают на `GET /healthz` (liveness); api-core, matching-engine,
agents-service, testing-engine, integration-engine и code-judge также дают
`GET /ready`. Сервисные токены передаются только между контейнерами и никогда не
попадают в браузер.

## web (nginx)

- **Назначение.** Единственная точка входа: отдаёт SPA `/` (welcome),
  `/candidate/`, `/employer/`, `/testing/`, `/support/` и проксирует `/api/*` в
  api-core с отрезанием префикса. `/api/internal` и `/api/internal/*` отвечают 404.
- **Особенности.** `client_max_body_size 16m` для документов-доказательств,
  `proxy_buffering off` и `proxy_read_timeout 75s` для SSE-потоков.
- **Конфигурация.** `infra/nginx/nginx.conf`; HTTPS-домен —
  `infra/nginx/comit.droidje.com.conf` ([deploy.md](deploy.md)).

## api-core (`apps/api`)

- **Назначение.** Транзакционное ядро и единственный публичный API: вход по email
  и коду, сессии, профиль кандидата и согласия, вакансии (через agents-service),
  приглашения и процесс найма, чаты, системный чат и консоль поддержки, статистика,
  витрины, расходы работодателя, проксирование теста в testing-engine.
- **Маршруты.** 249 операций, полный список — [api-reference.md](api-reference.md);
  Swagger UI на `/docs` (в production — `/api/docs`).
- **Ключевые переменные.** `DATABASE_URL`, `AUTH_SECRET` (≥32 байт; `JWT_SECRET` —
  устаревший fallback), `AUTH_ALLOWED_ORIGINS`, `AUTH_COOKIE_SECURE`, `AUTH_KEY_ID`,
  `ACCOUNT_CONSENT_VERSION`, `TRUST_PROXY`, `PUBLIC_SITE_URL`, `SMTP_*`,
  `MATCHING_ENGINE_URL`, `CANDIDATE_PIPELINE_TOKEN`, `AGENTS_SERVICE_URL`,
  `AGENTS_SERVICE_TOKEN`, `TESTING_ENGINE_URL`, `TESTING_ENGINE_TOKEN`,
  `VERIFICATION_ENGINE_URL`, `VERIFICATION_ENGINE_TOKEN`, `VERIFICATION_DOCUMENT_KEY`,
  `CHAT_SERVICE_URL`, `CHAT_SERVICE_TOKEN`, `FSP_DEMO_ENABLED`, `SUPPORT_EMAIL`,
  `SUPPORT_ACCESS_KEY`, `DEV_AUTH_EXPOSE_CODE` (только development). Полная таблица —
  [deploy.md](deploy.md).
- **Данные.** PostgreSQL: таблицы аккаунтов, сессий, профилей, найма, уведомлений,
  поддержки и расходов; миграции `apps/api/migrations/001…021`, учёт в
  `auth_migrations` ([data.md](data.md)).
- **Фоновые процессы из того же образа.** `auth-migrate` (миграции),
  `auth-mail-worker` (письма OTP и уведомлений, [email-notifications.md](email-notifications.md)),
  `candidate-profile-worker` (проекция профиля в matching), `recommendation-worker`
  (рекомендации кандидату через agents-service, [recommendations.md](recommendations.md)).
- **Локально.** `pnpm --filter @comit/api auth:migrate`, затем
  `pnpm --filter @comit/api dev` (http://127.0.0.1:8080). Тесты:
  `pnpm --filter @comit/api test`.

## matching-engine (`services/matching-engine`)

- **Назначение.** «Комбайн» подбора: пул кандидатов с векторами и PDF, строгий
  фильтр, гибридный скоринг, main top-k, sort-only реранк R₀, лента работодателя
  с vacancy-local preference-моделью и research-показами гипотез, витрины.
- **Маршруты.** Только внутренние, по `X-Internal-Token` (`CANDIDATE_PIPELINE_TOKEN`):
  `/internal/candidates/*`, `/internal/pool`, `/internal/vacancies/*` (готовность,
  combine, rerank, selections, feed, preferences, hypotheses, saved/visited,
  showcase-likes), `/internal/matching/events`, `/internal/strict-schema`,
  `/internal/employer/candidates/*`; плюс `/healthz`, `/ready`.
- **Переменные.** `DATABASE_URL`, `CANDIDATE_PIPELINE_TOKEN`, `APP_ENV`,
  `MATCHING_PROMOTION_MODE` (`off`/`shadow`/`active`; production compose — `shadow`,
  dev — `active`), `MATCHING_PROMOTION_ALLOW_DEMO`, `HF_HOME` (кэш модели Harrier),
  `PIPELINE_MAX_ATTEMPTS`, `PIPELINE_LEASE_SECONDS`.
- **Данные.** Схемы `candidate_pipeline`, `vacancy_pipeline`, `matching_pipeline`,
  `feed_pipeline`, `recommendation_evidence`; миграции
  `src/matching_engine/migrations/001…017` применяет job `candidate-migrate`.
- **Workers (тот же образ, `python -m matching_engine <команда>`).** `worker`
  (кандидаты), `vacancy-worker`, `combine-worker`, `rerank-worker`,
  `feed-refill-worker`, `preference-worker`.
- **Локально.** `PYTHONPATH=services/matching-engine/src python -m pytest services/matching-engine/tests -q`;
  `/ready` требует подготовленный snapshot Harrier ([deploy.md](deploy.md)).

## testing-engine (`services/testing-engine`)

- **Назначение.** Параметризованные blueprints и адаптивный грейд-тест: банк из
  44 заданий семи семейств, старт с L2, решение при вероятности 0.70, не более
  30 заданий; агрегаты наблюдения (камера/экран), запись, trusted publisher
  результата в api-core.
- **Маршруты.** `/blueprints`, `/blueprints/{id}`, `/testing/preflight`,
  `/testing/sessions` и действия сессии (`next`, `answer`, `supervision`, `media`,
  `result`, `finish`, `abandon`). Маршруты `/testing/*` требуют
  `X-Internal-Token` и `X-Candidate-ID` от api-core; браузер обращается к ним
  только через `/api/candidate/testing/*`.
- **Переменные.** `TESTING_ENGINE_TOKEN`, `TESTING_API_URL`, `BLUEPRINT_DIR`,
  `TESTING_DATA_DIR`, `TESTING_RECORDING_MODE`, `CODE_JUDGE_URL`,
  `CODE_JUDGE_TOKEN`, `CODE_JUDGE_CA_PEM`, `JEV_API_URL`/`JEV_API_KEY`/`JEV_MODEL`,
  `DEEPSEEK_BASE_URL`/`DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL`.
- **Данные.** Файловое хранилище сессий и записей в `TESTING_DATA_DIR`; при старте и
  затем ежечасно удаляются медиа старше 90 дней. Подтверждённый результат хранит api-core.
- **Подробнее.** [testing-engine.md](testing-engine.md), [testing-design.md](testing-design.md),
  [package-validation.md](package-validation.md).

## code-judge (`services/code-judge`)

- **Назначение.** Аутентифицированный брокер проверки кода: каждый testcase
  выполняется в отдельном Docker-контейнере (128 MiB, 0.5 CPU, 16 pids,
  UID 65534).
- **Маршруты.** `POST /submissions`, `GET /healthz`, `GET /ready`.
- **Переменные.** `CODE_JUDGE_TOKEN`, `CODE_JUDGE_IMAGE`, `CODE_JUDGE_DATA_DIR`,
  `CODE_JUDGE_PORT`.
- **Запуск.** Отдельная VM через `infra/docker-compose.judge.yml`; testing-engine
  подключается по `CODE_JUDGE_URL`. CI проверяет изоляцию job-ом
  `code-judge-isolation` ([ci.md](ci.md)).

## integration-engine (`services/integration-engine`)

- **Назначение.** Учебный мок-реестр ФСП и демо-привязка ФСП-профиля.
- **Маршруты.** `/fsp/participants`, `/fsp/participants/{fsp_id}`,
  `/fsp/participants/{fsp_id}/achievements/{claim_id}`, `/fsp/verify`,
  `/fsp/demo/authorize`, `/fsp/demo/token`, `/healthz`, `/ready`.
- **Переменные.** `APP_ENV`, `FSP_DEMO_ENABLED`; в dev также
  `FSP_DEMO_INTERNAL_TOKEN`, `FSP_DEMO_CALLBACK_URL`. Реестр отвечает только при
  `APP_ENV=development` и `FSP_DEMO_ENABLED=true`; в production — 503.
- **Подробнее.** [integration.md](integration.md), [fsp-link.md](fsp-link.md).

## verification-engine (`services/verification-engine`)

- **Назначение.** Проверка внешних фактов профиля: ФСП (через integration-engine),
  публичный Git-репозиторий, резюме hh.ru.
- **Маршруты.** `POST /verify/fsp`, `POST /verify/repository`, `POST /verify/hh`,
  `GET /healthz`.
- **Переменные.** `VERIFICATION_ENGINE_TOKEN`, `INTEGRATION_ENGINE_URL`,
  `HH_API_TOKEN`, `HH_USER_AGENT`, `FSP_DEMO_ENABLED` (+ `FSP_DEMO_INTERNAL_TOKEN` в dev).
- **Данные.** Собственной БД нет: результат сохраняет api-core в evidence профиля.
- **Подробнее.** [verification.md](verification.md).

## agents-service (`services/agents-service`)

- **Назначение.** Общий черновик вакансии для формы и чата, AI-сессии
  редактирования, глобальный агент организации (чат, память, документы, фоновые
  поручения, бюджет), разбор импортированного резюме и тексты рекомендаций.
- **Маршруты.** Внутренние, по `X-Internal-Token` (`AGENTS_SERVICE_TOKEN`) и
  `X-Owner-ID`: `/internal/vacancy-drafts/*`, `/internal/vacancies/*`,
  `/internal/agents/*`, `/internal/organization-agent/*`,
  `/internal/candidate-resume/parse`, `/internal/candidate-recommendations/generate`.
- **Переменные.** `DATABASE_URL`, `AGENTS_SERVICE_TOKEN`, `AGENTS_MODE`,
  `MATCHING_ENGINE_URL`, `CANDIDATE_PIPELINE_TOKEN`, `ORGANIZATION_API_URL`,
  `DEEPSEEK_*`, `JEV_*`, `JEV_LOCAL_URL`, тарифы `AGENT_*_USD_PER_MILLION` и
  `JEV_*_USD_PER_MILLION`.
- **Данные.** Схема `agents_service`; миграции `src/agents_service/migrations`
  применяет job `agents-migrate`.
- **Workers.** `agents-worker` (`python -m agents_service worker`) и
  `organization-agent-worker` (`python -m agents_service organization-worker`).

## qgramm-chat (`infra`, QGramm bridge)

- **Назначение.** Хранилище переписки найма с серверным шифрованием сообщений и
  вложений; api-core обращается к нему по внутренней сети `chat-private`.
- **Переменные.** `CHAT_BRIDGE_LISTEN`, `CHAT_SERVICE_TOKEN`, `CHAT_DEVICE_SECRET`,
  `CHAT_SIGNING_KEY`, `QGRAMM_MANAGEMENT_SECRET`, `QGRAMM_MASTER_KEY`,
  `QGRAMM_HPKE_KEY`; данные — volume `chat-data`.
- **Подробнее.** [chat.md](chat.md), [chat-events.md](chat-events.md).

## Фронтенды (`apps/*`)

| Приложение | Пакет | Путь | Vite dev | Документ |
|---|---|---|---|---|
| Витрина и вход | `@comit/web-welcome` | `/` | 5176 | [welcome-ui.md](welcome-ui.md) |
| Кабинет кандидата | `@comit/web-candidate` | `/candidate/` | 5173 | [candidate-ui.md](candidate-ui.md) |
| Кабинет работодателя | `@comit/web-employer` | `/employer/` | 5174 | [employer-ui.md](employer-ui.md) |
| Тестирование | `@comit/web-testing` | `/testing/` | 5175 | [testing-engine.md](testing-engine.md) |
| Консоль поддержки | `@comit/web-support` | `/support/` | 5177 | [support-console.md](support-console.md) |

Все приложения — React 19 + Vite, общие компоненты в `packages/ui-kit`
([ui-kit.md](ui-kit.md)), типы и клиент API — `packages/shared` и
`packages/api-client` ([packages.md](packages.md)).
