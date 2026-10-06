# Архитектура — Comit

> Статус: backend первого цикла; email auth, account-owned candidate profile и
> employer owner gateway реализованы, frontend и testing provider подключаются
> отдельными этапами.
> Канон-контекст: `context/40-architecture.md`; здесь — актуальное состояние кода.

> Цикл 1: реализован серверный путь от собранного upstream-профиля до готового пула.
> API-core проверяет сервисный токен и проксирует allowlisted internal методы;
> matching-engine хранит собственную JSONB-проекцию, версии координат, durable jobs,
> два вектора, PDF и outbox в одной PostgreSQL. Отдельный worker строит артефакты;
> отдельный migration job запускается перед serve/worker через local overlay.
> API-core хранит source profile и durable projection outbox, а matching-engine
> принимает только exact trusted snapshot. Auth имеет собственную PostgreSQL
> migration/outbox/session границу; тестовые сессии остаются trusted upstream
> контуром. UI и production acceptance этим документом не подтверждаются.

## Контур

```
web (nginx, один контейнер: welcome /candidate /employer /testing)
        │ /api/*
        ▼
api-core (Fastify)  ────────────  PostgreSQL 16 + pgvector
        ▲
services/matching-engine     services/testing-engine     services/integration-engine
(Python/FastAPI, :8101)      (Python/FastAPI, :8102)     (Python/FastAPI, :8103)
```

## Сервисы

| Сервис | Стек | Ответственность | Почему отдельный |
|---|---|---|---|
| `apps/api` | Fastify, TS (NodeNext ESM) | Email OTP auth, custom JWT + revocable DB sessions, account-owned candidate source profile, employer owner gateway, вакансии/приглашения, OpenAPI | Транзакционная логика, целостность |
| `services/matching-engine` | FastAPI, pgvector | Комбайн: strict filter → hybrid scoring → top-k → реранкер; вектора кандидата/вакансии; тиндер-фидбек; объяснимость выдачи | ML-эксперименты на Python, независимые релизы |
| `services/testing-engine` | FastAPI | Банк `blueprints/`, seeded-варианты, автопроверка, адаптивный грейд-тест, cooldown смены грейда | Изолированный CPU-интенсивный домен |
| `services/integration-engine` | FastAPI | Верификация внешних данных; мок-реестр ФСП (`/fsp/participants/{id}`, `/fsp/verify`) | Внешние источники меняются независимо от ядра |
| `services/agents-service` | FastAPI, PostgreSQL | Общий черновик формы/чата, Laya, durable turns, summary → Create | Отдельные знания, инструменты и провайдеры |
| `apps/{candidate,employer,testing,welcome}` | React 19, Vite, Tailwind 4 | Кабинеты по ролям + витрина | Один nginx, 4 сборки (`infra/web.Dockerfile`) |

Чаты (движок-протокол Mike) — третий спринт, интегрируется в пост-отбор
(`context/80`, решение Q3); account/draft gateway уже даёт owner boundary, но
public hiring chat и frontend остаются отдельными этапами; matching selection
gateway уже даёт owner boundary.

## Auth и ownership

Нормализованный email глобально уникален и навсегда связан с одной ролью:
`candidate` или `employer`. Пароли не используются: email OTP имеет пять попыток,
single-use expiry и durable PostgreSQL SMTP outbox. После проверки API выдаёт
HS256 JWT с role-bound claims и случайным sid; PostgreSQL хранит только hash sid,
поэтому logout/revoke действует до истечения JWT. Account consent на обработку
данных отделён от согласия candidate profile на публикацию в пуле.

Public employer draft/agent, candidate profile и candidate pipeline/PDF paths
получают account ID из проверенного JWT или HttpOnly role cookie. Клиент не может
выбрать owner через `X-Owner-ID` или query; internal service-to-service endpoints
по-прежнему требуют сервисный token. Employer selection gateway дополнительно
проверяет owner draft, publication state, текущий source watermark и consent
каждого выбранного кандидата; foreign/absent records дают единый 404.

## Ключевые потоки

Актуальная серверная реализация: candidate worker → dual-vector pool/PDF;
vacancy worker → ready; combine worker → main top-k; rerank worker →
рекомендованный порядок/резерв либо явный fallback. Постановка R₀ атомарна с main
publication; активная модель хранится в immutable PG registry/settings pointer.
Обучение выполняет отдельный operator workflow; runtime использует JSON linear
artifact без sklearn/LLM. Teacher pilot остановлен, активного learned artifact нет.
Контракты и приёмка — [matching.md](matching.md), [runbook.md](runbook.md).
Перечисленные ниже полные пользовательские потоки включают последующие этапы MVP.

1. Регистрация кандидата: email-подтверждение → CAS source profile → отдельные profile/pool consents → exact snapshot projection. Browser projection выдаёт self_reported; подготовленный trusted replacement producer позднее сможет заменить его через API source/evidence и новую outbox запись.
2. Создание вакансии: строгие параметры + ЗП «от–до» (обязательна) + описание → вектор в `matching-engine` → ready/open → employer combine → Selection с объяснением.
3. Тиндер: карточки из Selected с обоснованием → лайк/скип → коррекция вектора вакансии → следующая карточка.
4. Приглашение: ЗП обязательна; статусы у обеих сторон; контакты раскрываются только после принятия (п. 2.2 ТЗ).
5. Добор: события изменения пула → порог изменчивости → переотбор без Visited/Pending/Attractive.

## Правила

- Каждый сервис отвечает `GET /healthz` (контракт smoke-гейта); `/ready` — для зависимостей.
- Контракты типов — `packages/shared`; OpenAPI — схемы Fastify (api) и pydantic-модели (engines).
- Веса скоринга и формулы живут только в `matching-engine`.
- Source profile CAS/evidence принадлежат API-core; matching получает exact snapshot через durable outbox.
- ACK outbox означает доставку snapshot, а не готовность vectors/PDF/pool.
- БД: одна PostgreSQL, схемы по сервисам; auth миграция применяется явно до serve,
  matching/agents миграции имеют собственные runners (docs/data.md).

## Порты (dev)

| Сервис | Порт | Env |
|---|---|---|
| web (nginx) | 8088 | `WEB_PORT` |
| api-core | 8080 | `API_PORT` |
| matching-engine | 8101 | `MATCHING_PORT` |
| testing-engine | 8102 | `TESTING_PORT` |
| integration-engine | 8103 | `INTEGRATION_PORT` |
| agents-service | 8104 внутри compose | `PORT`, opt-in overlay |
| postgres | 5433→5432 | `POSTGRES_PORT` |

В prod (`infra/docker-compose.prod.yml`) наружу публикуется только web; api и engines доступны лишь на `127.0.0.1` сервера (отладка и smoke), БД — только внутренняя сеть.

## Hiring aggregate после подбора

RecruitmentStore в api-core владеет lifecycle и транзакциями обеих ролей;
authDomain инжектирует тот же API Pool/AuthStore/ProfileStore. Нового сервиса
или внешнего SDK не требуется. Matching владеет saved/grant/exclusions, agents
— опубликованной вакансией; API читает их через service-auth и сохраняет
условия предложения. Контакты берутся только из текущего API source profile
после принятия. Durable participant events отделены от будущих SMTP/chat
потребителей. [Контур и границы](recruitment.md).
