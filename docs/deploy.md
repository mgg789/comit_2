# Deploy — production по умолчанию

`infra/docker-compose.yml` содержит полный production runtime. `docker-compose.prod.yml`
сохраняет совместимость существующего deploy workflow и содержит тот же стек.
Наружу публикуется один web: `/`, `/candidate/`, `/employer/`, `/testing/`, `/api/`.
Testing UI обращается к API от имени кандидата; testing-engine остаётся внутренним backend. Его порт, как и
порты API/engines, опубликован только на loopback для smoke и диагностики.

## Подготовка

Скопировать `.env.example` в приватный `.env`, заполнить все обязательные значения.
Compose намеренно останавливается при пустом production секрете. Обязательны:
`POSTGRES_PASSWORD`, `AUTH_SECRET` (не менее 32 байт), `AUTH_ALLOWED_ORIGINS` (HTTPS),
`PUBLIC_SITE_URL` (HTTPS), `TRUST_PROXY` (доверенные proxy IP/CIDR),
`CANDIDATE_PIPELINE_TOKEN`, `AGENTS_SERVICE_TOKEN`, `VERIFICATION_ENGINE_TOKEN`,
`VERIFICATION_DOCUMENT_KEY`, `TESTING_ENGINE_TOKEN`, `CHAT_SERVICE_TOKEN`,
`CHAT_DEVICE_SECRET`, `CHAT_SIGNING_KEY`, `QGRAMM_MANAGEMENT_SECRET`,
`QGRAMM_MASTER_KEY`, `QGRAMM_HPKE_KEY`, `SMTP_USER`, `SMTP_PASSWORD`,
`DEEPSEEK_API_KEY`. `JEV_API_KEY` и `JEV_LOCAL_URL` — дополнительные провайдеры: без них соответствующие ИИ-возможности явно недоступны, ручное редактирование остаётся доступным.
Ключи QGramm сохранять неизменными вместе с `chat-data`; base64 ключи должны
декодироваться в 32 байта. Значения API pricing должны соответствовать реальным тарифам.

Перед первым запуском provision точную Harrier revision в `harrier_cache`:

```bash
docker compose --env-file .env -f infra/docker-compose.yml build matching-engine
docker compose --env-file .env -f infra/docker-compose.yml run --rm --no-deps matching-engine python -c 'from huggingface_hub import snapshot_download; from matching_engine.embeddings import MODEL_ID, MODEL_REVISION; snapshot_download(MODEL_ID, revision=MODEL_REVISION)'
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
make up
make smoke
```

Provisioning скачивает публичную модель; runtime использует только точный локальный
snapshot. `/ready` matching возвращает 503 при отсутствии модели или миграций.
Ни модель, ни сторонние AI/SMTP endpoints не разворачиваются автоматически.
По разрешению пользователя production использует существующий SMTP relay Navix:
`SMTP_HOST=host.docker.internal`, `SMTP_PORT=2525`. API и mail worker получают
`host.docker.internal:host-gateway`; SMTP credentials и sender повторно используются
только через приватное server env. `SMTP_TLS_SERVERNAME=mail.droidje.com` сохраняет
проверку сертификата при STARTTLS. Проверка TLS обязательна, relay и его конфигурация
этой интеграцией не изменяются.
Отсутствующий `HH_API_TOKEN` оставляет HH verification явно недоступной.
Production выключает OTP code exposure и FSP demo fixtures. Реального FSP registry
контракта пока нет: 503 означает недоступную интеграцию, а не подтверждённую историю.

## Порядок и данные

Migration jobs сериализованы: PostgreSQL healthy → candidate-migrate → agents-migrate
→ auth-migrate. Последний применяет все API migrations, включая expense triggers
для уже существующих agents tables. HTTP runtime и workers ждут необходимых migrations;
API дополнительно ждёт готовые engines/chat, web ждёт API readiness.
Работают auth/notification mail worker, candidate profile и recommendation workers,
candidate/vacancy/combine/rerank/feed-refill/preference workers и оба agents workers
(`agents-worker`, `organization-agent-worker`). `services/code-judge` в compose не входит.

