# Comit — платформа подбора ИТ-специалистов с обратной механикой

Проект хакатона **ЛЦТ 2026, спецтрек ФСП**. Соискатели автоматически распределяются по категориям
на основе объективно подтверждаемого профиля (обязательный опрос + грейд-тест), а работодатель сам
находит нужную категорию и выходит на конкретного кандидата с предложением и уровнем заработной
платы — механика, принципиально отличная от прямого отклика кандидата на вакансию (LHH-модель).

> **Статус: backend первого цикла.** Профили/вакансии → готовые пулы → main top-k
> → durable R₀ с явным fallback. Обучение на teacher остановлено после проваленного
> пилота; активного обученного артефакта нет. Интерфейсы продолжают работу по MVP-плану.

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
services: matching-engine | testing-engine | integration-engine  (Python/FastAPI)
```

- **api-core** — транзакционное ядро: auth, профили, вакансии, приглашения/статусы, рейтинговый ledger, правила видимости ТЗ §2.2.
- **matching-engine** — «комбайн»: строгий фильтр → гибридный скоринг (фичи + эмбеддинг) → top-k → реранкер; тиндер-фидбек обучает вектор вакансии.
- **testing-engine** — параметризованные blueprint'ы заданий, устойчивые к утечке; адаптивный грейд-тест.
- **integration-engine** — верификация данных из внешних источников; хостит мок-реестр ФСП (реального API на хакатоне нет).

Подробности: `docs/architecture.md`, `docs/matching.md`, `docs/testing-engine.md`, `docs/integration.md`.

В цикле 1 реализован серверный поток: подтверждённые/заявленные профили → пул
с двумя векторами и PDF; вакансии с необязательными группами требований → ready;
доставка событий → строгие фильтры → основной top-k с объяснениями.
Город обязателен для обеих сторон. R₀ получил typed facts, общий builder, offline
pairwise learner и gates качества/reviews. Пилот Laya дал0% swap consistency,
поэтому выдача сохраняет main порядок; UI тиндера и персонализация — далее.
Запуск четырёх worker и настоящие локальные backend/HTTP проверки —
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
| `infra/` | docker-compose (dev/prod), Dockerfiles, nginx, postgres init |
| `data/` | синтетика и сиды (готовых датасетов нет — ТЗ) |
| `tests/` | сквозной smoke-гейт |
| `docs/` | живая документация (RU) |
| `context/` | канон-контекст: ТЗ, исследование hh.ru, видение, механики, план |

## CI/CD

GitHub Actions на self-hosted раннерах `mgg-server` (`mgg-ci-1..3`, лейбл `ci`):
- **CI** — на PR и push в `dev`/`main`: typecheck+build JS-монорепо, pytest Python-сервисов, smoke в изолированном compose-проекте;
- **Deploy** — на push в `main`: сборка и подъём prod-стека на сервере в `/home/mgg/comit` + health-проверки.

Ветки: работа в `dev`, стабильные состояния — PR в `main` (подробности и правила — `AGENTS.md`).

## Документация

Вход для агентов и людей: [`AGENTS.md`](AGENTS.md) (правила работы) → [`context/README.md`](context/README.md) (карта контекста) → `docs/`.

- `docs/architecture.md` — сервисы, потоки, порты
- `docs/api.md` — контракты api-core
- `docs/ci.md`, `docs/deploy.md`, `docs/runbook.md` — процессы
- `context/tz/00-fsp-tz-2026.md` — канон требований (ТЗ), `context/tz/01-hh-research-apr-sep-2026.md` — исследовательская база проблем

## Лицензия

Код открыт (ТЗ §3.5): MIT для собственного кода; сторонние зависимости — MIT/Apache-2.0/BSD.
