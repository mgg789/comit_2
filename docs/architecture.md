# Архитектура — Comit

Интерфейс работодателя реализован в `apps/employer`: hash-навигация, вакансии,
подбор, анонимные резюме, переписка, статистика и постоянный AI overlay.
[Страницы, запуск и границы интеграции](employer-ui.md). UI использует общий SDK
и существующие серверные проверки; формулы подбора на клиент не переносятся.

Кабинет кандидата (`apps/candidate`) использует тот же `@comit/ui-kit`: вход по почте,
собственное резюме, приглашения работодателей и переписку после принятия.
[Экраны и границы интеграции](candidate-ui.md), [общие компоненты](ui-kit.md).
Показатели без публичного API отображаются как недоступные, без примерных цифр.

> Статус (11.10.2026): email auth, account-owned candidate profile, employer
> owner gateway, кабинеты кандидата/работодателя и общая витрина реализованы и
> собираются в один web-образ. Testing UI не публикуется; адаптивный контур
> testing-engine есть в коде, но сервисный entrypoint запускает только
> blueprint-реестр ([testing-engine.md](testing-engine.md)).
> Канон-контекст: `context/40-architecture.md`; здесь — актуальное состояние кода.

> Цикл 1: реализован серверный путь от собранного upstream-профиля до готового пула.
> API-core проверяет сервисный токен и проксирует allowlisted internal методы;
> matching-engine хранит собственную JSONB-проекцию, версии координат, durable jobs,
> два вектора, PDF и outbox в одной PostgreSQL. Отдельный worker строит артефакты;
> отдельный migration job (`candidate-migrate`) запускается перед serve/worker в
> основном compose.
> API-core хранит source profile и durable projection outbox, а matching-engine
> принимает только exact trusted snapshot. Auth имеет собственную PostgreSQL
> migration/outbox/session границу; тестовые сессии остаются trusted upstream
> контуром (`POST /internal/testing/session-result`). Production acceptance этим
> документом не подтверждается.

## Контур

```
web (nginx, один контейнер: welcome / , /candidate/, /employer/; /testing/ → 404)
        │ /api/*  (/api/internal/* → 404)
        ▼
api-core (Fastify, :8080)  ────────────  PostgreSQL 16 + pgvector
        ▲                                 ▲ workers: candidate/vacancy/combine/rerank/
        │                                 │ feed-refill/preference, agents, organization,
        │                                 │ auth-mail, candidate-profile, recommendation
services/matching-engine     services/testing-engine     services/integration-engine
(Python/FastAPI, :8101)      (Python/FastAPI, :8102)     (Python/FastAPI, :8103)
services/agents-service      services/verification-engine qgramm-chat (Go bridge, :8080,
(Python/FastAPI, :8104)      (Python/FastAPI, :8105)      сеть chat-private)
```

