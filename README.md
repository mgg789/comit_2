# Comit — платформа подбора ИТ-специалистов с обратной механикой

Проект хакатона **ЛЦТ 2026, спецтрек ФСП**. Соискатели автоматически распределяются по категориям
на основе объективно подтверждаемого профиля (обязательный опрос + грейд-тест), а работодатель сам
находит нужную категорию и выходит на конкретного кандидата с предложением и уровнем заработной
платы — механика, принципиально отличная от прямого отклика кандидата на вакансию (LHH-модель).

> **Статус: backend серверных контуров MVP; реранкер R₀ — операторский статус
> mvp-prod-ready.** Строгий фильтр → объяснимый скоринг → top-k → R₀ (sort-only,
> артефакт `c18f4bc1`; учительская разметка совпала со слепой человеческой
> проверкой в 17/20 пар, на уверенных оценках 17/18) → vacancy-preference
> learning (на синтетических работодателях бар достигается за ≤21 реакцию,
> барьер первого fit — 30) → bounded-исследование гипотез H1–H10 (механика
> работает, но вердиктов при текущих объёмах ждать не приходится). Кабинеты
> кандидата и работодателя (включая свайп-ленту), общая витрина,
> приглашения/собеседования/офферы со сроками, чаты QGramm, системный inbox,
> email-уведомления, training-consent и демо-привязка ФСП реализованы; ФСП-буст
> в скоринг не подключён. Адаптивный грейд-тест реализован в коде testing-engine
> (целевой [дизайн](docs/testing-design.md)), но в развёрнутом сервисе и UI пока
> не запущен ([состояние](docs/testing-engine.md)); генератор синтетики 200+ и
> Precision/MRR — по MVP-плану (S5–S7).

## Быстрый старт

Требования: Docker Compose v2, Make; для production — HTTPS gateway, приватные SMTP/provider настройки и реальный Harrier cache. Подготовка описана в [deploy.md](docs/deploy.md).

```sh
cp .env.example .env      # заполните приватные production значения
make up                   # production по умолчанию
make smoke                # health/readiness и публичные маршруты
make logs
make down                 # остановить стек, сохранить volumes
```

Для локальной разработки профиль включается явно:

```sh
make dev-up               # отдельный compose/profile и volumes comit-dev
SMOKE_MODE=dev make smoke
make dev-down             # сохранить данные dev volumes
```

В dev одноразовый код автоматически заполняется при каждом входе. В production код доставляется через SMTP и не возвращается API. Mock ФСП доступен только в dev. Конфигурации запускаются отдельно; при одновременном запуске задайте разные порты.

| URL | Что |
|---|---|
| http://localhost:8088/ | Общая начальная страница и витрина |
| http://localhost:8088/candidate/ | Кабинет кандидата |
| http://localhost:8088/employer/ | Кабинет работодателя |
| http://localhost:8088/api/docs | OpenAPI (Swagger UI) api-core; JSON — `/api/docs/json` |

Testing UI в общем приложении не публикуется (`/testing/` возвращает 404). Backend тестирования остаётся отдельным внутренним сервисом. API и остальные service ports доступны через loopback; [deploy.md](docs/deploy.md) описывает HTTPS домен и CD, [ci.md](docs/ci.md) — реальные тесты, измерения покрытия и пороги.

## Архитектура

```
web (nginx: welcome | candidate | employer)
        │ /api/*
        ▼
api-core (Fastify) ──────── PostgreSQL 16 + pgvector
        ▲
services: matching-engine | testing-engine | integration-engine | verification-engine | agents-service  (Python/FastAPI)
          qgramm-chat (Go, приватная сеть) + фоновые workers
```

- **api-core** — транзакционное ядро: auth, профили, вакансии, приглашения/статусы, рейтинговый ledger, правила видимости ТЗ §2.2.
- **matching-engine** — «комбайн»: строгий фильтр → гибридный скоринг (фичи + эмбеддинг) → top-k → реранкер; реакции ленты обучают vacancy-local preference-модель (отдельный training-consent обязателен).
- **testing-engine** — blueprint-реестр заданий (развёрнут); в коде также адаптивный грейд-тест с банком семи семейств, ротацией, прокторинг-агрегатами и публикацией подтверждённого грейда в api-core (entrypoint пока его не запускает). Код кандидата проверяет только отдельный `services/code-judge` (runner ещё не установлен, в compose не входит).
- **integration-engine** — хостит мок-реестр ФСП и демо-привязку ФСП-профиля (реального API на хакатоне нет).
- **verification-engine** — проверка внешних фактов профиля: ФСП (через мок integration-engine), публичный Git-репозиторий, резюме hh.ru; порт 8105, на хосте только loopback `VERIFICATION_PORT` (28105).
- **agents-service** — общий черновик вакансии для формы и чата (Laya/DeepSeek) и глобальный агент работодателя (один чат на аккаунт, фоновые поручения через `organization-worker`); входит в основной compose вместе с `agents-worker` и `organization-agent-worker`.

