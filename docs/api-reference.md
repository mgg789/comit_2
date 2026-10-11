# Справочник маршрутов api-core

Полный перечень из OpenAPI текущего api-core: **263 операций** при настроенной поддержке. Браузер добавляет префикс `/api`; внешние `/api/internal/*` и `/api/metrics` закрыты gateway. Подробности: [api.md](api.md).

## Группы

| OpenAPI tag | Операций |
|---|---|
| auth | 16 |
| candidate-account | 3 |
| system-inbox | 12 |
| candidate-profile | 8 |
| candidate-verification | 10 |
| candidate-recommendations | 1 |
| recruitment | 15 |
| chat | 12 |
| candidate-testing | 9 |
| candidate-weekly-challenge | 3 |
| employer-account | 49 |
| employer-showcase | 3 |
| employer-profile | 2 |
| employer-statistics | 1 |
| employer-selection | 9 |
| employer-feed | 20 |
| health | 2 |
| agents | 50 |
| candidate-pipeline | 9 |
| organization-agent | 1 |
| internal-testing | 1 |
| vacancy-pipeline | 14 |
| operations | 1 |
| public-showcase | 3 |
| support | 9 |

## auth

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/auth/candidate/code` | без сессии |
| POST | `/auth/candidate/logout` | bearerAuth, candidateSession |
| POST | `/auth/candidate/logout-all` | bearerAuth, candidateSession |
| GET | `/auth/candidate/me` | bearerAuth, candidateSession |
| GET | `/auth/candidate/providers` | без сессии |
| POST | `/auth/candidate/verify` | без сессии |
| GET | `/auth/candidate/yandex/callback` | без сессии |
| POST | `/auth/candidate/yandex/start` | без сессии |
| POST | `/auth/employer/code` | без сессии |
| POST | `/auth/employer/logout` | bearerAuth, employerSession |
| POST | `/auth/employer/logout-all` | bearerAuth, employerSession |
| GET | `/auth/employer/me` | bearerAuth, employerSession |
| GET | `/auth/employer/providers` | без сессии |
| POST | `/auth/employer/verify` | без сессии |
| GET | `/auth/employer/yandex/callback` | без сессии |
| POST | `/auth/employer/yandex/start` | без сессии |

## candidate-account

| Метод | Путь | Доступ |
|---|---|---|
| DELETE | `/candidate/account` | bearerAuth, candidateSession |
| GET | `/candidate/pipeline` | bearerAuth, candidateSession |
| GET | `/candidate/resume.pdf` | bearerAuth, candidateSession |

## system-inbox

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/chat-unread` | bearerAuth, candidateSession |
| GET | `/candidate/system-chat` | bearerAuth, candidateSession |
| POST | `/candidate/system-chat/files` | bearerAuth, candidateSession |
| GET | `/candidate/system-chat/files/{file_id}` | bearerAuth, candidateSession |
| POST | `/candidate/system-chat/messages` | bearerAuth, candidateSession |
| POST | `/candidate/system-chat/read` | bearerAuth, candidateSession |
| GET | `/employer/chat-unread` | bearerAuth, employerSession |
| GET | `/employer/system-chat` | bearerAuth, employerSession |
| POST | `/employer/system-chat/files` | bearerAuth, employerSession |
| GET | `/employer/system-chat/files/{file_id}` | bearerAuth, employerSession |
| POST | `/employer/system-chat/messages` | bearerAuth, employerSession |
| POST | `/employer/system-chat/read` | bearerAuth, employerSession |

## candidate-profile

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/profile` | bearerAuth, candidateSession |
| PUT | `/candidate/profile` | bearerAuth, candidateSession |
| PATCH | `/candidate/profile` | bearerAuth, candidateSession |
| POST | `/candidate/profile/import` | bearerAuth, candidateSession |
| POST | `/candidate/profile/projection/retry` | bearerAuth, candidateSession |
| GET | `/candidate/skills/catalog` | bearerAuth, candidateSession |
| GET | `/candidate/taxonomy` | bearerAuth, candidateSession |
| GET | `/candidate/testing-history` | bearerAuth, candidateSession |

## candidate-verification

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/candidate/profile/fsp/demo/approve` | bearerAuth, candidateSession |
| POST | `/candidate/profile/fsp/demo/authorize` | bearerAuth, candidateSession |
| POST | `/candidate/profile/fsp/demo/callback` | bearerAuth, candidateSession |
| GET | `/candidate/profile/fsp/status` | bearerAuth, candidateSession |
| POST | `/candidate/profile/verification-documents` | bearerAuth, candidateSession |
| GET | `/candidate/profile/verification-documents` | bearerAuth, candidateSession |
| GET | `/candidate/profile/verification-documents/{id}` | bearerAuth, candidateSession |
| POST | `/candidate/profile/verifications/fsp` | bearerAuth, candidateSession |
| POST | `/candidate/profile/verifications/hh` | bearerAuth, candidateSession |
| POST | `/candidate/profile/verifications/repository` | bearerAuth, candidateSession |

