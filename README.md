# Comit — джобборд следующего поколения

<p align="center">
  <img src="docs/assets/comit-cover.jpg" alt="Comit — платформа найма с обратной механикой" width="100%">
</p>

<p align="center">
  <a href="https://github.com/mgg789/comit_2/actions/workflows/ci.yml"><img src="https://github.com/mgg789/comit_2/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/mgg789/comit_2/releases/tag/v0.5"><img src="https://img.shields.io/badge/release-v0.5-orange" alt="release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="license"></a>
  <a href="docs/deploy.md"><img src="https://img.shields.io/badge/deploy-Docker-2496ED?logo=docker&logoColor=white" alt="deploy"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python"></a>
  <a href="docs/data.md"><img src="https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL"></a>
</p>

Веб-платформа найма ИТ-специалистов с **обратной механикой** (LHH-модель): не кандидат
рассылает резюме в никуда, а работодатель просматривает объективно категоризированные пулы
специалистов и сам выходит на конкретного человека — с приглашением и уровнем заработной платы.

Проект хакатона **ЛЦТ 2026, спецтрек ФСП**. Механика категоризации строится на
опросе и проверке навыков. Каждый отбор объясним: работодатель видит,
почему кандидат попал в подборку.

## Где потестить

Продакшн-контур: **https://comit.droidje.com**