Project name `comit`, volumes `pgdata`, `harrier_cache`, `chat-data` и `testing-data` сохранены.
`make down` сохраняет volumes. `down -v` запрещён без отдельного разрешения пользователя.

## Явный dev-режим

```bash
make dev-up
SMOKE_MODE=dev make smoke
make dev-down
```

Для тестов с кодом сначала подключите изолированный judge по инструкции ниже (`CODE_JUDGE_URL`, `CODE_JUDGE_TOKEN`).
Это отдельный `infra/docker-compose.dev.yml` с `--profile dev`, project `comit-dev`
и изолированными volumes. Не объединять dev/prod файлы: development настройки
заданы только в явном dev entry point. Dev Makefile явно использует `--env-file /dev/null`, поэтому production `.env`
не читается. Для собственной dev-конфигурации используйте отдельный `--env-file`. Передать другие host ports, если
production уже работает. Здесь разрешены localhost HTTP cookies, synthetic
non-secret service keys, локальная выдача OTP и FSP demo. Mail worker по умолчанию исключён из dev
(профиль `prod` в dev-файле требует реального SMTP); OTP не создаёт mail outbox.
Уведомления доступны в системном inbox, внешняя email доставка dev не включена. AI providers остаются
реальными; без их конфигурации запросы явно недоступны. Harrier provision необходим
и для dev (те же run/build команды с dev файлом, project `comit-dev`, `--profile dev`).

## Сервер

Deploy target: `/home/mgg/comit`, пользователь `mgg`, существующий self-hosted workflow.
Приватный `/home/mgg/comit/.env` перед deployment должен быть дополнен указанными
обязательными настройками. Deploy workflow использует production compatibility file.
Интерактивные операции на сервере требуют явной команды пользователя.
Smoke проверяет bounded HTTP readiness, storage showcase, auth boundaries и доступность
testing UI; curl не проверяет JS userflow, доставку SMTP или реальный AI вызов.

## CD: exact SHA, backup и rollback

`Deploy` запускается после успешного `CI` на `main`; ручной запуск также проверяет
последний успешный CI именно для текущего SHA `main`. Устаревший результат не
разворачивается. GitHub concurrency и host `flock` сериализуют переходы.
`infra/deploy.py` извлекает `git archive` в `/home/mgg/comit/releases/<SHA>`.
Приватный env читается только через `--env-file /home/mgg/comit/.env`, не копируется
в checkout/release и не выводится в лог. Требуются Python 3, Docker Compose v2
с `--wait`, существующие production volumes и доступные исходные Compose файлы
текущего runtime. Первый bootstrap и исправление неполного предыдущего snapshot
требуют отдельной подготовки: CD останавливается до изменения работающих сервисов.

До перехода проверяются resolved Compose, HTTPS origins, secure cookies, production
mode, отключённые dev flags и точные identities `comit_pgdata`, `comit_chat-data`,
`comit_harrier_cache`. Образы собираются заранее с уникальными SHA tags и OCI
`org.opencontainers.image.revision`; build label проверяется до запуска. Runtime
получает `COMIT_RELEASE=<SHA>`. Новая попытка с уже существующим release directory
останавливается: сначала нужно расследовать предыдущую попытку.

Перед migration jobs создаётся PostgreSQL custom-format backup с правами 600 в
`/home/mgg/comit/backups`; `pg_restore --list` проверяет читаемость архива.
Это проверка структуры архива, не практическая проверка восстановления данных.
Миграции должны быть additive и совместимыми с предыдущими app images: проверка
миграций входит в code review перед `main`, скрипт не доказывает совместимость SQL.
Во время успешного перехода выполняются local smoke и публичный HTTPS smoke с
проверкой readiness, showcase, auth boundaries и доступности testing UI.