## candidate-recommendations

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/recommendations/latest` | bearerAuth, candidateSession |

## recruitment

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/recruitment` | bearerAuth, candidateSession |
| GET | `/candidate/recruitment-events` | bearerAuth, candidateSession |
| GET | `/candidate/recruitment-events/stream` | bearerAuth, candidateSession |
| GET | `/candidate/recruitment/{id}` | bearerAuth, candidateSession |
| POST | `/candidate/recruitment/{id}/actions` | bearerAuth, candidateSession |
| POST | `/employer/contacts` | bearerAuth, employerSession |
| GET | `/employer/recruitment` | bearerAuth, employerSession |
| GET | `/employer/recruitment-events` | bearerAuth, employerSession |
| GET | `/employer/recruitment-events/stream` | bearerAuth, employerSession |
| GET | `/employer/recruitment/{id}` | bearerAuth, employerSession |
| POST | `/employer/recruitment/{id}/actions` | bearerAuth, employerSession |
| GET | `/employer/recruitment/{id}/contacts` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/invitations` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/recruitment` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/recruitment/reject` | bearerAuth, employerSession |

## chat

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/recruitment/{id}/chat/events` | bearerAuth, candidateSession |
| POST | `/candidate/recruitment/{id}/chat/files` | bearerAuth, candidateSession |
| GET | `/candidate/recruitment/{id}/chat/files/{file_id}` | bearerAuth, candidateSession |
| GET | `/candidate/recruitment/{id}/chat/messages` | bearerAuth, candidateSession |
| POST | `/candidate/recruitment/{id}/chat/messages` | bearerAuth, candidateSession |
| POST | `/candidate/recruitment/{id}/chat/read` | bearerAuth, candidateSession |
| GET | `/employer/recruitment/{id}/chat/events` | bearerAuth, employerSession |
| POST | `/employer/recruitment/{id}/chat/files` | bearerAuth, employerSession |
| GET | `/employer/recruitment/{id}/chat/files/{file_id}` | bearerAuth, employerSession |
| GET | `/employer/recruitment/{id}/chat/messages` | bearerAuth, employerSession |
| POST | `/employer/recruitment/{id}/chat/messages` | bearerAuth, employerSession |
| POST | `/employer/recruitment/{id}/chat/read` | bearerAuth, employerSession |

## candidate-testing

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/testing/preflight` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions/{sessionId}/abandon` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions/{sessionId}/answer` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions/{sessionId}/finish` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions/{sessionId}/media` | candidateSession, bearerAuth |
| GET | `/candidate/testing/sessions/{sessionId}/next` | candidateSession, bearerAuth |
| GET | `/candidate/testing/sessions/{sessionId}/result` | candidateSession, bearerAuth |
| POST | `/candidate/testing/sessions/{sessionId}/supervision` | candidateSession, bearerAuth |

## candidate-weekly-challenge

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/candidate/weekly-challenge` | candidateSession, bearerAuth |
| POST | `/candidate/weekly-challenge/start` | candidateSession, bearerAuth |
| POST | `/candidate/weekly-challenge/submit` | candidateSession, bearerAuth |

## employer-account

| Метод | Путь | Доступ |
|---|---|---|
| DELETE | `/employer/account` | bearerAuth, employerSession |
| GET | `/employer/agents/capabilities` | bearerAuth, employerSession |
| GET | `/employer/agents/jobs/{job_id}` | bearerAuth, employerSession |
| POST | `/employer/agents/jobs/{job_id}/retry` | bearerAuth, employerSession |
| POST | `/employer/agents/sessions` | bearerAuth, employerSession |
| GET | `/employer/agents/sessions/{session_id}` | bearerAuth, employerSession |
| POST | `/employer/agents/sessions/{session_id}/interactions/{interaction_id}` | bearerAuth, employerSession |
| POST | `/employer/agents/sessions/{session_id}/turns` | bearerAuth, employerSession |
| GET | `/employer/agents/taxonomy` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/budget` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/chat` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/context` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/documents` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/documents` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/documents/{document_id}` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/documents/{document_id}/download` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/memory` | bearerAuth, employerSession |
| PATCH | `/employer/organization-agent/memory` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/messages` | bearerAuth, employerSession |
| DELETE | `/employer/organization-agent/messages` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/messages` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/pending-answers` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/pending-answers/{answer_id}` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/presence` | bearerAuth, employerSession |
| PUT | `/employer/organization-agent/presence` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/tasks` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/tasks` | bearerAuth, employerSession |
| DELETE | `/employer/organization-agent/tasks/{task_id}` | bearerAuth, employerSession |
| POST | `/employer/organization-agent/tasks/{task_id}/availability` | bearerAuth, employerSession |
| GET | `/employer/organization-agent/turns/{turn_id}` | bearerAuth, employerSession |
| GET | `/employer/vacancies` | bearerAuth, employerSession |
| POST | `/employer/vacancies` | bearerAuth, employerSession |
| GET | `/employer/vacancies/{draft_id}` | bearerAuth, employerSession |
| PATCH | `/employer/vacancies/{draft_id}` | bearerAuth, employerSession |
| DELETE | `/employer/vacancies/{draft_id}` | bearerAuth, employerSession |
| POST | `/employer/vacancies/{draft_id}/approve` | bearerAuth, employerSession |
| POST | `/employer/vacancies/{draft_id}/close` | bearerAuth, employerSession |
| GET | `/employer/vacancies/{draft_id}/deletion` | bearerAuth, employerSession |
| GET | `/employer/vacancies/{draft_id}/lifecycle/{expected_revision}/{action}` | bearerAuth, employerSession |
| POST | `/employer/vacancies/{draft_id}/publish` | bearerAuth, employerSession |
| POST | `/employer/vacancies/{draft_id}/withdraw` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}` | bearerAuth, employerSession |
| PATCH | `/employer/vacancy-drafts/{draft_id}` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/approve` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/create` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/enrich` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/pipeline` | bearerAuth, employerSession |