`services/code-judge` (FastAPI, по умолчанию :8105) существует как отдельный
пакет, но в compose не входит ([testing-engine.md](testing-engine.md#сервис-code-judge)).

## Сервисы

| Сервис | Стек | Ответственность | Почему отдельный |
|---|---|---|---|
| `apps/api` | Fastify, TS (NodeNext ESM) | Email OTP auth, custom JWT + revocable DB sessions, account-owned candidate source profile, employer owner gateway, вакансии/приглашения, OpenAPI | Транзакционная логика, целостность |
| `services/matching-engine` | FastAPI, pgvector | Комбайн: strict filter → hybrid scoring → top-k → реранкер; вектора кандидата/вакансии; тиндер-фидбек; объяснимость выдачи | ML-эксперименты на Python, независимые релизы |
| `services/testing-engine` | FastAPI | Банк `blueprints/`; в коде — адаптивный грейд-тест, банк семи семейств, ротация, прокторинг-агрегаты и trusted publisher (entrypoint пока обслуживает только blueprints) | Изолированный CPU-интенсивный домен |
| `services/code-judge` | FastAPI | Хранение отправок кода и упаковка задач DMOJ; runner не установлен, в compose не входит | Недоверенный код кандидата не исполняется в testing-engine |
| `services/integration-engine` | FastAPI | Мок-реестр ФСП (`/fsp/participants/{id}`, `/fsp/verify`, достижения) и демо-привязка (`/fsp/demo/authorize`, `/fsp/demo/token`) | Внешние источники меняются независимо от ядра |
| `services/verification-engine` | FastAPI | Внутренние `/verify/fsp`, `/verify/repository`, `/verify/hh` под сервисным токеном; без candidate id ([verification.md](verification.md)) | Сетевые проверки внешних провайдеров изолированы от ядра |
| `services/agents-service` | FastAPI, PostgreSQL | Общий черновик формы/чата, Laya, durable turns, summary → Create; глобальный агент работодателя (`/employer/organization-agent`, `organization-worker`, [agents.md](agents.md)) | Отдельные знания, инструменты и провайдеры |
| `apps/{candidate,employer,welcome}` | React 19, Vite, Tailwind 4 | Кабинеты по ролям + общая витрина | Один nginx, 3 сборки (`infra/web.Dockerfile`); `apps/testing` в образ не входит |

Публичная стартовая страница `apps/welcome` и её переходы описаны в [welcome-ui.md](welcome-ui.md); кабинеты — в [employer-ui.md](employer-ui.md) и [candidate-ui.md](candidate-ui.md).

Чаты на QGramm интегрированы в кабинеты обеих ролей: маршруты `/{role}/recruitment/{id}/chat/*` проверяют сессию, доступ по приглашению и текущее согласие под DB-locks. Системный чат Comit доставляет уведомления платформы.

## Карта репозитория

| Путь | Содержание |
|---|---|
| `apps/candidate`, `apps/employer`, `apps/testing`, `apps/welcome` | React 19 + TS + Vite + Tailwind 4 (`apps/testing` в web-образ не входит) |
| `apps/api` | api-core (Fastify + OpenAPI) |
| `packages/shared` | домен-типы и контракты (источник истины по типам) |
| `packages/ui-kit` | дизайн-токены и базовые компоненты (гамма ФСП) |
| `packages/api-client` | типизированный HTTP-клиент |
| `services/matching-engine` | Python/FastAPI: подбор и объяснимость |
| `services/testing-engine` | Python/FastAPI: тестирование (blueprints/, адаптивный контур) |
| `services/code-judge` | Python/FastAPI: хранение отправок кода и упаковка задач DMOJ (без runner) |
| `services/integration-engine` | Python/FastAPI: внешние данные и мок ФСП |
| `services/verification-engine` | Python/FastAPI: проверка ФСП, Git-репозитория и резюме hh.ru |
| `services/agents-service` | Python/FastAPI: черновик вакансии (форма + агент), глобальный агент работодателя |
| `infra/` | docker-compose (production по умолчанию, dev, legacy overlays), Dockerfiles, nginx, QGramm bridge, postgres init, `deploy.py` |
| `data/` | синтетика и сиды (готовых датасетов нет — ТЗ) |
| `tests/` | сквозной smoke-гейт и acceptance-сценарии |
| `docs/` | живая документация (RU) |
| `context/` | канон-контекст: ТЗ, исследование hh.ru, видение, механики, план |

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
5. Переотбор: событие `candidate.ready` сравнивает профиль с исходным снимком dislike; при превышении порога (structural distance > 1.0, строгий N > K) кандидат возвращается через `visited_updated`. Автоматическое пополнение хвоста ленты — `feed-refill-worker` ([feed-refill.md](feed-refill.md)).

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
| api-core | 127.0.0.1:28080 → 8080 | `API_PORT` |
| matching-engine | 127.0.0.1:28101 → 8101 | `MATCHING_PORT` |
| testing-engine | 127.0.0.1:8102 → 8102 | `TESTING_PORT` |
| integration-engine | 127.0.0.1:8103 → 8103 | `INTEGRATION_PORT` |
| agents-service | 127.0.0.1:28104 → 8104 | `AGENTS_PORT` |
| verification-engine | 127.0.0.1:28105 → 8105 | `VERIFICATION_PORT` |
| qgramm-chat | только сеть `chat-private` (8080) | — |
| postgres | dev: 127.0.0.1:5433 → 5432; prod: без host-порта | `POSTGRES_PORT` |

Одинаково для `infra/docker-compose.yml` (production по умолчанию),
`infra/docker-compose.prod.yml` (совместимая копия) и dev-файла: наружу
публикуется только web (`WEB_PORT`, по умолчанию 8088); api и engines доступны
лишь на `127.0.0.1` (отладка и smoke), БД в production — только внутренняя сеть.

## Hiring aggregate после подбора

RecruitmentStore в api-core владеет lifecycle и транзакциями обеих ролей;
authDomain инжектирует тот же API Pool/AuthStore/ProfileStore. Нового сервиса
или внешнего SDK не требуется. Matching владеет saved/grant/exclusions, agents
— опубликованной вакансией; API читает их через service-auth и сохраняет
условия предложения. Контакты берутся только из текущего API source profile
после принятия. Durable participant events отделены от email-уведомлений
([email-notifications.md](email-notifications.md)) и системного inbox
([system-inbox.md](system-inbox.md)). [Контур и границы](recruitment.md).

QGramm (`qgramm-chat`) входит в основной compose: образ BASIC + files в приватной
сети `chat-private`. Go-адаптер использует ядро in-process и не публикует raw
QGramm endpoints; api-core проверяет сессию, приглашение и текущее согласие
под DB-locks на каждый вызов. Постоянные сообщения/файлы принадлежат SQLite
QGramm, а решения найма — PostgreSQL API. [Контракт и ключи](chat.md).

Общая витрина использует matching-engine для независимых lexical/Harrier
каналов и фильтров актуального пула. Api-core выводит owner из сессии,
проверяет source consent/revision и закрывает частный PDF. Ручной лайк
транзакционно использует FeedStore и существующий recruitment; отдельный
сервис или ML-запуск для него не создаётся. [Контракт](candidate-showcase.md).

`@comit/shared` собирается в JavaScript `dist/` перед API: production Node не выполняет TypeScript из `node_modules`. Package exports сохраняет source typings; Docker API и coverage gate явно собирают shared перед запуском.