При ошибке запуска/smoke CD пытается восстановить предыдущие immutable image IDs
приложений через предыдущие Compose файлы с `--no-deps --no-build`. PostgreSQL,
volumes и migration jobs rollback не запускает; downgrade/restore базы автоматически
не выполняется. Rollback при несовместимой миграции не гарантируется; требуется
ручной план восстановления. Служебный manifest содержит SHA, backup path и image
IDs, но не environment. `/home/mgg/comit/current` переключается только после smoke.
Не выполняются `down`, удаление volumes или image prune.

Локальная регрессия preflight: `python3 tests/deploy-config.py`.


### Первый переход с legacy stack

Для проверенного старого runtime (ровно `web`, `api`, `matching-engine`, `postgres`,
`testing-engine`, `integration-engine`) предусмотрен отдельный ручной input
`adopt_legacy=true`. Workflow передаёт `--adopt-legacy` только при manual dispatch;
автоматический CI-trigger никогда не включает adoption. SHA всё равно должен быть
текущим `main` с успешным CI. Флаг отвергает любую другую комбинацию live services.

Перед этим переходом оператор отдельно provision'ит `comit_chat-data` и
`comit_harrier_cache`, включая exact Harrier snapshot; deploy их не создаёт.
Фактический live mount `comit_pgdata` проверяется и сохраняется. Старому matching
без `/models` не приписывается новый volume: mount добавляется только новой версии.
При неудаче останавливаются только вновь введённые сервисы (включая workers и
migration containers), без удаления containers/volumes; image rollback возвращает
пять прежних приложений через старый Compose с `--no-deps --no-build`.
База остаётся после additive migrations; прежние образы должны быть совместимы
с ними. Последующие deployments используют строгий полный snapshot без adoption.

Backend тестирования получает `TESTING_API_URL` и внутренний токен; данные сессий сохраняются в `testing-data`. UI доступен по `/testing/`; попытки и запись проходят только через авторизованный API.


Rollback adoption читает только заранее подготовленный приватный snapshot
`/home/mgg/comit/legacy/compose.json` (права 600): точный resolved старый Compose,
ровно шесть legacy services, project `comit`, прежний `comit_pgdata`.
Resolved snapshot содержит приватные environment значения; его нельзя печатать
или сохранять в Git/artifacts. Его bind sources (nginx config, postgres init и другие)
нужно предварительно скопировать внутрь `/home/mgg/comit/legacy/` и скорректировать
пути snapshot: runner workspace меняется при checkout. Скрипт отвергает mutable bind
sources. При обычном CD предыдущие Compose files и working directory должны быть
в `/home/mgg/comit/releases/<SHA>/`; runner workspace источником rollback не служит.

Домен `comit.droidje.com` обслуживает host nginx (образец: `infra/nginx/comit.droidje.com.conf`), проксируя gateway на `127.0.0.1:8088`. HTTP ACME challenge обслуживается из `/var/www/comit-acme`, остальные HTTP-запросы перенаправляются на HTTPS. Сертификат Let’s Encrypt находится в `/etc/letsencrypt/live/comit.droidje.com/`; host `certbot.timer` включён, scoped deploy hook проверяет nginx и перезагружает его после продления именно этого домена. Перед изменением vhost обязательны `nginx -t` и проверка внешнего ACME маршрута.

SMTP использует существующий host relay Navix на порту 2525; STARTTLS проверяет имя `mail.droidje.com`. Учетные данные хранятся только в server `.env`, TLS-проверка не отключается. Сам SMTP/Navix сервис не модифицируется.

При первом переходе автоматический CD main заранее отклоняет legacy-стек, не занимая каталог SHA; тот же проверенный SHA затем можно выкатить ручным dispatch с `adopt_legacy=true`. Повтор после ошибки сборки существующего release требует сначала осмотреть его состояние; deployment не перезаписывает release автоматически.


После `compose up --wait` CD дополнительно проверяет все фоновые workers дважды с интервалом 20 секунд: контейнер должен работать, иметь нужный образ и метку релиза, не иметь перезапусков и проходить healthcheck, если он задан. Перезапуск worker приводит к откату приложения. Пустой `JEV_LOCAL_URL` означает отключённую необязательную Laya: он не мешает запуску worker публикации/DeepSeek, а обогащение остаётся недоступным с ручным редактированием.

