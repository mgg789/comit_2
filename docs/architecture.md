# Архитектура — Comit

> Статус: каркас (S1). Канон-контекст: `context/40-architecture.md`; здесь — актуальное состояние кода.

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
| `apps/api` | Fastify, TS (NodeNext ESM) | Auth (email+JWT), профили, вакансии, приглашения/статусы, rating ledger, правила видимости п. 2.2 ТЗ, OpenAPI | Транзакционная логика, целостность |
| `services/matching-engine` | FastAPI, pgvector | Комбайн: strict filter → hybrid scoring → top-k → реранкер; вектора кандидата/вакансии; тиндер-фидбек; объяснимость выдачи | ML-эксперименты на Python, независимые релизы |
| `services/testing-engine` | FastAPI | Банк `blueprints/`, seeded-варианты, автопроверка, адаптивный грейд-тест, cooldown смены грейда | Изолированный CPU-интенсивный домен |
| `services/integration-engine` | FastAPI | Верификация внешних данных; мок-реестр ФСП (`/fsp/participants/{id}`, `/fsp/verify`) | Внешние источники меняются независимо от ядра |
| `apps/{candidate,employer,testing,welcome}` | React 19, Vite, Tailwind 4 | Кабинеты по ролям + витрина | Один nginx, 4 сборки (`infra/web.Dockerfile`) |

Чаты (движок-протокол Mike) — третий спринт, интегрируется в пост-отбор (context/80, решение Q3).

## Ключевые потоки

1. Регистрация кандидата: email-подтверждение → опрос специализации → заявленный грейд → `testing-engine` формирует вариант → результат → категория → профиль в пуле.
2. Создание вакансии: строгие параметры + ЗП «от–до» (обязательна) + описание → вектор в `matching-engine` → Active → комбайн → Selection с объяснением → HasCandidates.
3. Тиндер: карточки из Selected с обоснованием → лайк/скип → коррекция вектора вакансии → следующая карточка.
4. Приглашение: ЗП обязательна; статусы у обеих сторон; контакты раскрываются только после принятия (п. 2.2 ТЗ).
5. Добор: события изменения пула → порог изменчивости → переотбор без Visited/Pending/Attractive.

## Правила

- Каждый сервис отвечает `GET /healthz` (контракт smoke-гейта); `/ready` — для зависимостей.
- Контракты типов — `packages/shared`; OpenAPI — схемы Fastify (api) и pydantic-модели (engines).
- Веса скоринга и формулы живут только в `matching-engine`.
- БД: одна PostgreSQL, схемы по сервисам; миграции — с S1/S3 (docs/data.md).

## Порты (dev)

| Сервис | Порт | Env |
|---|---|---|
| web (nginx) | 8088 | `WEB_PORT` |
| api-core | 8080 | `API_PORT` |
| matching-engine | 8101 | `MATCHING_PORT` |
| testing-engine | 8102 | `TESTING_PORT` |
| integration-engine | 8103 | `INTEGRATION_PORT` |
| postgres | 5433→5432 | `POSTGRES_PORT` |

В prod (`infra/docker-compose.prod.yml`) публикуется только web; engines и БД — внутренняя сеть.