| URL | Что |
|---|---|
| [comit.droidje.com](https://comit.droidje.com/) | Витрина и вход |
| [comit.droidje.com/candidate/](https://comit.droidje.com/candidate/) | Кабинет кандидата: профиль, опрос, тест, приглашения, чаты |
| [comit.droidje.com/employer/](https://comit.droidje.com/employer/) | Кабинет работодателя: вакансии, подборка с объяснением, лента, найм |
| [comit.droidje.com/api/docs](https://comit.droidje.com/api/docs) | OpenAPI (Swagger UI) |
| [comit.droidje.com/support/](https://comit.droidje.com/support/) | Консоль службы поддержки (вход оператора) |
| [comit.droidje.com/help/](https://comit.droidje.com/help/) | Справка по платформе |

Testing UI доступен по `/testing/`: подготовка устройств, адаптивный тест и результат. Попытки доступны только авторизованному кандидату через API; backend тестирования остаётся внутренним сервисом. API и остальные service ports доступны через loopback; [deploy.md](docs/deploy.md) описывает HTTPS домен и CD, [ci.md](docs/ci.md) — реальные тесты, измерения покрытия и пороги.

Вход — по email и одноразовому коду из письма. В локальном dev-контуре код подставляется
на форму автоматически, тестировать можно без SMTP. У оператора поддержки отдельный
вход по email и access key ([support-console.md](docs/support-console.md)).

## Презентация и дизайн

- **Презентация**: [PDF в репозитории](demo/comit-presentation.pdf) · [Демо-видео, часть 1](demo/comit-demo-part-01.mp4) · [часть 2](demo/comit-demo-part-02.mp4) · [Google Slides](https://docs.google.com/presentation/d/1csFfRs20WJFlEqlPujfLhpAnB1E3YNWOK_Y3tGc3UOM/edit?usp=sharing)
- **Дизайн**: [Figma — LCT core \| ЛЦТ 2026 ФСП](https://www.figma.com/design/gPFLCJKXEoRwRP3YLyMsX4/LCT-core-%7C-%D0%9B%D0%A6%D0%A2-2026-%D0%A4%D0%A1%D0%9F?node-id=0-1&t=LPk0C0vv17PMQCYn-1)
- **Схемы и архитектура (FigJam)**: [LCT core \| Схемы проекта](https://www.figma.com/board/HSYre3JOQlCaPQxQp9gj6R/LCT-core-%7C-%D0%A1%D1%85%D0%B5%D0%BC%D1%8B-%D0%BF%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D0%B0?node-id=0-1&t=gKnYZlsk3evyWJNZ-1)
- **PDF документация и демо-видео**: [PDF в репозитории](docs/COMIT-docs.pdf) · [Видео, часть 1](demo/comit-demo-part-01.mp4) · [Видео, часть 2](demo/comit-demo-part-02.mp4) · [Google Drive](https://drive.google.com/drive/folders/1w5vVd7tjaDT8-EnzcIRqwQ3oF1BklijG?usp=sharing)

## Что умеем

**Обратная механика найма (ядро конкурса)**
- Работодатель описывает потребность → получает подборку с объяснением «почему эти кандидаты» → смотрит свайп-ленту → приглашает с зарплатой «от–до». Кандидат не откликается — работа настигает его сама.
- Без кнопки «откликнуться» — сознательно: массовые отклики вернут сервис к проблемам hh — спам заявкам и отсечению неадаптивными фильтрами. Вместо отклика кандидат управляет заметностью и конкурентной силой профиля: ИИ-рекомендации «что улучшить», ФСП-буст и недельные задания ([inverse-concept.md](docs/inverse-concept.md)).
- Кандидат проходит опрос и предметные тесты по банку заданий с ограничением частоты. Результат подтверждает конкретный навык и его измеренный уровень; общий заявленный грейд профиля автоматически не переписывается ([testing-engine.md](docs/testing-engine.md)).
- Профиль подтверждается внешними источниками: история ФСП, публичный Git-репозиторий, резюме hh.ru — с рейтингом подтверждённых полей и очками.

**Объяснимый подбор, который учится**
- Конвейер: строгий фильтр → объяснимый гибридный скоринг (фичи + эмбеддинги) → top-k → реранкер. Каждая карточка несёт структурированные факторы попадания — никаких «поверьте, он подходит».
- Лента работодателя обучается на его реальных свайпах; сходимость примерно за 20 реакций измерена на синтетических работодателях. В ленту встроено ограниченное исследование гипотез.
- Механика проверена числами, а не обещаниями: сквозная приёмка комбайна на 200 кандидатах и 10 вакансиях, предзарегистрированный бенчмарк реранкера (ΔNDCG +0.0107 к основному скорингу), слепая человеческая разметка 17/20 пар (на уверенных оценках 17/18), golden-регрессия на любое изменение весов.

**Честный продукт**
- Зарплата «от–до» обязательна и в вакансии, и в приглашении — кандидат видит условия до начала общения. Контакты раскрываются только после принятия приглашения; статусы видны обеим сторонам.
- Чаты найма на [QGramm](https://github.com/mgg789/QGramm): серверное шифрование сообщений и вложений.
- Системный чат Comit в обоих кабинетах: уведомления платформы и живая служба поддержки.
- Агент работодателя (DeepSeek/Jev): черновик вакансии из свободного описания, глобальный чат-ассистент с фоновыми поручениями и учётом бюджета.
- Статистика найма у работодателя, публичная витрина вакансий и согласившихся соискателей с обязательными зарплатными вилками, email-уведомления о событиях.
- 152-ФЗ: раздельные согласия на обработку, публикацию в пуле и обучение моделей; отказ от обучения не ломает обычный подбор. Локализация данных в РФ и исчерпывающая карта трансграничных передач — [152-fz-compliance.md](docs/152-fz-compliance.md).
- Внешние вызовы кэшируются; недоступность провайдера явно отражается в интерфейсе. Проверка кода требует доступного изолированного judge, развёрнутого ответа — Jev/DeepSeek; сбой сохраняет ответ для повтора и не превращается в неверный результат.

## Особенно интересно

Шесть фич, которых не было в ТЗ, — то, что превращает прототип в продукт:

1. **Лента работодателя, которая учится.** Свайпы обучают vacancy-local preference-модель: сходимость за ~20 реакций на синтетических работодателях, исследовательские карточки гипотез, добор из резерва того же run — подбор становится точнее от каждого действия ([preference-convergence](docs/preference-convergence.md), [feed-refill](docs/feed-refill.md)).
2. **AI-агент работодателя** (Jev/DeepSeek): черновик вакансии из свободного описания и глобальный чат-ассистент с фоновыми поручениями и явным бюджетом токенов ([agents.md](docs/agents.md)).
3. **Честное тестирование под наблюдением.** Код кандидата исполняется только в одноразовых Docker-песочницах на отдельной VM (fail-closed без judge), а прокторинг считает агрегаты детекции лица локально в браузере — кадры не покидают устройство ([инструкция проверяющего](AGENTS.md), [testing-design](docs/testing-design.md)).
4. **Верификация фактов из внешних источников:** история ФСП (мок-реестр), публичный Git-репозиторий (clone --bare, без запуска кода) и резюме hh.ru — с ledger очков подтверждённых полей ([verification.md](docs/verification.md)).
5. **Чаты найма с серверным шифрованием** на [QGramm](https://github.com/mgg789/QGramm) в изолированной сети плюс системный чат Comit с живой службой поддержки в обоих кабинетах ([chat.md](docs/chat.md), [support-console.md](docs/support-console.md)).
6. **Экономика найма под учётом:** воронка delivered → … → hired со сравнением периодов и durable журнал расходов — публикации, приглашения, неустойки за просрочки, бюджет ИИ ([employer-statistics.md](docs/employer-statistics.md)).

## Как запустить локально

Требования: Docker Compose 2.24.4+ и Make. Первый запуск требует интернета
для сборки образов и скачивания публичного pinned Harrier; затем matching работает
по локальному cache. Необязательный внешний judge не нужен для запуска сайта.

```sh
make dev-up  # отдельные volumes comit-dev, bootstrap Harrier и ожидание readiness
```

- Приложение: http://localhost:8088 — витрина, `/candidate/`, `/employer/`, `/testing/`, `/support/`, API `/api/`. На хост публикуется один loopback-порт.
- Одноразовый код входа подставляется автоматически (dev-only), мок ФСП включён.
- Тестирование полностью выключено без judge: попытки и оценки не создаются.
- Для реального judge: `COMIT_LOCAL_PROJECT=comit-dev ./infra/local-dev.sh --env-file /absolute/private/dev.env --with-judge` ([настройка](docs/deploy.md#локальный-запуск-через-один-порт)).
- `make dev-models` отдельно готовит точную модель; `make dev-down` останавливает стек, сохраняя volumes.
- Ошибка скачивания модели останавливает bootstrap, без fake embeddings или ослабления readiness.

Production-контур, отличия dev/prod и запуск без Docker — [docs/environments.md](docs/environments.md).

## Сервисы

- **api-core** — транзакционное ядро: auth, профили, вакансии, приглашения/статусы, рейтинговый ledger, правила видимости ТЗ §2.2.
- **matching-engine** — «комбайн»: строгий фильтр → гибридный скоринг (фичи + эмбеддинг) → top-k → реранкер; реакции ленты обучают vacancy-local preference-модель (отдельный training-consent обязателен).
- **testing-engine** — адаптивный грейд-тест с банком семи семейств, ротацией, прокторинг-агрегатами и trusted publisher в api-core. Код проверяет отдельный `services/code-judge` на изолированной VM; развёрнутые ответы — Jev/DeepSeek ([проверки](docs/package-validation.md)).
- **integration-engine** — хостит учебный реестр ФСП и демо-привязку ФСП-профиля; на хакатоне он выступает источником данных ФСП вместо внешнего API.
- **code-judge** — изолированная проверка кода на отдельной VM: каждый testcase в собственном Docker-контейнере (128 MiB, 0.5 CPU, 16 pids, UID 65534); testing-engine обращается к нему по `CODE_JUDGE_URL` ([services.md](docs/services.md)).
- **verification-engine** — проверка внешних фактов профиля: ФСП (через мок integration-engine), публичный Git-репозиторий, резюме hh.ru; порт 8105, на хосте только loopback `VERIFICATION_PORT` (28105).
- **agents-service** — общий черновик вакансии для формы и чата (Jev/DeepSeek) и глобальный агент работодателя (один чат на аккаунт, фоновые поручения через `organization-worker`); входит в основной compose вместе с `agents-worker` и `organization-agent-worker`.

Подробности: [обзор сервисов](docs/services.md), [диаграммы](docs/architecture-diagrams.md), `docs/architecture.md`, `docs/matching.md`, `docs/testing-engine.md`, `docs/integration.md`, `docs/verification.md`.

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
| `apps/candidate`, `apps/employer`, `apps/testing`, `apps/welcome`, `apps/support` | React 19 + TS + Vite + Tailwind 4; пять приложений в одном web-образе |
| `apps/api` | api-core (Fastify + OpenAPI) |
| `packages/shared` | домен-типы и контракты (источник истины по типам) |
| `packages/ui-kit` | дизайн-токены и базовые компоненты (гамма ФСП) |
| `packages/api-client` | типизированный HTTP-клиент |
| `services/matching-engine` | Python/FastAPI: подбор и объяснимость |
| `services/testing-engine` | Python/FastAPI: тестирование (blueprints/, адаптивный контур) |
| `services/code-judge` | Python/FastAPI: authenticated broker, ресурсно ограниченные Docker задания на отдельной VM |
| `services/integration-engine` | Python/FastAPI: внешние данные и мок ФСП |
| `services/verification-engine` | Python/FastAPI: проверка ФСП, Git-репозитория и резюме hh.ru |
| `services/agents-service` | Python/FastAPI: черновик вакансии (форма + агент), глобальный агент работодателя |
| `infra/` | docker-compose (production по умолчанию, dev, legacy overlays), Dockerfiles, nginx, QGramm bridge, postgres init, `deploy.py` |
| `data/` | синтетика и сиды: по ТЗ данные для подбора генерируются самостоятельно |
| `tests/` | сквозной smoke-гейт и acceptance-сценарии |
| `docs/` | живая документация (RU) |
| `context/` | канон-контекст: ТЗ, исследование hh.ru, видение, механики, план |

## CI/CD

GitHub Actions на self-hosted раннерах `mgg-server` (`mgg-ci-1..3`, лейбл `ci`):
- **CI** — на PR и push в `dev`/`main`: typecheck+build JS-монорепо и frontend-тесты, pytest с unit coverage для matching/testing/integration/agents/verification/code-judge, API-тесты с coverage на disposable PostgreSQL, полный Python-набор с PostgreSQL на раннере `regression`, smoke в изолированном compose-проекте и отдельная реальная проверка code-judge на одноразовом GitHub-hosted runner;
- **Deploy** — после успешного CI на push в `main` (или вручную): только текущий SHA `main`, immutable release в `/home/mgg/comit/releases/<SHA>`, backup БД, smoke и откат образов при ошибке; затем публичный smoke с GitHub-hosted runner ([deploy.md](docs/deploy.md)).

Ветки: работа в `dev`, стабильные состояния — PR в `main` (правила разработчиков — `docs/judge-agents.md`).

## GitHub Issues

Нашли проблему, несоответствие в документации или есть предложение — заведите issue
в [трекере](https://github.com/mgg789/comit_2/issues): опишите шаги воспроизведения,
ожидаемое и фактическое поведение, затронутую часть системы.

Исключение — безопасность: уязвимости (включая zero-day), утечки и чувствительные
данные в публичные issues не выносятся. Напишите в телеграм техподдержки сервисов
droidje — [@droidje](https://t.me/droidje) — или напрямую CTO проекта ([Михаил](https://t.me/mschail));
процедура и ожидания описаны в [SECURITY.md](SECURITY.md).

## Документация

Живая документация подсистем — в [`docs/`](docs/), канон требований и продуктовый контекст —
в [`context/README.md`](context/README.md). 
Единая PDF-книга документации: [PDF в репозитории](docs/COMIT-docs.pdf) · [Google Drive](https://drive.google.com/drive/folders/1w5vVd7tjaDT8-EnzcIRqwQ3oF1BklijG?usp=sharing)

С чего начать:

| Файл | О чём |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | Быстрый старт: от клонирования до первого сценария и проверок перед PR |
| [docs/services.md](docs/services.md) | Карточки сервисов: назначение, порты, маршруты, переменные, данные |
| [docs/architecture-diagrams.md](docs/architecture-diagrams.md) | Диаграммы Mermaid: контейнеры, старт, подбор, тест, CI/CD |
| [docs/user-flows.md](docs/user-flows.md) | Сценарии кандидата, работодателя, AI-агента и оператора с маршрутами API |
| [docs/api-reference.md](docs/api-reference.md) | Полный генерируемый перечень операций api-core с типом доступа |
| [docs/glossary.md](docs/glossary.md), [docs/faq.md](docs/faq.md) | Термины и частые вопросы |
| [docs/environments.md](docs/environments.md) | Поднять dev или production, чем они отличаются |
| [docs/architecture.md](docs/architecture.md) | Сервисы, потоки, карта репозитория |
| [docs/matching.md](docs/matching.md) | Механика подбора, веса, объяснимость, валидация |
| [docs/testing-engine.md](docs/testing-engine.md) | Тестирование: blueprint'ы, грейды, состояние контура |
| [docs/recruitment.md](docs/recruitment.md), [docs/chat.md](docs/chat.md) | Приглашения, правила видимости, чаты найма |
| [docs/system-inbox.md](docs/system-inbox.md), [docs/support-console.md](docs/support-console.md) | Системный чат Comit и служба поддержки |
| [docs/api.md](docs/api.md), [docs/data.md](docs/data.md) | Контракты API и данные |
| [docs/anti-fraud-and-ats.md](docs/anti-fraud-and-ats.md) | Концепция: защита от фиктивных вакансий (плата за публикацию, карма работодателя, AI-guard) и интеграция с ATS |
| [docs/deploy.md](docs/deploy.md), [docs/ci.md](docs/ci.md), [docs/runbook.md](docs/runbook.md) | Деплой, CI/CD, эксплуатация |

### Указатель документации

- **Старт и обзор:** [quickstart](docs/quickstart.md), [environments](docs/environments.md), [services](docs/services.md), [architecture](docs/architecture.md), [architecture-diagrams](docs/architecture-diagrams.md), [user-flows](docs/user-flows.md), [glossary](docs/glossary.md), [faq](docs/faq.md), [team](docs/team.md), [packages](docs/packages.md).
- **Требования хакатона:** [core](docs/core.md), [additional](docs/additional.md), [anti-fraud-and-ats](docs/anti-fraud-and-ats.md), [152-fz-compliance](docs/152-fz-compliance.md), [inverse-concept](docs/inverse-concept.md), [инструкция проверяющего](AGENTS.md), [package-validation](docs/package-validation.md), [demo-data](docs/demo-data.md).
- **API и данные:** [api](docs/api.md), [api-reference](docs/api-reference.md), [api-operations](docs/api-operations.md), [cursor-pagination](docs/cursor-pagination.md), [data](docs/data.md), [auth](docs/auth.md).
- **Кандидат:** [candidate-ui](docs/candidate-ui.md), [candidate-profile](docs/candidate-profile.md), [resume-import](docs/resume-import.md), [verification](docs/verification.md), [integration](docs/integration.md), [fsp-link](docs/fsp-link.md), [weekly-challenges](docs/weekly-challenges.md), [recommendations](docs/recommendations.md), [candidate-backend-audit](docs/candidate-backend-audit.md).
- **Тестирование:** [testing-engine](docs/testing-engine.md), [testing-design](docs/testing-design.md).
- **Работодатель и агенты:** [employer-ui](docs/employer-ui.md), [vacancy-crud](docs/vacancy-crud.md), [vacancy-suggestions](docs/vacancy-suggestions.md), [employer-statistics](docs/employer-statistics.md), [economics](docs/economics.md), [agents](docs/agents.md), [agents-offline](docs/agents-offline.md), [organization-agent-acceptance](docs/organization-agent-acceptance.md).
- **Найм и коммуникации:** [recruitment](docs/recruitment.md), [hiring-live-acceptance](docs/hiring-live-acceptance.md), [chat](docs/chat.md), [chat-events](docs/chat-events.md), [system-inbox](docs/system-inbox.md), [support-console](docs/support-console.md), [email-notifications](docs/email-notifications.md).
- **Витрины и интерфейс:** [welcome-ui](docs/welcome-ui.md), [public-showcase](docs/public-showcase.md), [candidate-showcase](docs/candidate-showcase.md), [candidate-showcase-verification](docs/candidate-showcase-verification.md), [search-index](docs/search-index.md), [ui-kit](docs/ui-kit.md).
- **Подбор и лента:** [matching](docs/matching.md), [matching-migrations](docs/matching-migrations.md), [feed-refill](docs/feed-refill.md), [feed-change-policy](docs/feed-change-policy.md), [preference-learning](docs/preference-learning.md), [preference-convergence](docs/preference-convergence.md), [hypothesis-learning](docs/hypothesis-learning.md), [hypothesis-validation](docs/hypothesis-validation.md), [real-card-learning](docs/real-card-learning.md).
- **Реранкер R₀ (исследовательский журнал):** [rerank-mvp-status](docs/rerank-mvp-status.md), [rerank-activation](docs/rerank-activation.md), [rerank-hard-benchmark](docs/rerank-hard-benchmark.md), [rerank-grounded-cohort](docs/rerank-grounded-cohort.md), [rerank-grounded-cohort-acceptance](docs/rerank-grounded-cohort-acceptance.md), [rerank-cohort-training](docs/rerank-cohort-training.md), [rerank-cohort-review](docs/rerank-cohort-review.md), [rerank-cohort-pair-repair](docs/rerank-cohort-pair-repair.md), [rerank-cohort-pair-completion](docs/rerank-cohort-pair-completion.md), [rerank-class-only-oracle](docs/rerank-class-only-oracle.md), [rerank-current-sample-probe](docs/rerank-current-sample-probe.md), [rerank-grounded-teacher-pilot](docs/rerank-grounded-teacher-pilot.md), [rerank-gemma-teacher-pilot](docs/rerank-gemma-teacher-pilot.md), [rerank-teacher-comparison](docs/rerank-teacher-comparison.md), [rerank-transport-recovery](docs/rerank-transport-recovery.md), [rerank-frontend05-diagnosis](docs/rerank-frontend05-diagnosis.md).
- **Эксплуатация:** [deploy](docs/deploy.md), [ci](docs/ci.md), [runbook](docs/runbook.md).
- **Правовые тексты (тестовые):** [legal/README](docs/legal/README.md).

Вход для агентов — [`AGENTS.md`](AGENTS.md).

## Лицензия

Собственный код — [Apache-2.0](LICENSE); лицензии сторонних компонентов перечислены
в [docs/packages.md](docs/packages.md).

Тестовые документы хакатона: [политика и отдельные согласия](docs/legal/README.md).
Обучение моделей требует добровольного разрешения; отказ сохраняет обычный подбор.

## Команда и лицензия

[Ответственность и CODEOWNERS](docs/team.md), [Apache-2.0](LICENSE),
[компоненты и версии](docs/packages.md), [правила безопасности](SECURITY.md).

Консоль `/support/` требует оператора: задайте `SUPPORT_EMAIL` и приватный `SUPPORT_ACCESS_KEY` в отдельном dev env-файле и запустите `COMIT_LOCAL_PROJECT=comit-dev ./infra/local-dev.sh --env-file /путь/dev.env`. Production `.env` для локального dev не используется.