Статические файлы всех четырёх приложений в web-образе получают права чтения для nginx worker. Это обязательно при сборке приватного immutable release (`umask 077`): Vite сохраняет права копируемых public assets. Smoke проверяет содержимое SVG логотипа и иконки витрины, чтобы HTTP 403 или SPA fallback не маскировались успешной загрузкой главной.

## Запись тестирования

`TESTING_RECORDING_MODE=off` по умолчанию: камера обрабатывается на устройстве,
сервер получает агрегаты присутствия и фокуса. Полная запись включается явно
значением `full` после согласования эксплуатации; затем требуются согласия кандидата.
Фрагменты экрана и камеры сохраняются только во внутреннем `testing-data`,
удаляются через 90 дней и не выдаются работодателю. Нельзя публиковать этот volume
через nginx. Модель определения лица и WASM поставляются в web-образе, внешних
запросов для обработки камеры нет. Проверка `make smoke` проверяет маршрут UI и
границу авторизации; разрешения устройств и весь сценарий проверяются браузером.


### Изолированная проверка кода

Перед запуском полного dev-стека обязательно задайте адрес существующего изолированного judge в `CODE_JUDGE_URL` и его `CODE_JUDGE_TOKEN`: без них dev compose останавливается с понятной ошибкой конфигурации. Несуществующее локальное имя сервиса по умолчанию не используется.

Основной compose приложения получает только `CODE_JUDGE_URL` и отдельный
`CODE_JUDGE_TOKEN`. Пустой URL закрывает начало тестов по Python и алгоритмам:
проверка кода должна быть доступна до расходования попытки. В production
Docker socket приложению не передаётся.

`infra/docker-compose.judge.yml` запускается **только на отдельной disposable VM**,
где нет приложения, пользовательских данных и посторонних секретов. Broker
получает socket выделенного Docker daemon и потому является доверенной частью
этой VM: компрометация broker равносильна компрометации VM. Этот compose нельзя
запускать на сервере приложения. Перед запуском оператор загружает образ Python
по digest из compose и задаёт отдельный token и `DOCKER_SOCKET_GID` (группа socket).
После загрузки образов исходящий доступ VM закрывают сетевой политикой; дочерние
контейнеры дополнительно запускаются с `--network=none` и `--pull=never`.

Broker слушает только `127.0.0.1:8106`. Доступ testing-engine предоставляется через
приватный TLS ingress внутри judge VM, разрешённый только серверу приложения.
Для частного CA задайте `CODE_JUDGE_CA_PEM` (PEM с разделителями `\n`) вместе с
`CODE_JUDGE_URL=https://…`: CA добавляется к системному trust store только клиента
judge; проверка цепочки и hostname остаётся обязательной. IP в URL должен быть
в SAN сертификата. Неверный CA закрывает readiness и не расходует попытку.
Token хранится в
серверном окружении и ротируется отдельно от остальных сервисов. Liveness
`/healthz` не подтверждает изоляцию: authenticated `/ready` запускает реальную
проверку UID, capabilities, read-only FS, сети и действующих CPU/memory/PID limits.
При провале проверки новые попытки блокируются. Сбой при отправке ответа
возвращает 503 и сохраняет возможность повторить тот же ответ.

Для разработки разрешён тот же отдельный judge либо одноразовая локальная VM с
синтетическими заданиями. Основной dev compose автоматически не монтирует Docker
socket. Проверка реального sandbox выполняется отдельно:
`CODE_JUDGE_DOCKER_TESTS=1 python -m pytest services/code-judge/tests/test_docker_acceptance.py -q`.

Развёрнутые ответы проверяются Jev (`JEV_API_KEY`, `JEV_API_URL`, `JEV_MODEL`,
по умолчанию `kev-latest`); если ключ Jev не задан — DeepSeek
(`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`). Сбой настроенного Jev
не подменяется молча другим провайдером: ответ остаётся доступен для повтора.
Провайдер оценивает заданные сервером критерии; итоговый грейд рассчитывает движок.

