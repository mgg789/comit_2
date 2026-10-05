# Runbook — типовые операции

## Auth API-core: явная миграция и mail worker

Auth не использует пароль и не создаёт аккаунт до успешного OTP. Один
нормализованный email может иметь только одну роль. Сначала применить миграцию в
отдельном шаге, затем запускать API и worker:

```bash
DATABASE_URL='postgres://...' pnpm --filter @comit/api auth:migrate
DATABASE_URL='postgres://...' AUTH_SECRET='(>=32 bytes)' \
  pnpm --filter @comit/api auth:mail-worker
```

`AUTH_SECRET`/`JWT_SECRET`, `AUTH_ALLOWED_ORIGINS`, `ACCOUNT_CONSENT_VERSION` и
SMTP-параметры передаются через секретное окружение; значения не печатаются и не
записываются в репозиторий. Worker перед SMTP проверяет lease и актуальность
challenge. После доставки/истечения/пяти неудачных попыток зашифрованный payload
удаляется. Повтор письма после crash между SMTP ACK и фиксацией ACK возможен;
реальную доставку этим локальным запуском не считать проверенной.

Опциональный compose overlay `infra/docker-compose.auth.yml` добавляет только
auth-migration и mail-worker, использует существующий PostgreSQL и внешний SMTP;
новый SMTP-сервис не создаётся. Не применять `down -v` для auth-проверки.

Тестовые БД auth должны быть раздельными и одноразовыми:

```bash
AUTH_TEST_DATABASE_URL='postgres://...' \
AUTH_ROUTE_TEST_DATABASE_URL='postgres://...' \
  pnpm --filter @comit/api test
```

Без этих переменных DB acceptance тесты пропускаются; crypto/unit и transport
проверки не доказывают работу PostgreSQL или реальную доставку.

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

## Создание вакансий: локальные проверки

Сервис и opt-in контейнер описаны в [agents.md](agents.md). Перед запуском явно
применить миграции `python -m agents_service migrate`; `/ready` проверяет таблицы.
Для smoke дополнительного контура задать `AGENTS_PORT` — проверяются liveness и БД.
При ошибке публикации повторять job через `/agents/jobs/{id}/retry`: снимок после
Create заморожен. `created` означает ACK intake; вектора и готовность читать через
`/vacancy-drafts/{id}/pipeline`.

Реальная синтетическая приёмка запускается отдельно от unit-тестов:

```sh
PYTHONPATH=services/agents-service/src:services/matching-engine/src \
  python tests/vacancy_creation_acceptance.py --provider-file /path/to/provider-config.txt
```

Требуются отдельная `AGENTS_ACCEPTANCE_DATABASE_URL`, работающие API/agents/matching,
локальный Laya и Harrier; адреса и внутренние токены передаются через env.
Файл провайдера читается только в память процесса. Скрипт пишет игнорируемый протокол
`tests/artifacts/vacancy-creation-acceptance.json`, проверяет manual/Laya/DeepSeek,
summary/edit/Create, ownership, вектора, события и карточки. Для продолжения
проверки уже созданных вакансий использовать `--resume-report` с этим протоколом.
Это проверка интеграции; качество выведенных моделью уровней требует калибровки.

## Сервер (mgg-server): операции

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

## Baseline R₀

Применить новую миграцию штатным `python -m matching_engine migrate`, затем
запустить отдельный `python -m matching_engine rerank-worker`. Существующие
candidate/vacancy/combine workers продолжают прежний поток. Порты не добавляются.
Без активной модели worker публикует явный main_fallback.
Operator регистрация и активация разделены; не обходить gates ради демонстрации.
При stale result проверять сначала main run, затем artifact/feature/tail hashes.
Retry сохраняет идемпотентность и не возвращает просмотренных кандидатов.

### Ограниченный offline эксперимент

Из корня репозитория использовать Python окружение matching-engine с extras
`dev` (включает sklearn для проверок) или `training` для отдельного обучения.
В командах ниже `PYTHONPATH=services/matching-engine/src`; путь к Python —
`services/matching-engine/.venv/bin/python`. Запускается только локальный jev-local;
команды не управляют его worker/model settings.

1. `python tests/rerank_pilot.py`:5 профилей/20 сравнений, без расхода grade-бюджета.
2. `python -m matching_engine.rerank_experiment manifest`, затем `generate`.
   Последняя команда отказывает после проваленного пилота. Лимиты с учётом
   повторов:60 семейств/600 профилей/3000 pair/600 grade; cache/usage сохраняются.
3. `python -m matching_engine.rerank_experiment train`: только cached данные.
   Source/семейства фиксированы, нормализация train-only, C выбирается по validation.
   Dataset/code seals запрещают настройку по уже раскрытому golden; максимум2
   попытки обучения в одном эксперименте, одинаковый успешный запуск replay.
4. `python -m matching_engine.rerank_experiment review-package`: раздельные blind
   JSON packets по20 пар. Ответы: список `{pair_id,choice,factual_issue}`;
   choice=A/B/tie/insufficient, factual_issue=null либо описание реальной ошибки.
   Assistant review не заменяет human review и не считается человеческой статистикой.