Подробности: `docs/architecture.md`, `docs/matching.md`, `docs/testing-engine.md`, `docs/integration.md`, `docs/verification.md`.

В цикле 1 реализован серверный поток: подтверждённые/заявленные профили → пул
с двумя векторами и PDF; вакансии с необязательными группами требований → ready;
доставка событий → строгие фильтры → основной top-k с объяснениями.
Город обязателен для обеих сторон. Комбайн проверен сквозной приёмкой (05.10.2026) на
200 кандидатов и 10 вакансиях (все ветки фильтров, 100 % доставка событий,
нулевые quarantined) и защищён golden-регрессией: любое изменение весов или
порядка ловится тестом. Реранкер R₀ прошёл предзарегистрированный сложный бенчмарк
(ΔNDCG +0.0107 к main) и две слепые проверки разметки — ассистентскую 20/20 и
человеческую 17/20 — [детали](docs/rerank-mvp-status.md).
Персонализация на синтетических работодателях сходится за ~20 реакций (барьер первого включения понижен
до 30); в ленту встроено ограниченное исследование гипотез (2 research-карточки
на 18 обычных); feed-refill-worker добирает ленту из reserve того же
rerank-run. Запуск шести matching worker и настоящие локальные backend/HTTP проверки —
в [runbook](docs/runbook.md); входные сценарии — `data/seed/matching-scenarios.json`.

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

## CI/CD

GitHub Actions на self-hosted раннерах `mgg-server` (`mgg-ci-1..3`, лейбл `ci`):
- **CI** — на PR и push в `dev`/`main`: typecheck+build JS-монорепо и frontend-тесты, pytest с unit coverage для matching/testing/integration/agents/verification, API-тесты с coverage на disposable PostgreSQL, полный Python-набор с PostgreSQL на раннере `regression`, smoke в изолированном compose-проекте;
- **Deploy** — после успешного CI на push в `main` (или вручную): только текущий SHA `main`, immutable release в `/home/mgg/comit/releases/<SHA>`, backup БД, smoke и откат образов при ошибке ([deploy.md](docs/deploy.md)).

Ветки: работа в `dev`, стабильные состояния — PR в `main` (подробности и правила — `AGENTS.md`).

## GitHub Issues

При начале работы с репозиторием и на смысловых контрольных точках проверяйте открытые
GitHub Issues. Подтверждённые и актуальные проблемы исправляйте в рамках разрешённой
области репозитория, даже если пользователь отдельно не назвал issue. Сверяйте описание
issue с текущим кодом: уже исправленные и неподтверждённые пункты не меняйте вслепую;
за пределами разрешённой области зафиксируйте проблему и сообщите о ней ответственному.

## Документация

Вход для агентов и людей: [`AGENTS.md`](AGENTS.md) (правила работы) → [`context/README.md`](context/README.md) (карта контекста) → `docs/`.

- `docs/architecture.md` — сервисы, потоки, порты
- `docs/api.md` — контракты api-core
- `docs/ci.md`, `docs/deploy.md`, `docs/runbook.md` — процессы
- подсистемы: [`auth.md`](docs/auth.md), [`agents.md`](docs/agents.md), [`verification.md`](docs/verification.md), [`recruitment.md`](docs/recruitment.md), [`chat.md`](docs/chat.md), [`chat-events.md`](docs/chat-events.md), [`system-inbox.md`](docs/system-inbox.md), [`email-notifications.md`](docs/email-notifications.md), [`recommendations.md`](docs/recommendations.md), [`candidate-showcase.md`](docs/candidate-showcase.md), [`public-showcase.md`](docs/public-showcase.md), [`employer-statistics.md`](docs/employer-statistics.md), [`feed-refill.md`](docs/feed-refill.md), [`preference-learning.md`](docs/preference-learning.md), [`api-operations.md`](docs/api-operations.md)
- интерфейсы: [`welcome-ui.md`](docs/welcome-ui.md), [`candidate-ui.md`](docs/candidate-ui.md), [`employer-ui.md`](docs/employer-ui.md), [`ui-kit.md`](docs/ui-kit.md)
- валидация ML: [`rerank-mvp-status.md`](docs/rerank-mvp-status.md), [`rerank-hard-benchmark.md`](docs/rerank-hard-benchmark.md), [`preference-convergence.md`](docs/preference-convergence.md), [`hypothesis-validation.md`](docs/hypothesis-validation.md); ФСП — [`integration.md`](docs/integration.md); тестирование — [`testing-engine.md`](docs/testing-engine.md), [`testing-design.md`](docs/testing-design.md)
- `context/tz/00-fsp-tz-2026.md` — канон требований (ТЗ), `context/tz/01-hh-research-apr-sep-2026.md` — исследовательская база проблем

## Лицензия

Код открыт (ТЗ §3.5): MIT для собственного кода; сторонние зависимости — MIT/Apache-2.0/BSD.

Тестовые документы хакатона: [политика и отдельные согласия](docs/legal/README.md).
Обучение моделей требует добровольного разрешения; отказ сохраняет обычный подбор.