### Проверенное подключение на mgg (2026-10-11)

VM `comit-code-judge` имеет 2 vCPU, 2 GiB RAM и 12 GiB диска, автозапуск;
выделенные storage `comit-judge` и bridge `comitjudge0` (10.239.76.0/28).
Guest Docker содержит только broker и pinned Python runtime. TLS ingress слушает
10.239.76.2:8443; сертификат содержит этот IP в SAN. Ключ и отдельный token
хранятся внутри guest, клиентские настройки — в приватном `/home/mgg/comit/.env`.

LXD ACL `comit-judge-closed` закрывает исходящий доступ и разрешает TLS ingress
от приложения/хоста. Host guard `comit-judge-network-guard` поддерживает только
правила интерфейса `comitjudge0`: DHCP, ответы на разрешённые соединения и
app bridge → judge TLS; остальные входящие и исходящие соединения блокируются.
IPv6 guest отключён, на интерфейсе также действует IPv6 drop. Guard имеет только
NET_ADMIN, без mount и Docker socket. Его исходники лежат в
`/home/mgg/comit-judge-ops/`; текущий постоянный образ имеет тег `:2`, исправленный
bootstrap cleanup проверен отдельно в образе `:3`.

Временный доступ к репозиториям ограничивался 30 минутами через kernel expiry.
После установки bootstrap container и все его исключения удалены; broker
запускался после закрытия этого доступа. Для будущих установок используйте
исправленный cleanup с уникальными комментариями правил, без общего flush.

Проверены реальная готовность и выполнение кода из сети приложения, отказ при
неверном CA/SAN/токене, блокировка исходящих TCP/DNS и повтор полного сценария
после перезапуска VM. Основной стек приложения и SMTP не перезапускались.
Следующий CD должен подхватить три judge env и пройти smoke до переключения
release; эти проверки подключения сами по себе не подтверждают deployment UI.

## Необязательные настройки окружения

Кроме обязательных секретов выше, код читает следующие переменные (значения по
умолчанию — из кода; compose задаёт часть из них явно):