5. `python -m matching_engine.rerank_experiment seal-reviews --user-review <file> --assistant-review <file>`
   создаёт новый `artifact-reviewed.json` с реальными receipts. Ответы пользователя
   автоматически не заполняются. Ошибки/непройденные gates запрещают активацию.
6. При настроенном DATABASE_URL: `python -m matching_engine.rerank_experiment register --artifact <file>`
   возвращает SHA; отдельный `promote --hash <SHA>` повторно проверяет gates и
   атомарно переставляет active pointer/ставит задания актуальным main batches.

По умолчанию artifacts расположены в `tests/artifacts/reranker/`, git-ignored.
В live worker нет sklearn, provider calls и обучения. Провал пилота запрещает
расширение: не обходить блокировку изменением JSON-флага или увеличением бюджета.

С 06.10 teacher заменён на `https://ai.sverk.tech/v1/systemone`, `kev-latest`.
Ключ читается только при cache miss из существующего `~/Desktop/qwen_token.txt`;
другой файл задаётся переменной `RERANK_SYSTEMONE_KEY_FILE` (только путь).
Файл может содержать один голый ключ либо явно помеченную секцию Sverk среди
других провайдеров; неоднозначность останавливает вызов. Ключ не копируется
в проект, identity, кэш или отчёт. HTTP-редиректы запрещены.
В API отправляются только синтетические факты, без score/feature vectors;
`choice` и независимый `score` читаются из `answers`, проверяются распределения,
модель и отсутствие усечения. Устаревшие pilot/family receipts другого teacher
не допускаются. Исторический Qwen3.5 остаётся replayable; старый ledger продолжает учитывать
все прошлые попытки Laya и не сбрасывается при смене teacher.
Первый SystemOne pilot дал 8/10 согласованных перестановок; исходный неуспешный
Laya pilot сохраняется отдельно. Смена API сама по себе не активирует R₀.

Контракт адаптера сверён с [исходниками Kev](https://github.com/jaredpalmer/kev/blob/main/kev/api.py)
и реальными ответами указанного API; актуальные результаты — в `data/validation/`.
После прямого уточнения Mike генераторы Qwen/Gemma используют обычный Sverk
OpenAI endpoint с тем же ключом: `qwen3.8-27b` и `gemma4-vlm`, до пяти общих HTTP
вызовов. Harrier остаётся локальным. Батчи и завершённые семейства имеют
проверяемое происхождение; старые валидные результаты не генерируются повторно.
Явные cache-only операции `reuse-batch` и `seal-family`, их ограничения и
команды описаны в [matching.md](matching.md#генерация-через-sverk-openai-api-06102026).
Исторический невалидный профиль исключён с receipt без исправления фактов.
Текущее расширение остановлено после 12 семейств /119 профилей: на frontend-05
SystemOne согласовал1/3 перестановок при минимуме2/3. Дополнительный ограниченный
pilot Qwen/Gemma дал10валидных профилей для frontend-06; Gemma заняла13.87с.
Новый генератор не снимает блокер качества teacher. Raw responses/ledger и
прежние отчёты сохраняются; обучение на неполном dataset и активация запрещены.
Не изменять диапазоны, метки, receipts или бюджет вручную для обхода проверок.

Для cache-only расследования frontend-05 и отдельного двадцатизапросного pilot
использовать `python -m matching_engine.rerank_diagnostics audit|pilot` с тем же
PYTHONPATH. Opt-in pair v2 исправил3исходныхswaps, но весь pilot остановлен:
raw7/10, derived8/10, decisive5/10; ledger28/676/119. Повтор сохранённого отчёта
не отправляет HTTP; interrupted started receipt блокирует automatic retry.
Условия, source hashes, blind assistant receipt и commands —
[diagnosis](rerank-frontend05-diagnosis.md). Passed robustness pilot сам по себе
также не разрешает обучение, смешивание teacher versions или активацию R₀.

Альтернативный Gemma pilot: `python -m matching_engine.rerank_gemma_pilot prepare|run`
с тем же PYTHONPATH. Требует sealed blind review; сохраняет отдельные model/prompt
receipts, использует общий pair ledger и партии максимум пяти HTTP-вызовов.
Первый запуск stopped после пяти ответов с ошибками формата; повтор читает кэш
без новых запросов. Ledger28/681/119. Новый format probe автоматически не запускается.
Источник, команды и ограничения — [пилот Gemma](rerank-gemma-teacher-pilot.md).

### Повтор backend приёмки

При настроенном loopback `PIPELINE_TEST_DATABASE_URL`, установленном embeddings
extra и exact cached Harrier выполнить `python tests/rerank_acceptance.py`.
Создаётся новая БД с уникальным именем `comit_reranker_acceptance_*`; существующие
БД не очищаются. Скрипт использует сохранённые synthetic pilot-profiles и настоящий
Harrier, проверяет typed facts/PDFs/durable delivery/backfill. Не запускает teacher.
После `pnpm build` выполнить `python tests/rerank_http_acceptance.py`: только
короткие собственные FastAPI/Fastify процессы на свободных портах, с cleanup.
Отчёт и service logs сохраняются в ignored artifacts; credentials не печатаются.
