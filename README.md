# Comit — платформа подбора ИТ-специалистов с обратной механикой

Проект хакатона **ЛЦТ 2026, спецтрек ФСП**. Соискатели автоматически распределяются по категориям
на основе объективно подтверждаемого профиля (обязательный опрос + грейд-тест), а работодатель сам
находит нужную категорию и выходит на конкретного кандидата с предложением и уровнем заработной
платы — механика, принципиально отличная от прямого отклика кандидата на вакансию (LHH-модель).

> **Статус: backend серверных контуров MVP.** Профили/вакансии → готовые пулы →
> main top-k → R₀ (sort-only rollout, failed quality gate сохранён) → лента
> работодателя с vacancy-preference learning и bounded-исследованием гипотез.
> Витрина кандидатов, приглашения/собеседования/офферы, приватные чаты QGramm и
> отдельное training-consent реализованы. Тиндер-UI, живой грейд-тест в контуре
> профиля, генератор синтетики 200+ и валидационные цифры — по MVP-плану (S5–S7).

## Быстрый старт

Требования: Docker, Make.

```sh
cp .env.example .env      # при желании поменяйте порты/пароль
make up                   # соберёт и поднимет весь стек
make smoke                # smoke-гейт: все сервисы и маршруты
make logs                 # логи стека
make down                 # остановить и снести volumes
```

| URL | Что |
|---|---|
| http://localhost:8088/ | Витрина/welcome |
| http://localhost:8088/candidate/ | Кабинет кандидата |
| http://localhost:8088/employer/ | Кабинет работодателя |
| http://localhost:8088/testing/ | Сервис тестирования |
| http://localhost:8088/api/docs | OpenAPI (Swagger UI) api-core |
| http://localhost:8101..8103 | matching / testing / integration engines (dev-порты) |

## Архитектура

```
web (nginx: welcome | candidate | employer | testing)
        │ /api/*
        ▼
api-core (Fastify) ──────── PostgreSQL 16 + pgvector
        ▲
services: matching-engine | testing-engine | integration-engine | verification-engine  (Python/FastAPI)
```

- **api-core** — транзакционное ядро: auth, профили, вакансии, приглашения/статусы, рейтинговый ledger, правила видимости ТЗ §2.2.
- **matching-engine** — «комбайн»: строгий фильтр → гибридный скоринг (фичи + эмбеддинг) → top-k → реранкер; реакции ленты обучают vacancy-local preference-модель (отдельный training-consent обязателен).
- **testing-engine** — параметризованные blueprint'ы заданий, устойчивые к утечке; адаптивный грейд-тест.
- **integration-engine** — хостит мок-реестр ФСП и демо-привязку ФСП-профиля (реального API на хакатоне нет).
- **verification-engine** — проверка внешних фактов профиля: ФСП (через мок integration-engine), публичный Git-репозиторий, резюме hh.ru; внутренний порт 8105 без публикации на хост.
- **agents-service** — общий черновик вакансии для формы и чата (Laya/DeepSeek) и глобальный агент работодателя (один чат на аккаунт, фоновые поручения через `organization-worker`), opt-in overlay `infra/docker-compose.agents.yml`.

Подробности: `docs/architecture.md`, `docs/matching.md`, `docs/testing-engine.md`, `docs/integration.md`, `docs/verification.md`.

В цикле 1 реализован серверный поток: подтверждённые/заявленные профили → пул
с двумя векторами и PDF; вакансии с необязательными группами требований → ready;
доставка событий → строгие фильтры → основной top-k с объяснениями.
Город обязателен для обеих сторон. R₀ активирован вручную в sort-only режиме
по упакованному hash-pinned артефакту: quality gate остался failed, модель только
сортирует main top-k, хвост не отсекается. Персонализация — vacancy-local
preference-поправка на непоказанный суффикс ленты; в ленту встроено ограниченное
исследование гипотез (2 research-карточки на 18 обычных). Feed-refill-worker
добирает ленту из reserve того же rerank-run. UI тиндера — следующий этап.
Запуск шести worker и настоящие локальные backend/HTTP проверки —
в [runbook](docs/runbook.md); входные сценарии — `data/seed/matching-scenarios.json`.

## Карта репозитория

| Путь | Содержание |
|---|---|
| `apps/candidate`, `apps/employer`, `apps/testing`, `apps/welcome` | React 19 + TS + Vite + Tailwind 4 |
| `apps/api` | api-core (Fastify + OpenAPI) |
| `packages/shared` | домен-типы и контракты (источник истины по типам) |
| `packages/ui-kit` | дизайн-токены и базовые компоненты (гамма ФСП) |
| `packages/api-client` | типизированный HTTP-клиент |
| `services/matching-engine` | Python/FastAPI: подбор и объяснимость |
| `services/testing-engine` | Python/FastAPI: тестирование (blueprints/) |
| `services/integration-engine` | Python/FastAPI: внешние данные и мок ФСП |
| `services/verification-engine` | Python/FastAPI: проверка ФСП, Git-репозитория и резюме hh.ru |
| `services/agents-service` | Python/FastAPI: черновик вакансии (форма + агент), глобальный агент работодателя |
| `infra/` | docker-compose (dev/prod), Dockerfiles, nginx, postgres init |
| `data/` | синтетика и сиды (готовых датасетов нет — ТЗ) |
| `tests/` | сквозной smoke-гейт и acceptance-сценарии |
| `docs/` | живая документация (RU) |
| `context/` | канон-контекст: ТЗ, исследование hh.ru, видение, механики, план |

## CI/CD

GitHub Actions на self-hosted раннерах `mgg-server` (`mgg-ci-1..3`, лейбл `ci`):
- **CI** — на PR и push в `dev`/`main`: typecheck+build JS-монорепо, pytest matching/testing/integration engines, полный Python-набор с PostgreSQL на раннере `regression`, smoke в изолированном compose-проекте;
- **Deploy** — на push в `main`: сборка и подъём prod-стека на сервере в `/home/mgg/comit` + health-проверки.

Ветки: работа в `dev`, стабильные состояния — PR в `main` (подробности и правила — `AGENTS.md`).

## Документация

Вход для агентов и людей: [`AGENTS.md`](AGENTS.md) (правила работы) → [`context/README.md`](context/README.md) (карта контекста) → `docs/`.

- `docs/architecture.md` — сервисы, потоки, порты
- `docs/api.md` — контракты api-core
- `docs/ci.md`, `docs/deploy.md`, `docs/runbook.md` — процессы
- подсистемы: [`verification.md`](docs/verification.md), [`recruitment.md`](docs/recruitment.md), [`chat.md`](docs/chat.md), [`candidate-showcase.md`](docs/candidate-showcase.md), [`employer-statistics.md`](docs/employer-statistics.md), [`feed-refill.md`](docs/feed-refill.md), [`preference-learning.md`](docs/preference-learning.md), [`api-operations.md`](docs/api-operations.md)
- `context/tz/00-fsp-tz-2026.md` — канон требований (ТЗ), `context/tz/01-hh-research-apr-sep-2026.md` — исследовательская база проблем

## Лицензия

Код открыт (ТЗ §3.5): MIT для собственного кода; сторонние зависимости — MIT/Apache-2.0/BSD.

Тестовые документы хакатона: [политика и отдельные согласия](docs/legal/README.md).
Обучение моделей требует добровольного разрешения; отказ сохраняет обычный подбор.