| Переменная | Где | По умолчанию | Назначение |
|---|---|---|---|
| `WEB_PORT`, `API_PORT`, `MATCHING_PORT`, `TESTING_PORT`, `INTEGRATION_PORT`, `AGENTS_PORT`, `VERIFICATION_PORT` | compose | 8088, 28080, 28101, 8102, 8103, 28104, 28105 | host-порты; все, кроме web, только на `127.0.0.1` |
| `API_RATE_LIMIT`, `API_RATE_WINDOW_SECONDS`, `API_RATE_FALLBACK_KEYS` | api | 240, 60, 10000 | квота запросов ([api-operations.md](api-operations.md)) |
| `PIPELINE_MAX_ATTEMPTS`, `PIPELINE_LEASE_SECONDS`, `PIPELINE_RETRY_BASE_SECONDS`, `PIPELINE_POLL_SECONDS` | matching workers | 5, 300, 30, 2 | попытки, lease, backoff и опрос очередей (compose задаёт 5/300) |
| `AGENTS_LEASE_SECONDS`, `AGENTS_POLL_SECONDS` | agents workers | 300, 2 | lease и опрос очередей агентов |
| `AGENT_MAX_ROUNDS`, `AGENT_MAX_OUTPUT_TOKENS`, `AGENT_MAX_INPUT_TOKENS`, `AGENT_CONTEXT_COMPACTION_BYTES` | organization-worker | 8, 1024, 128000, 90000 | лимиты глобального агента работодателя |
| `AGENT_INPUT_USD_PER_MILLION`, `AGENT_OUTPUT_USD_PER_MILLION`, `AGENT_CACHED_INPUT_USD_PER_MILLION` | agents | 0.2, 0.7, 0.2 (compose) | тарифы DeepSeek для учёта расходов |
| `JEV_API_URL`, `JEV_MODEL`, `JEV_PROVIDER`, `JEV_MAX_OUTPUT_TOKENS`, `JEV_PRICING_VERSION`, `JEV_INPUT_USD_PER_MILLION`, `JEV_OUTPUT_USD_PER_MILLION` | agents | `https://ai.sverk.tech/v1/systemone`, `kev-latest`, `systemone`, 0, `user-2026-10-07`, 0.042, 0 | необязательный провайдер Jev/SystemOne для свайпов агента |
| `JEV_LOCAL_URL` | agents | production compose — пусто, dev — `http://host.docker.internal:12444`, вне compose — `http://127.0.0.1:12444` | Laya; пустое значение отключает обогащение (`laya_not_configured`), непустое допускает только `localhost`, `127.0.0.1`, `host.docker.internal` |
| `ORGANIZATION_API_URL` | agents | `http://api:8080` (compose) | API bridge инструментов агента |
| `CHAT_SERVICE_URL` | api | `http://qgramm-chat:8080` (compose) | приватный адрес QGramm bridge |
| `FSP_DEMO_ENABLED`, `FSP_DEMO_INTERNAL_TOKEN`, `FSP_DEMO_CALLBACK_URL`, `FSP_DEMO_PUBLIC_URL`, `APP_ENV` | api, integration, verification | выключено; dev-файл включает | демо-привязка ФСП работает только при `NODE_ENV=development`/`APP_ENV=development` и `FSP_DEMO_ENABLED=true`; интерфейс маркирует demo |
| `HH_API_TOKEN`, `HH_USER_AGENT` | verification | пусто, `comit-verification/0.1` (compose) | HH-проверка; без токена — `hh_token_unavailable`, без user agent — `hh_user_agent_unavailable` |
| `BLUEPRINT_DIR`, `TESTING_DATA_DIR`, `TESTING_API_URL` | testing | `/app/blueprints`, `/data/testing`, `http://api:8080` (compose) | blueprint-каталог, хранилище и адрес publisher адаптивного контура |
| `TESTING_RECORDING_MODE`, `CODE_JUDGE_URL`, `CODE_JUDGE_TOKEN`, `CODE_JUDGE_CA_PEM` | testing | `off`; judge задаётся оператором | режим записи, адрес изолированного judge, отдельный token и optional частный CA ([testing-engine.md](testing-engine.md)) |
| `CODE_JUDGE_PORT`, `CODE_JUDGE_DATA_DIR`, `DOCKER_SOCKET_GID` | изолированный code-judge | 8106, `/data`, GID socket задаётся оператором | отдельный judge compose на disposable VM |
| `DEV_AUTH_EXPOSE_CODE` | api | `false` | только dev: OTP в ответе ([auth.md](auth.md)) |

### Отдельная внешняя проверка после CD

После успешного server deploy job `public-smoke` выполняется на GitHub-hosted
Ubuntu, вне mgg. Адрес берётся из repository variable `PUBLIC_SITE_URL`, по
умолчанию `https://comit.droidje.com`. Он использует системные CA и проверку
hostname, не следует redirects и не передаёт cookies или секреты.

`infra/public-smoke.py` проверяет четыре HTML-приложения, SVG assets, публичные
`/api/healthz` и `/api/ready` (JSON service/status), обе витрины (pagination
contract), три границы входа (401 + JSON unauthenticated), скрытый internal
маршрут (404). Ответы ограничены 2 MiB и 15 секундами; до трёх попыток.
HTTP 200 с HTML вместо API JSON, неверный service или неправильная структура
считаются ошибкой. Содержимое профилей и response bodies в журнал не выводятся.

Ошибка внешнего job делает workflow CD красным, но не запускает автоматический
rollback уже проверенного сервером release: оператор проверяет домен/gateway и
следует штатной инструкции rollback. Серверный deploy сохраняет прежние локальные
и публичные status-проверки до переключения current. Этот job проверяет HTTP
контракты, а не браузерный JS userflow, SMTP-доставку или пользовательский вход.
Проверка вручную: `python3 infra/public-smoke.py https://comit.droidje.com`.