## employer-showcase

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/employer/candidates/search` | bearerAuth, employerSession |
| GET | `/employer/candidates/{candidate_id}` | bearerAuth, employerSession |
| GET | `/employer/candidates/{candidate_id}/resume.pdf` | bearerAuth, employerSession |

## employer-profile

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/employer/profile` | bearerAuth, employerSession |
| PATCH | `/employer/profile` | bearerAuth, employerSession |

## employer-statistics

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/employer/statistics` | bearerAuth, employerSession |

## employer-selection

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/employer/vacancy-drafts/{draft_id}/candidates/{candidate_id}/resume.pdf` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/combine` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/combine` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/combine/retry` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/ready` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/rerank` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/rerank/retry` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/retry` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/selections` | bearerAuth, employerSession |

## employer-feed

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/employer/vacancy-drafts/{draft_id}/feed` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/decisions` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/decisions/{idempotency_key}` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/exposures/{exposure_id}` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/exposures/{exposure_id}/resume.pdf` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/impressions` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/impressions/{idempotency_key}` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/feed/{session_id}` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/feed/{session_id}/refill` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/hypotheses` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/hypotheses/{action}` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/preferences` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/reset` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/retry` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/preferences/rollback` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/saved` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/saved/{candidate_id}` | bearerAuth, employerSession |
| POST | `/employer/vacancy-drafts/{draft_id}/showcase-likes` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/showcase-likes/{idempotency_key}` | bearerAuth, employerSession |
| GET | `/employer/vacancy-drafts/{draft_id}/visited` | bearerAuth, employerSession |

