# Справочник маршрутов api-core

Полный перечень HTTP-операций api-core (`apps/api`), построенный из OpenAPI-спецификации, которую сервер отдаёт на `/docs` (в production — https://comit.droidje.com/api/docs). Всего **249** операций: 174 публичных и 75 `/internal/*` при настроенной консоли поддержки; без `SUPPORT_EMAIL`/`SUPPORT_ACCESS_KEY` семь операций `/support/*` не регистрируются. Подробные контракты, коды ошибок и примеры — в [api.md](api.md) и тематических документах по ссылкам.

Пути указаны так, как их видит api-core. Браузер обращается к ним через web-шлюз с префиксом `/api` (например, `/api/candidate/profile`); `/api/internal/*` шлюз не проксирует и отвечает 404.

## Аутентификация

| Схема | Где передаётся | Кто |
|---|---|---|
| `candidateSession` | cookie `__Host-comit_candidate_session` (при `AUTH_COOKIE_SECURE=false`, как в dev, — без префикса `__Host-`) | кандидат |
| `employerSession` | cookie `__Host-comit_employer_session` | работодатель |
| `bearerAuth` | `Authorization: Bearer <JWT>` | альтернатива cookie для тех же ролей |
| `supportSession` | cookie `__Host-comit_support_session` | оператор поддержки |
| `internalToken` | заголовок `X-Internal-Token` | доверенные сервисы внутри compose-сети |

Мутации с cookie-сессией дополнительно проверяют разрешённый `Origin` (`AUTH_ALLOWED_ORIGINS`); Bearer-клиенты могут не передавать `Origin`.

## Сводка по группам

| Группа (OpenAPI tag) | Операций |
|---|---|
| [Вход и сессии](#auth) — `auth` | 10 |
| [Здоровье](#health) — `health` | 2 |
| [Эксплуатация](#operations) — `operations` | 1 |
| [Аккаунт кандидата](#candidate-account) — `candidate-account` | 2 |
| [Профиль кандидата](#candidate-profile) — `candidate-profile` | 8 |
| [Конвейер кандидата (внутренний)](#candidate-pipeline) — `candidate-pipeline` | 9 |
| [Проверки кандидата](#candidate-verification) — `candidate-verification` | 10 |
| [Тестирование кандидата](#candidate-testing) — `candidate-testing` | 9 |
| [Недельные задания](#candidate-weekly-challenge) — `candidate-weekly-challenge` | 3 |
| [Рекомендации кандидату](#candidate-recommendations) — `candidate-recommendations` | 1 |
| [Найм](#recruitment) — `recruitment` | 14 |
| [Чаты найма](#chat) — `chat` | 12 |
| [Системный чат](#system-inbox) — `system-inbox` | 8 |
| [Аккаунт работодателя и агенты](#employer-account) — `employer-account` | 50 |
| [Профиль работодателя](#employer-profile) — `employer-profile` | 2 |
| [Конвейер вакансии (внутренний)](#vacancy-pipeline) — `vacancy-pipeline` | 14 |
| [Лента работодателя](#employer-feed) — `employer-feed` | 20 |
| [Подборка вакансии](#employer-selection) — `employer-selection` | 9 |
| [Поиск по витрине кандидатов](#employer-showcase) — `employer-showcase` | 3 |
| [Статистика работодателя](#employer-statistics) — `employer-statistics` | 1 |
| [Инструменты агента организации (внутренний)](#organization-agent) — `organization-agent` | 1 |
| [Публичная витрина](#public-showcase) — `public-showcase` | 2 |
| [Консоль поддержки](#support) — `support` | 7 |
| [Внутренний gateway agents-service](#agents) — `agents` | 50 |
| [Доверенный приём результатов теста](#internal-testing) — `internal-testing` | 1 |

<a id="auth"></a>
## Вход и сессии (`auth`)

Вход по email и одноразовому коду, сессии кандидата и работодателя — [auth.md](auth.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/auth/candidate/code` | без сессии | да |
| POST | `/auth/candidate/logout` | кандидат (cookie или Bearer) | — |
| POST | `/auth/candidate/logout-all` | кандидат (cookie или Bearer) | — |
| GET | `/auth/candidate/me` | кандидат (cookie или Bearer) | — |
| POST | `/auth/candidate/verify` | без сессии | да |
| POST | `/auth/employer/code` | без сессии | да |
| POST | `/auth/employer/logout` | работодатель (cookie или Bearer) | — |
| POST | `/auth/employer/logout-all` | работодатель (cookie или Bearer) | — |
| GET | `/auth/employer/me` | работодатель (cookie или Bearer) | — |
| POST | `/auth/employer/verify` | без сессии | да |

<a id="health"></a>
## Здоровье (`health`)

Liveness/readiness.

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/healthz` | без сессии | — |
| GET | `/ready` | без сессии | — |

<a id="operations"></a>
## Эксплуатация (`operations`)

Prometheus-метрики — [api-operations.md](api-operations.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/metrics` | без сессии | — |

<a id="candidate-account"></a>
## Аккаунт кандидата (`candidate-account`)

Статус конвейера кандидата и PDF собственного резюме.

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/pipeline` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/resume.pdf` | кандидат (cookie или Bearer) | — |

<a id="candidate-profile"></a>
## Профиль кандидата (`candidate-profile`)

Исходный профиль и согласия, импорт резюме, каталог навыков, таксономия, история тестов — [candidate-profile.md](candidate-profile.md), [resume-import.md](resume-import.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/profile` | кандидат (cookie или Bearer) | — |
| PATCH | `/candidate/profile` | кандидат (cookie или Bearer) | да |
| PUT | `/candidate/profile` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/import` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/projection/retry` | кандидат (cookie или Bearer) | да |
| GET | `/candidate/skills/catalog` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/taxonomy` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/testing-history` | кандидат (cookie или Bearer) | — |

<a id="candidate-pipeline"></a>
## Конвейер кандидата (внутренний) (`candidate-pipeline`)

Проекция профиля в пул matching, события доставки, strict-schema — [candidate-profile.md](candidate-profile.md), [matching.md](matching.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/internal/candidates/{candidate_id}` | X-Internal-Token | — |
| PUT | `/internal/candidates/{candidate_id}/profile` | X-Internal-Token | да |
| POST | `/internal/candidates/{candidate_id}/retry` | X-Internal-Token | — |
| GET | `/internal/matching/events` | X-Internal-Token | — |
| POST | `/internal/matching/events/retry` | X-Internal-Token | да |
| GET | `/internal/pool` | X-Internal-Token | — |
| GET | `/internal/pool/{candidate_id}/resume.pdf` | X-Internal-Token | — |
| GET | `/internal/strict-schema` | X-Internal-Token | — |
| POST | `/internal/strict-schema/coordinates` | X-Internal-Token | да |

<a id="candidate-verification"></a>
## Проверки кандидата (`candidate-verification`)

ФСП, Git-репозиторий, hh.ru, документы — [verification.md](verification.md), [fsp-link.md](fsp-link.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/candidate/profile/fsp/demo/approve` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/fsp/demo/authorize` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/fsp/demo/callback` | кандидат (cookie или Bearer) | да |
| GET | `/candidate/profile/fsp/status` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/profile/verification-documents` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/profile/verification-documents` | кандидат (cookie или Bearer) | да |
| GET | `/candidate/profile/verification-documents/{id}` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/profile/verifications/fsp` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/verifications/hh` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/profile/verifications/repository` | кандидат (cookie или Bearer) | да |

<a id="candidate-testing"></a>
## Тестирование кандидата (`candidate-testing`)

Браузерный API адаптивного теста — [testing-engine.md](testing-engine.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/testing/preflight` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/testing/sessions` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/testing/sessions/{sessionId}/abandon` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/testing/sessions/{sessionId}/answer` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/testing/sessions/{sessionId}/finish` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/testing/sessions/{sessionId}/media` | кандидат (cookie или Bearer) | да |
| GET | `/candidate/testing/sessions/{sessionId}/next` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/testing/sessions/{sessionId}/result` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/testing/sessions/{sessionId}/supervision` | кандидат (cookie или Bearer) | да |

<a id="candidate-weekly-challenge"></a>
## Недельные задания (`candidate-weekly-challenge`)

[weekly-challenges.md](weekly-challenges.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/weekly-challenge` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/weekly-challenge/start` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/weekly-challenge/submit` | кандидат (cookie или Bearer) | да |

<a id="candidate-recommendations"></a>
## Рекомендации кандидату (`candidate-recommendations`)

[recommendations.md](recommendations.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/recommendations/latest` | кандидат (cookie или Bearer) | — |

<a id="recruitment"></a>
## Найм (`recruitment`)

Приглашения, собеседования, офферы, события — [recruitment.md](recruitment.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/recruitment` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/recruitment-events` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/recruitment-events/stream` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/recruitment/{id}` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/recruitment/{id}/actions` | кандидат (cookie или Bearer) | да |
| GET | `/employer/recruitment` | работодатель (cookie или Bearer) | — |
| GET | `/employer/recruitment-events` | работодатель (cookie или Bearer) | — |
| GET | `/employer/recruitment-events/stream` | работодатель (cookie или Bearer) | — |
| GET | `/employer/recruitment/{id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/recruitment/{id}/actions` | работодатель (cookie или Bearer) | да |
| GET | `/employer/recruitment/{id}/contacts` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/invitations` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/recruitment` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/recruitment/reject` | работодатель (cookie или Bearer) | да |

<a id="chat"></a>
## Чаты найма (`chat`)

QGramm-переписка процесса — [chat.md](chat.md), [chat-events.md](chat-events.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/recruitment/{id}/chat/events` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/recruitment/{id}/chat/files` | кандидат (cookie или Bearer) | да |
| GET | `/candidate/recruitment/{id}/chat/files/{file_id}` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/recruitment/{id}/chat/messages` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/recruitment/{id}/chat/messages` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/recruitment/{id}/chat/read` | кандидат (cookie или Bearer) | да |
| GET | `/employer/recruitment/{id}/chat/events` | работодатель (cookie или Bearer) | — |
| POST | `/employer/recruitment/{id}/chat/files` | работодатель (cookie или Bearer) | да |
| GET | `/employer/recruitment/{id}/chat/files/{file_id}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/recruitment/{id}/chat/messages` | работодатель (cookie или Bearer) | — |
| POST | `/employer/recruitment/{id}/chat/messages` | работодатель (cookie или Bearer) | да |
| POST | `/employer/recruitment/{id}/chat/read` | работодатель (cookie или Bearer) | да |

<a id="system-inbox"></a>
## Системный чат (`system-inbox`)

Уведомления платформы и поддержка — [system-inbox.md](system-inbox.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/candidate/chat-unread` | кандидат (cookie или Bearer) | — |
| GET | `/candidate/system-chat` | кандидат (cookie или Bearer) | — |
| POST | `/candidate/system-chat/messages` | кандидат (cookie или Bearer) | да |
| POST | `/candidate/system-chat/read` | кандидат (cookie или Bearer) | да |
| GET | `/employer/chat-unread` | работодатель (cookie или Bearer) | — |
| GET | `/employer/system-chat` | работодатель (cookie или Bearer) | — |
| POST | `/employer/system-chat/messages` | работодатель (cookie или Bearer) | да |
| POST | `/employer/system-chat/read` | работодатель (cookie или Bearer) | да |

<a id="employer-account"></a>
## Аккаунт работодателя и агенты (`employer-account`)

Вакансии, черновики, AI-сессии и глобальный агент организации — [vacancy-crud.md](vacancy-crud.md), [agents.md](agents.md). Gateway зеркалирует все `/internal/*` операции контракта agents-service в `/employer/*`, поэтому здесь также видны `POST /employer/candidate-resume/parse` и `POST /employer/candidate-recommendations/generate`; приложения их не вызывают — импорт резюме и рекомендации кандидату работают через собственные маршруты кандидата и `recommendation-worker`.

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/employer/agents/capabilities` | работодатель (cookie или Bearer) | — |
| GET | `/employer/agents/jobs/{job_id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/agents/jobs/{job_id}/retry` | работодатель (cookie или Bearer) | — |
| POST | `/employer/agents/sessions` | работодатель (cookie или Bearer) | да |
| GET | `/employer/agents/sessions/{session_id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/agents/sessions/{session_id}/interactions/{interaction_id}` | работодатель (cookie или Bearer) | да |
| POST | `/employer/agents/sessions/{session_id}/turns` | работодатель (cookie или Bearer) | да |
| GET | `/employer/agents/taxonomy` | работодатель (cookie или Bearer) | — |
| POST | `/employer/candidate-recommendations/generate` | работодатель (cookie или Bearer) | да |
| POST | `/employer/candidate-resume/parse` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/budget` | работодатель (cookie или Bearer) | — |
| GET | `/employer/organization-agent/chat` | работодатель (cookie или Bearer) | — |
| GET | `/employer/organization-agent/context` | работодатель (cookie или Bearer) | — |
| GET | `/employer/organization-agent/documents` | работодатель (cookie или Bearer) | — |
| POST | `/employer/organization-agent/documents` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/documents/{document_id}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/organization-agent/documents/{document_id}/download` | работодатель (cookie или Bearer) | — |
| GET | `/employer/organization-agent/memory` | работодатель (cookie или Bearer) | — |
| PATCH | `/employer/organization-agent/memory` | работодатель (cookie или Bearer) | да |
| DELETE | `/employer/organization-agent/messages` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/messages` | работодатель (cookie или Bearer) | — |
| POST | `/employer/organization-agent/messages` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/pending-answers` | работодатель (cookie или Bearer) | — |
| POST | `/employer/organization-agent/pending-answers/{answer_id}` | работодатель (cookie или Bearer) | да |
| POST | `/employer/organization-agent/presence` | работодатель (cookie или Bearer) | да |
| PUT | `/employer/organization-agent/presence` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/tasks` | работодатель (cookie или Bearer) | — |
| POST | `/employer/organization-agent/tasks` | работодатель (cookie или Bearer) | да |
| DELETE | `/employer/organization-agent/tasks/{task_id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/organization-agent/tasks/{task_id}/availability` | работодатель (cookie или Bearer) | да |
| GET | `/employer/organization-agent/turns/{turn_id}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancies` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancies` | работодатель (cookie или Bearer) | да |
| DELETE | `/employer/vacancies/{draft_id}` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancies/{draft_id}` | работодатель (cookie или Bearer) | — |
| PATCH | `/employer/vacancies/{draft_id}` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancies/{draft_id}/approve` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancies/{draft_id}/close` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancies/{draft_id}/deletion` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancies/{draft_id}/lifecycle/{expected_revision}/{action}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancies/{draft_id}/publish` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancies/{draft_id}/withdraw` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}` | работодатель (cookie или Bearer) | — |
| PATCH | `/employer/vacancy-drafts/{draft_id}` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/approve` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/create` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/enrich` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/pipeline` | работодатель (cookie или Bearer) | — |

<a id="employer-profile"></a>
## Профиль работодателя (`employer-profile`)

Название компании (CAS).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/employer/profile` | работодатель (cookie или Bearer) | — |
| PATCH | `/employer/profile` | работодатель (cookie или Bearer) | да |

<a id="vacancy-pipeline"></a>
## Конвейер вакансии (внутренний) (`vacancy-pipeline`)

Проекция вакансии в matching, готовность, комбайн, реранк, подборки — [matching.md](matching.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/internal/vacancies/ready` | X-Internal-Token | — |
| GET | `/internal/vacancies/{vacancy_id}` | X-Internal-Token | — |
| PUT | `/internal/vacancies/{vacancy_id}` | X-Internal-Token | да |
| GET | `/internal/vacancies/{vacancy_id}/combine` | X-Internal-Token | — |
| POST | `/internal/vacancies/{vacancy_id}/combine` | X-Internal-Token | да |
| POST | `/internal/vacancies/{vacancy_id}/combine/retry` | X-Internal-Token | — |
| GET | `/internal/vacancies/{vacancy_id}/ready` | X-Internal-Token | — |
| GET | `/internal/vacancies/{vacancy_id}/rerank` | X-Internal-Token | — |
| POST | `/internal/vacancies/{vacancy_id}/rerank` | X-Internal-Token | да |
| POST | `/internal/vacancies/{vacancy_id}/rerank/retry` | X-Internal-Token | — |
| GET | `/internal/vacancies/{vacancy_id}/rerank/selections` | X-Internal-Token | — |
| POST | `/internal/vacancies/{vacancy_id}/retry` | X-Internal-Token | — |
| PUT | `/internal/vacancies/{vacancy_id}/selection-state/{candidate_id}` | X-Internal-Token | да |
| GET | `/internal/vacancies/{vacancy_id}/selections` | X-Internal-Token | — |

<a id="employer-feed"></a>
## Лента работодателя (`employer-feed`)

Свайпы, показы, сохранённые и посещённые, лайки витрины, preference и гипотезы — [feed-refill.md](feed-refill.md), [preference-learning.md](preference-learning.md), [hypothesis-learning.md](hypothesis-learning.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/employer/vacancy-drafts/{draft_id}/feed` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/decisions` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/decisions/{idempotency_key}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/exposures/{exposure_id}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/exposures/{exposure_id}/resume.pdf` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/impressions` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/impressions/{idempotency_key}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/{session_id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/{session_id}/refill` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/hypotheses` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/hypotheses/{action}` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/preferences` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/reset` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/retry` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/rollback` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/saved` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/saved/{candidate_id}` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/showcase-likes` | работодатель (cookie или Bearer) | да |
| GET | `/employer/vacancy-drafts/{draft_id}/showcase-likes/{idempotency_key}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/visited` | работодатель (cookie или Bearer) | — |

<a id="employer-selection"></a>
## Подборка вакансии (`employer-selection`)

Готовность, комбайн, реранк, подборки и резюме кандидата из подборки — [matching.md](matching.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/employer/vacancy-drafts/{draft_id}/candidates/{candidate_id}/resume.pdf` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/combine` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/combine` | работодатель (cookie или Bearer) | да |
| POST | `/employer/vacancy-drafts/{draft_id}/combine/retry` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/ready` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/rerank` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/rerank/retry` | работодатель (cookie или Bearer) | — |
| POST | `/employer/vacancy-drafts/{draft_id}/retry` | работодатель (cookie или Bearer) | — |
| GET | `/employer/vacancy-drafts/{draft_id}/selections` | работодатель (cookie или Bearer) | — |

<a id="employer-showcase"></a>
## Поиск по витрине кандидатов (`employer-showcase`)

Поиск, карточка и резюме — [candidate-showcase.md](candidate-showcase.md), [search-index.md](search-index.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/employer/candidates/search` | работодатель (cookie или Bearer) | да |
| GET | `/employer/candidates/{candidate_id}` | работодатель (cookie или Bearer) | — |
| GET | `/employer/candidates/{candidate_id}/resume.pdf` | работодатель (cookie или Bearer) | — |

<a id="employer-statistics"></a>
## Статистика работодателя (`employer-statistics`)

[employer-statistics.md](employer-statistics.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/employer/statistics` | работодатель (cookie или Bearer) | — |

<a id="organization-agent"></a>
## Инструменты агента организации (внутренний) (`organization-agent`)

Мост инструментов глобального агента — [agents.md](agents.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/internal/organization-agent/tools/{tool}` | X-Internal-Token | да |

<a id="public-showcase"></a>
## Публичная витрина (`public-showcase`)

[public-showcase.md](public-showcase.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/public/showcase/candidates` | без сессии | — |
| GET | `/public/showcase/vacancies` | без сессии | — |

<a id="support"></a>
## Консоль поддержки (`support`)

Регистрируются только при настроенных `SUPPORT_EMAIL`/`SUPPORT_ACCESS_KEY` — [support-console.md](support-console.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/support/conversations` | оператор | — |
| GET | `/support/conversations/{account_id}/messages` | оператор | — |
| POST | `/support/conversations/{account_id}/messages` | оператор | да |
| POST | `/support/conversations/{account_id}/read` | оператор | да |
| POST | `/support/login` | без сессии | да |
| POST | `/support/logout` | оператор | — |
| GET | `/support/me` | оператор | — |

<a id="agents"></a>
## Внутренний gateway agents-service (`agents`)

Доступ по `X-Internal-Token`; nginx скрывает `/api/internal/*` (404).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| GET | `/internal/agents/capabilities` | X-Internal-Token | — |
| GET | `/internal/agents/jobs/{job_id}` | X-Internal-Token | — |
| POST | `/internal/agents/jobs/{job_id}/retry` | X-Internal-Token | — |
| POST | `/internal/agents/sessions` | X-Internal-Token | да |
| GET | `/internal/agents/sessions/{session_id}` | X-Internal-Token | — |
| POST | `/internal/agents/sessions/{session_id}/interactions/{interaction_id}` | X-Internal-Token | да |
| POST | `/internal/agents/sessions/{session_id}/turns` | X-Internal-Token | да |
| GET | `/internal/agents/taxonomy` | X-Internal-Token | — |
| GET | `/internal/agents/vacancies` | X-Internal-Token | — |
| POST | `/internal/agents/vacancies` | X-Internal-Token | да |
| DELETE | `/internal/agents/vacancies/{draft_id}` | X-Internal-Token | да |
| GET | `/internal/agents/vacancies/{draft_id}` | X-Internal-Token | — |
| PATCH | `/internal/agents/vacancies/{draft_id}` | X-Internal-Token | да |
| POST | `/internal/agents/vacancies/{draft_id}/approve` | X-Internal-Token | да |
| POST | `/internal/agents/vacancies/{draft_id}/close` | X-Internal-Token | да |
| GET | `/internal/agents/vacancies/{draft_id}/deletion` | X-Internal-Token | — |
| GET | `/internal/agents/vacancies/{draft_id}/lifecycle/{expected_revision}/{action}` | X-Internal-Token | — |
| POST | `/internal/agents/vacancies/{draft_id}/publish` | X-Internal-Token | да |
| POST | `/internal/agents/vacancies/{draft_id}/withdraw` | X-Internal-Token | да |
| POST | `/internal/candidate-recommendations/generate` | X-Internal-Token | да |
| POST | `/internal/candidate-resume/parse` | X-Internal-Token | да |
| GET | `/internal/organization-agent/budget` | X-Internal-Token | — |
| GET | `/internal/organization-agent/chat` | X-Internal-Token | — |
| GET | `/internal/organization-agent/context` | X-Internal-Token | — |
| GET | `/internal/organization-agent/documents` | X-Internal-Token | — |
| POST | `/internal/organization-agent/documents` | X-Internal-Token | да |
| GET | `/internal/organization-agent/documents/{document_id}` | X-Internal-Token | — |
| GET | `/internal/organization-agent/documents/{document_id}/download` | X-Internal-Token | — |
| GET | `/internal/organization-agent/memory` | X-Internal-Token | — |
| PATCH | `/internal/organization-agent/memory` | X-Internal-Token | да |
| DELETE | `/internal/organization-agent/messages` | X-Internal-Token | да |
| GET | `/internal/organization-agent/messages` | X-Internal-Token | — |
| POST | `/internal/organization-agent/messages` | X-Internal-Token | да |
| GET | `/internal/organization-agent/pending-answers` | X-Internal-Token | — |
| POST | `/internal/organization-agent/pending-answers/{answer_id}` | X-Internal-Token | да |
| POST | `/internal/organization-agent/presence` | X-Internal-Token | да |
| PUT | `/internal/organization-agent/presence` | X-Internal-Token | да |
| GET | `/internal/organization-agent/tasks` | X-Internal-Token | — |
| POST | `/internal/organization-agent/tasks` | X-Internal-Token | да |
| DELETE | `/internal/organization-agent/tasks/{task_id}` | X-Internal-Token | — |
| POST | `/internal/organization-agent/tasks/{task_id}/availability` | X-Internal-Token | да |
| GET | `/internal/organization-agent/turns/{turn_id}` | X-Internal-Token | — |
| GET | `/internal/vacancy-drafts` | X-Internal-Token | — |
| POST | `/internal/vacancy-drafts` | X-Internal-Token | да |
| GET | `/internal/vacancy-drafts/{draft_id}` | X-Internal-Token | — |
| PATCH | `/internal/vacancy-drafts/{draft_id}` | X-Internal-Token | да |
| POST | `/internal/vacancy-drafts/{draft_id}/approve` | X-Internal-Token | да |
| POST | `/internal/vacancy-drafts/{draft_id}/create` | X-Internal-Token | да |
| POST | `/internal/vacancy-drafts/{draft_id}/enrich` | X-Internal-Token | да |
| GET | `/internal/vacancy-drafts/{draft_id}/pipeline` | X-Internal-Token | — |

<a id="internal-testing"></a>
## Доверенный приём результатов теста (`internal-testing`)

`POST /internal/testing/session-result` — [api.md](api.md).

| Метод | Путь | Доступ | Тело |
|---|---|---|---|
| POST | `/internal/testing/session-result` | X-Internal-Token | да |

## Как обновить справочник

Перечень строится из `app.swagger()` собранного сервера: запустите api-core с `SUPPORT_EMAIL`/`SUPPORT_ACCESS_KEY` (иначе раздел поддержки не появится) и откройте `/docs/json`. Число операций сверяется с [api.md](api.md).
