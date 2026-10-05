# Runbook — типовые операции

## Candidate pipeline: запуск с собранными данными

Это серверный поток, не регистрация пользователя. Нужны PostgreSQL 16 + pgvector,
Python 3.12, зависимости сервиса, private `CANDIDATE_PIPELINE_TOKEN`, exact cached
Harrier revision. Токен не передавать клиентским приложениям и не писать в логи.

```bash
uv venv --python 3.12 services/matching-engine/.venv
uv pip install --python services/matching-engine/.venv/bin/python -e 'services/matching-engine[embeddings,dev]'
pnpm install --frozen-lockfile
pnpm build
```

Заранее, в разрешённом сетевом контуре подготовки, скачать публичную модель:

```bash
services/matching-engine/.venv/bin/python -c 'from huggingface_hub import snapshot_download; snapshot_download("microsoft/harrier-oss-v1-270m", revision="31de22b673913c7d658c0f03f792d77c2dcf8ebd")'
```

Runtime использует `local_files_only=True`, не требует внешнего API и не скачивает
модель. `HF_HOME`/`HF_HUB_CACHE` могут задавать каталог кэша. Шрифты DejaVu для
кириллицы включены в пакет, лицензия рядом; системные шрифты не нужны.

Опциональный **локальный** compose-overlay включает миграции перед серверами и
отдельный worker; базовый `make up` сам по себе worker не запускает:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.candidate-pipeline.yml build
docker compose -f infra/docker-compose.yml -f infra/docker-compose.candidate-pipeline.yml run --rm candidate-worker python -c 'from huggingface_hub import snapshot_download; snapshot_download("microsoft/harrier-oss-v1-270m", revision="31de22b673913c7d658c0f03f792d77c2dcf8ebd")'
docker compose -f infra/docker-compose.yml -f infra/docker-compose.candidate-pipeline.yml up -d
```

В overlay используются текущая БД и persistent `harrier_cache`; volume не удалять.
Build/подготовка модели требуют доступных пакетов/артефактов, обработка работает
офлайн. Prod compose и сервер в этом цикле не изменялись: подключение runtime
на сервере — отдельная явная операция с его секретами и путями.

Без Docker, в отдельных терминалах с уже настроенными `DATABASE_URL`,
`CANDIDATE_PIPELINE_TOKEN`, `MATCHING_ENGINE_URL=http://127.0.0.1:38101`:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine migrate
PORT=38101 PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine serve
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine worker
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine vacancy-worker
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine combine-worker
PORT=38080 pnpm --filter @comit/api start
```

`/healthz` означает живой процесс; matching `/ready` проверяет token, миграции/БД
и наличие pinned модели. Не считать healthz доказательством готового пула.
Статусы queued/processing/retrying/failed видны через candidate status API.
После устранения ошибки failed/retrying можно повторить через POST retry.

## Проверки candidate pipeline

```bash
pnpm --filter @comit/api test
pnpm typecheck
pnpm build
cd services/matching-engine
PYTHONPATH=src .venv/bin/python -m pytest -q tests
.venv/bin/ruff check src tests
```

Integration tests **очищают таблицы**. `PIPELINE_TEST_DATABASE_URL` должен указывать
исключительно на отдельную одноразовую БД, не на локальные пользовательские данные
и не на рабочий пул. Запуск: `PYTHONPATH=src .venv/bin/python -m pytest -q -m integration tests`.
Тестовые doubles в этих проверках проверяют транзакции/ошибки; это не доказательство
инференса модели. Реальная модель проверяется отдельно через acceptance ниже.

```bash
# Из корня репо; только свежая отдельная БД, реальные API и worker уже запущены.
CANDIDATE_PIPELINE_TOKEN="$CANDIDATE_PIPELINE_TOKEN" services/matching-engine/.venv/bin/python tests/candidate_pool_acceptance.py
```

Скрипт допускает только loopback URL (`PIPELINE_ACCEPTANCE_URL`, default :38080),
подаёт четыре синтетических профиля, проверяет настоящий Harrier/PDF/ранг,
идемпотентность, миграцию 37→38, новую partial координату, отзыв согласия и stale409.
Сохраняет отчёт и PDF в git-ignored `tests/artifacts/`.
Нельзя параллельно запускать integration cleanup и acceptance на одной БД.

## Проверки vacancy pipeline

Перед запуском обновлённых серверов применить migration 002; оба worker работают
от одной DATABASE_URL, каждый в отдельном процессе. Локальный overlay включает
candidate-worker и vacancy-worker с общим persistent кэшем. `/ready` проверяет
обе схемы. Бизнес-статус open не заменяет техническую готовность артефактов.

```bash
# Только свежая отдельная БД; API и оба настоящих worker уже запущены.
CANDIDATE_PIPELINE_TOKEN="$CANDIDATE_PIPELINE_TOKEN" services/matching-engine/.venv/bin/python tests/vacancy_pool_acceptance.py
```

Десять синтетических вакансий проверяют class-only/domain-only/stack-only/mixed,
явный ноль, неполные снимки и soft-only. Реальный Harrier проверяется для query
вакансии и document кандидата; затем оба пула мигрируют 37→38. Также проверяются
идемпотентность, отзыв pending, reopen и stale409. Отчёт — ignored
`tests/artifacts/vacancy-pool-acceptance.json`. Candidate acceptance и vacancy
acceptance требуют разных свежих БД, поскольку оба добавляют одну координату.

## Локально

Для основного отбора применить миграции 003/004 до запуска серверов. Отдельный
`combine-worker` доставляет оба outbox и считает основной отбор; модель ему не
нужна, он использует готовые вектора. ACK события означает сохранённое задание,
а готовность карточек проверяется по state=ready конкретного combine run.
`GET /internal/matching/events` показывает backlog, retry и quarantine.
После исправления причины доступны отдельные retry задания и события.
При изменении пула старые карточки возвращают 409 до завершения нового отбора;
не готовая/закрытая вакансия возвращает 404. Обходить эти проверки нельзя.

Полная локальная проверка требует свежую отдельную БД и три настоящих worker:

```bash
CANDIDATE_PIPELINE_TOKEN="$CANDIDATE_PIPELINE_TOKEN" services/matching-engine/.venv/bin/python tests/combine_acceptance.py
```

200 кандидатов/10 вакансий проверяют фильтры, основной отбор, доставку событий,
отзыв согласия, общую миграцию и закрытие. Скрипт меняет только свой локальный
контур через HTTP; отчёт и вымышленные входы сохраняются в `tests/artifacts/`.
Реранкер, пользовательская авторизация и интерфейс тиндера проверкой не охвачены.

| Задача | Команда |
|---|---|
| Поднять стек | `make up` |
| Smoke-гейт | `make smoke` |
| Логи | `make logs` |
| Остановить и снести | `make down` |
| Пересобрать всё | `make build` |
| JS typecheck/build | `pnpm -r typecheck && pnpm -r build` |
| Python-тесты | `pip install -e 'services/matching-engine[dev]' -e 'services/testing-engine[dev]' -e 'services/integration-engine[dev]' && pytest services/*/tests` |
| Dev-сервер одного аппа | `pnpm --filter @comit/web-candidate dev` |
| OpenAPI api-core | http://localhost:8080/docs (или `/api/docs` через nginx) |

## Сервер (mgg-server)

| Задача | Команда |
|---|---|
| Статус раннеров | `systemctl --user status github-runner@mgg-ci-1` |
| Логи раннера | `journalctl --user -u github-runner@mgg-ci-1 -f` |
| Статус стека | `docker ps --filter name=comit-` |
| Логи сервиса | `docker logs -f comit-api-1` |
| Ручной deploy | `gh workflow run deploy.yml --repo mgg789/comit_2` |

## Типовые проблемы

| Симптом | Причина / решение |
|---|---|
| `make smoke` красный по web | nginx отдаёт 404 на `/candidate/` — проверь, что `pnpm -r build` собрал все аппы (Dockerfile собирает 4) |
| Контейнер engines перезапускается | смотри `docker logs`; чаще всего отсутствие `DATABASE_URL`/порта |
| CI-джоба висит в `queued` | раннеры заняты или офлайн: `gh api repos/mgg789/comit_2/actions/runners` |
| Smoke конфликтует с деплоем | один `smoke-host` concurrency на все прогоны; не убирай его |
| 409/странные порты на сервере | занят `WEB_PORT`: смени в `/home/mgg/comit/.env` и redeploy |