## health

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/healthz` | без сессии |
| GET | `/ready` | без сессии |

## agents

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/internal/agents/capabilities` | internalToken |
| GET | `/internal/agents/jobs/{job_id}` | internalToken |
| POST | `/internal/agents/jobs/{job_id}/retry` | internalToken |
| POST | `/internal/agents/sessions` | internalToken |
| GET | `/internal/agents/sessions/{session_id}` | internalToken |
| POST | `/internal/agents/sessions/{session_id}/interactions/{interaction_id}` | internalToken |
| POST | `/internal/agents/sessions/{session_id}/turns` | internalToken |
| GET | `/internal/agents/taxonomy` | internalToken |
| GET | `/internal/agents/vacancies` | internalToken |
| POST | `/internal/agents/vacancies` | internalToken |
| GET | `/internal/agents/vacancies/{draft_id}` | internalToken |
| PATCH | `/internal/agents/vacancies/{draft_id}` | internalToken |
| DELETE | `/internal/agents/vacancies/{draft_id}` | internalToken |
| POST | `/internal/agents/vacancies/{draft_id}/approve` | internalToken |
| POST | `/internal/agents/vacancies/{draft_id}/close` | internalToken |
| GET | `/internal/agents/vacancies/{draft_id}/deletion` | internalToken |
| GET | `/internal/agents/vacancies/{draft_id}/lifecycle/{expected_revision}/{action}` | internalToken |
| POST | `/internal/agents/vacancies/{draft_id}/publish` | internalToken |
| POST | `/internal/agents/vacancies/{draft_id}/withdraw` | internalToken |
| POST | `/internal/candidate-recommendations/generate` | internalToken |
| POST | `/internal/candidate-resume/parse` | internalToken |
| GET | `/internal/organization-agent/budget` | internalToken |
| GET | `/internal/organization-agent/chat` | internalToken |
| GET | `/internal/organization-agent/context` | internalToken |
| POST | `/internal/organization-agent/documents` | internalToken |
| GET | `/internal/organization-agent/documents` | internalToken |
| GET | `/internal/organization-agent/documents/{document_id}` | internalToken |
| GET | `/internal/organization-agent/documents/{document_id}/download` | internalToken |
| GET | `/internal/organization-agent/memory` | internalToken |
| PATCH | `/internal/organization-agent/memory` | internalToken |
| GET | `/internal/organization-agent/messages` | internalToken |
| DELETE | `/internal/organization-agent/messages` | internalToken |
| POST | `/internal/organization-agent/messages` | internalToken |
| GET | `/internal/organization-agent/pending-answers` | internalToken |
| POST | `/internal/organization-agent/pending-answers/{answer_id}` | internalToken |
| POST | `/internal/organization-agent/presence` | internalToken |
| PUT | `/internal/organization-agent/presence` | internalToken |
| GET | `/internal/organization-agent/tasks` | internalToken |
| POST | `/internal/organization-agent/tasks` | internalToken |
| DELETE | `/internal/organization-agent/tasks/{task_id}` | internalToken |
| POST | `/internal/organization-agent/tasks/{task_id}/availability` | internalToken |
| GET | `/internal/organization-agent/turns/{turn_id}` | internalToken |
| POST | `/internal/vacancy-drafts` | internalToken |
| GET | `/internal/vacancy-drafts` | internalToken |
| GET | `/internal/vacancy-drafts/{draft_id}` | internalToken |
| PATCH | `/internal/vacancy-drafts/{draft_id}` | internalToken |
| POST | `/internal/vacancy-drafts/{draft_id}/approve` | internalToken |
| POST | `/internal/vacancy-drafts/{draft_id}/create` | internalToken |
| POST | `/internal/vacancy-drafts/{draft_id}/enrich` | internalToken |
| GET | `/internal/vacancy-drafts/{draft_id}/pipeline` | internalToken |

## candidate-pipeline

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/internal/candidates/{candidate_id}` | internalToken |
| PUT | `/internal/candidates/{candidate_id}/profile` | internalToken |
| POST | `/internal/candidates/{candidate_id}/retry` | internalToken |
| GET | `/internal/matching/events` | internalToken |
| POST | `/internal/matching/events/retry` | internalToken |
| GET | `/internal/pool` | internalToken |
| GET | `/internal/pool/{candidate_id}/resume.pdf` | internalToken |
| GET | `/internal/strict-schema` | internalToken |
| POST | `/internal/strict-schema/coordinates` | internalToken |

## organization-agent

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/internal/organization-agent/tools/{tool}` | internalToken |

## internal-testing

| Метод | Путь | Доступ |
|---|---|---|
| POST | `/internal/testing/session-result` | internalToken |

## vacancy-pipeline

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/internal/vacancies/ready` | internalToken |
| PUT | `/internal/vacancies/{vacancy_id}` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}` | internalToken |
| POST | `/internal/vacancies/{vacancy_id}/combine` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}/combine` | internalToken |
| POST | `/internal/vacancies/{vacancy_id}/combine/retry` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}/ready` | internalToken |
| POST | `/internal/vacancies/{vacancy_id}/rerank` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}/rerank` | internalToken |
| POST | `/internal/vacancies/{vacancy_id}/rerank/retry` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}/rerank/selections` | internalToken |
| POST | `/internal/vacancies/{vacancy_id}/retry` | internalToken |
| PUT | `/internal/vacancies/{vacancy_id}/selection-state/{candidate_id}` | internalToken |
| GET | `/internal/vacancies/{vacancy_id}/selections` | internalToken |

## operations

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/metrics` | без сессии |

## public-showcase

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/public/salary-market` | без сессии |
| GET | `/public/showcase/candidates` | без сессии |
| GET | `/public/showcase/vacancies` | без сессии |

## support

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/support/conversations` | supportSession |
| POST | `/support/conversations/{account_id}/files` | supportSession |
| GET | `/support/conversations/{account_id}/files/{file_id}` | supportSession |
| GET | `/support/conversations/{account_id}/messages` | supportSession |
| POST | `/support/conversations/{account_id}/messages` | supportSession |
| POST | `/support/conversations/{account_id}/read` | supportSession |
| POST | `/support/login` | без сессии |
| POST | `/support/logout` | supportSession |
| GET | `/support/me` | supportSession |

## Обновление

`pnpm --filter @comit/api exec tsx scripts/generate-api-reference.ts` обновляет документ из зарегистрированных схем. С `--check` команда проверяет точное совпадение и число операций. Генератор использует синтетическую конфигурацию, не запускает HTTP-сервер и не читает production env.
