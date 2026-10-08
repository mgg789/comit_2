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
                                                          ▲
                             services/verification-engine (Python/FastAPI, :8105, только внутри compose)
```

## Сервисы

| Сервис | Стек | Ответственность | Почему отдельный |
|---|---|---|---|
| `apps/api` | Fastify, TS (NodeNext ESM) | Email OTP auth, custom JWT + revocable DB sessions, account-owned candidate source profile, employer owner gateway, вакансии/приглашения, OpenAPI | Транзакционная логика, целостность |
| `services/matching-engine` | FastAPI, pgvector | Комбайн: strict filter → hybrid scoring → top-k → реранкер; вектора кандидата/вакансии; тиндер-фидбек; объяснимость выдачи | ML-эксперименты на Python, независимые релизы |
| `services/testing-engine` | FastAPI | Банк `blueprints/`, seeded-варианты, автопроверка, адаптивный грейд-тест, cooldown смены грейда | Изолированный CPU-интенсивный домен |
| `services/integration-engine` | FastAPI | Мок-реестр ФСП (`/fsp/participants/{id}`, `/fsp/verify`, достижения) и демо-привязка (`/fsp/demo/authorize`, `/fsp/demo/token`) | Внешние источники меняются независимо от ядра |
| `services/verification-engine` | FastAPI | Внутренние `/verify/fsp`, `/verify/repository`, `/verify/hh` под сервисным токеном; без candidate id ([verification.md](verification.md)) | Сетевые проверки внешних провайдеров изолированы от ядра |
| `services/agents-service` | FastAPI, PostgreSQL | Общий черновик формы/чата, Laya, durable turns, summary → Create; глобальный агент работодателя (`/employer/organization-agent`, `organization-worker`, [agents.md](agents.md)) | Отдельные знания, инструменты и провайдеры |
| `apps/{candidate,employer,testing,welcome}` | React 19, Vite, Tailwind 4 | Кабинеты по ролям + витрина | Один nginx, 4 сборки (`infra/web.Dockerfile`) |

Чаты на движке-протоколе Mike (QGramm) интегрированы в пост-отбор отдельным
opt-in overlay: маршруты `/{role}/recruitment/{id}/chat/*` проверяют сессию,
доступ по приглашению и текущее согласие под DB-locks. Frontend и public hiring
chat остаются отдельными этапами; matching selection gateway уже даёт owner boundary.

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
vacancy worker → ready; combine worker → main top-k; rerank worker → sort-only
порядок активного R₀ либо явный main_fallback (упакованный hash-pinned артефакт
активирован ручным решением оператора, quality gate остаётся failed, хвост не
отсекается). Preference-worker автоматически обучает vacancy-local поправку по
реакциям ленты и переставляет только непоказанный суффикс; каждый участник
обучения требует отдельного training-v1 consent. Research-показы гипотез
(2 карточки на 18 обычных доставок) добавляются после preference refresh;
feed-refill-worker добирает ленту immutable batch-ами ([feed-refill.md](feed-refill.md)).
Runtime использует JSON linear artifacts без sklearn/LLM; operator-обучение
реранкера — отдельный workflow, численный activation gate не пройден.
Контракты и приёмка — [matching.md](matching.md), [runbook.md](runbook.md).
Перечисленные ниже полные пользовательские потоки включают последующие этапы MVP.

1. Регистрация кандидата: email-подтверждение → CAS source profile → отдельные profile/pool consents → exact snapshot projection. Browser projection выдаёт self_reported; подготовленный trusted replacement producer позднее сможет заменить его через API source/evidence и новую outbox запись.
2. Создание вакансии: строгие параметры + ЗП «от–до» (обязательна) + описание → вектор в `matching-engine` → ready/open → employer combine → Selection с объяснением.
3. Тиндер-лента: порции из готового R₀ top-k с обоснованием → лайк → saved; скип → immutable структурный снимок и блокировка нового отбора до достаточного изменения профиля; непоказанный суффикс может переставляться preference-моделью, в порцию добавляются парные research-карточки.
4. Приглашение: ЗП обязательна; статусы у обеих сторон; контакты раскрываются только после принятия (п. 2.2 ТЗ); после принятия доступен приватный чат QGramm.
5. Переотбор: событие `candidate.ready` сравнивает профиль с исходным снимком dislike; при превышении порога (structural distance > 1.0, строгий N > K) кандидат возвращается через `visited_updated`. Автоматическое пополнение (добор) короткой выдачи не реализовано.

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
| verification-engine | 8105 внутри compose, без host-порта | `PORT` |
| postgres | 5433→5432 | `POSTGRES_PORT` |

В prod (`infra/docker-compose.prod.yml`) наружу публикуется только web; api и engines доступны лишь на `127.0.0.1` сервера (отладка и smoke), БД — только внутренняя сеть. verification-engine пока есть только в dev compose (`infra/docker-compose.yml`), в prod compose его нет.

## Hiring aggregate после подбора

RecruitmentStore в api-core владеет lifecycle и транзакциями обеих ролей;
authDomain инжектирует тот же API Pool/AuthStore/ProfileStore. Нового сервиса
или внешнего SDK не требуется. Matching владеет saved/grant/exclusions, agents
— опубликованной вакансией; API читает их через service-auth и сохраняет
условия предложения. Контакты берутся только из текущего API source profile
после принятия. Durable participant events отделены от будущих SMTP
потребителей. [Контур и границы](recruitment.md).

QGramm подключается отдельным opt-in образом BASIC + files через приватную
сеть `chat-private`. Go-адаптер использует ядро in-process и не публикует raw
QGramm endpoints; api-core проверяет сессию, приглашение и текущее согласие
под DB-locks на каждый вызов. Постоянные сообщения/файлы принадлежат SQLite
QGramm, а решения найма — PostgreSQL API. [Контракт и ключи](chat.md).

Общая витрина использует matching-engine для независимых lexical/Harrier
каналов и фильтров актуального пула. Api-core выводит owner из сессии,
проверяет source consent/revision и закрывает частный PDF. Ручной лайк
транзакционно использует FeedStore и существующий recruitment; отдельный
сервис или ML-запуск для него не создаётся. [Контракт](candidate-showcase.md).
