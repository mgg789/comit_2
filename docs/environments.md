# Контуры: production и dev

В репозитории два явно разделённых способа запуска. Они собирают одни и те же
образы, но различаются compose-файлом, project name, volumes, секретами и
поведением auth/ФСП. Файлы нельзя объединять или запускать одновременно без
смены портов: production по умолчанию, dev — всегда явный.

| | Production (`make up`) | Dev (`make dev-up`) |
|---|---|---|
| Compose-файл | `infra/docker-compose.yml` (+ `docker-compose.prod.yml` для CD) | `infra/docker-compose.dev.yml` + `--profile dev` |
| Project / volumes | `comit`, `comit_pgdata`, `comit_chat-data`, `comit_harrier_cache` | `comit-dev`, изолированные dev-volumes |
| Env-файл | приватный `.env` (копия `.env.example` с реальными секретами) | `--env-file /dev/null`: production `.env` намеренно не читается |
| `NODE_ENV` | `production` | `development` |
| Cookies | `__Host-comit_*_session`, только HTTPS (`AUTH_COOKIE_SECURE=true` обязателен) | localhost HTTP, `AUTH_COOKIE_SECURE=false` |
| Вход по email | одноразовый код только письмом через SMTP; API код не возвращает | код автоматически подставляется на форму (`DEV_AUTH_EXPOSE_CODE=true`), SMTP не нужен |
| Мок ФСП | выключен (`/fsp/participants/*` → 503) | включён (`FSP_DEMO_ENABLED=true`, демо-привязка работает) |
| Auth mail worker | работает (durable SMTP outbox) | исключён (профиль `prod` в dev-файле) |
| AI-провайдеры | реальные ключи (DeepSeek обязателен, Jev опционален) | те же провайдеры; без конфигурации возможность явно недоступна |
| Порты по умолчанию | web `8088`, api `28080`, matching `28101`, testing `8102`, integration `8103`, agents `28104`, verification `28105`, postgres — без публикации | те же + postgres на `127.0.0.1:5433` |
| Обновление | CD: push в `main` → immutable release на сервере | вручную: `make dev-up` (пересобирает изменённые образы) |

Все служебные порты, кроме web, публикуются только на `127.0.0.1` — для smoke и
диагностики. Наружу в production смотрит один web-шлюз.

## Поднять production локально (или на сервере)

```sh
cp .env.example .env       # заполнить все обязательные значения (см. ниже)
make up                    # production по умолчанию
make smoke                 # health/readiness и публичные маршруты
make logs                  # хвост логов
make down                  # остановить, volumes сохраняются
```

Обязательные значения `.env`: `POSTGRES_PASSWORD`, `AUTH_SECRET` (≥32 байт),
`AUTH_ALLOWED_ORIGINS` (HTTPS), `PUBLIC_SITE_URL` (HTTPS), `TRUST_PROXY`
(точный IP/CIDR nginx), все сервисные токены и ключи чата (перечислены в
`.env.example`), `SMTP_USER`, `SMTP_PASSWORD`, `DEEPSEEK_API_KEY`. Compose
останавливается при пустом production секрете.

Перед первым запуском провижинить точный snapshot модели Harrier в локальный
cache (команда — в [deploy.md](deploy.md), раздел «Подготовка»): без него
`/ready` matching останется 503.

Порядок старта сериализован: PostgreSQL → миграции (`candidate-migrate`,
`agents-migrate`, `auth-migrate`) → HTTP-сервисы и workers → web. Ручные
миграции отдельно запускать не нужно.

## Поднять dev

```sh
make dev-up                # отдельный compose-проект comit-dev
SMOKE_MODE=dev make smoke
make dev-down              # данные dev-volumes сохраняются
```

- Приложение: http://localhost:8088 (`/`, `/candidate/`, `/employer/`, `/testing/`, `/support/`).
- Вход: любой email, код на форме появляется сам (`dev_code` в ответе API);
  в этом режиме письма не отправляются и mail outbox не пишется.
- Мок ФСП доступен, сценарий демо-привязки работает end-to-end.
- Production `.env` не читается: dev-секреты имеют безопасные значения по
  умолчанию. Исключение — `CODE_JUDGE_URL` и `CODE_JUDGE_TOKEN` изолированного judge:
  их нужно экспортировать в shell перед `make dev-up`, иначе compose остановится
  ([подключение](deploy.md#изолированная-проверка-кода)). Свой dev-env передавайте
  отдельным `--env-file`.
- Если production-стек запущен одновременно, разведите порты через переменные
  `WEB_PORT`, `API_PORT` и т.д.

## Запуск без Docker (точечно, для разработки)

```sh
pnpm install
pnpm --filter @comit/api auth:migrate      # миграции API
pnpm --filter @comit/api dev               # api-core на http://127.0.0.1:8080
pnpm --filter @comit/web-candidate dev     # vite :5173, /api проксируется на 8080
pnpm --filter @comit/web-employer dev      # :5174
pnpm --filter @comit/web-testing dev       # :5175
pnpm --filter @comit/web-welcome dev       # :5176
pnpm --filter @comit/web-support dev       # :5177
```

`DEV_AUTH_EXPOSE_CODE=true NODE_ENV=development` даёт тот же автоподставляемый
код; вне development флаг отвергается конфигурацией. Engines запускаются своими
`python -m` entrypoints. Чат QGramm работает в Docker-контуре; без него кабинеты
показывают в чатах понятный статус `503`, а остальные функции работают как обычно.

## Проверки контуров

- `make smoke` — production-проверка: `/healthz` всех сервисов, `/ready` API,
  matching и agents, все пять web-приложений (включая `/testing/` и `/support/`),
  скрытие `/api/internal/*`, границы auth (401), публичная витрина, `503` ФСП-мока.
- `SMOKE_MODE=dev make smoke` — то же с ожиданиями dev-режима (ФСП-фикстуры 200).
- `make compose-check` — валидация resolved compose-файлов.
- CD дополнительно проверяет HTTPS origins, secure cookies, отключённые dev
  флаги и точные identity volumes ([deploy.md](deploy.md)).

## Типичные ошибки

- **Запуск dev с production `.env`.** Dev Makefile намеренно использует
  `--env-file /dev/null`; свой файл подключайте явно и только для dev.
- **Одновременный запуск двух контуров на одних портах.** Задайте разные
  `WEB_PORT`/`API_PORT`/... одному из них.
- **`down -v` без явного разрешения** — удаляет volumes вместе с данными демо.
- **Смена ключей QGramm при живом `chat-data`.** Ключи и volume резервируются
  и меняются только вместе ([chat.md](chat.md)).
