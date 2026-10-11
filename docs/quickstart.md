# Быстрый старт и онбординг

Путь нового разработчика от клонирования до первого изменения. Все команды взяты
из `Makefile`, `package.json` и compose-файлов репозитория; подробности
контуров — [environments.md](environments.md).

## 1. Что понадобится

| Инструмент | Версия | Зачем |
|---|---|---|
| Docker + Docker Compose v2 | — | dev- и production-стек |
| GNU Make | — | короткие команды `make dev-up`, `make smoke` |
| Node.js | ≥ 22 (`engines` в `package.json`) | api-core и фронтенды без Docker |
| pnpm | 9.14.2 (`packageManager`) | монорепозиторий JS |
| Python | ≥ 3.12 (`requires-python` сервисов) | тесты Python-сервисов |

Обычный dev стартует без judge; тестирование при этом честно отключено. Для полного
грейд-теста используйте отдельный judge: [environments.md](environments.md).

## 2. Поднять dev-стек

```sh
make dev-up                # compose-проект comit-dev, отдельные volumes
make dev-smoke  # health/readiness, web-приложения, границы доступа
```

Порядок старта сериализован: PostgreSQL → `candidate-migrate` → `agents-migrate`
→ `auth-migrate` → HTTP-сервисы и workers → web. Отдельно запускать миграции не нужно.

Откройте http://localhost:8088:

| Путь | Что |
|---|---|
| `/` | витрина и вход |
| `/candidate/` | кабинет кандидата |
| `/employer/` | кабинет работодателя |
| `/testing/` | адаптивный тест |
| `/support/` | консоль поддержки (если заданы `SUPPORT_EMAIL`/`SUPPORT_ACCESS_KEY`) |

Вход — любой email; в dev одноразовый код автоматически подставляется на форму
(`DEV_AUTH_EXPOSE_CODE=true`), SMTP не нужен. Мок ФСП включён, поэтому демо-привязку
ФСП можно пройти целиком. Остановить стек с сохранением данных — `make dev-down`,
логи — `make dev-logs`.

## 3. Первый сценарий руками

1. Войдите кандидатом на `/candidate/`, заполните резюме, отметьте согласие на
   обработку и показ в подборе — профиль уйдёт в пул подбора.
2. В другом браузере (или профиле браузера) войдите работодателем на `/employer/`,
   создайте вакансию формой или через AI-агента и опубликуйте её.
3. Дождитесь готовности подборки, откройте ленту, поставьте лайк и пригласите
   кандидата с вилкой зарплаты.
4. Вернитесь в кабинет кандидата: примите приглашение — откроется чат процесса.

Подробная последовательность с маршрутами API — [user-flows.md](user-flows.md),
демо-данные для наполнения — [demo-data.md](demo-data.md).

## 4. Разработка без Docker

```sh
pnpm install
pnpm --filter @comit/api auth:migrate      # нужен DATABASE_URL
pnpm --filter @comit/api dev               # http://127.0.0.1:8080
pnpm --filter @comit/web-candidate dev     # :5173, /api → 8080
pnpm --filter @comit/web-employer dev      # :5174
pnpm --filter @comit/web-testing dev       # :5175
pnpm --filter @comit/web-welcome dev       # :5176
pnpm --filter @comit/web-support dev       # :5177
```

Python-сервисы запускаются из своих каталогов с `PYTHONPATH=src`; переменные — в
[services.md](services.md).

## 5. Проверки перед PR

```sh
pnpm typecheck && pnpm build               # все JS-пакеты
pnpm --filter @comit/api test              # тесты api-core
make regression                            # Python-регрессия (PostgreSQL-тесты — с PIPELINE_TEST_DATABASE_URL)
make compose-check                         # согласованность compose-режимов и секретов
make dev-smoke                  # сквозной smoke на поднятом стеке
```

CI повторяет эти шаги и проверяет пороги покрытия ([ci.md](ci.md)).
`make regression-golden-update` обновляет golden-снимок скоринга только после
намеренного изменения весов — объясните diff в коммите.

## 6. Правила репозитория

- Рабочая ветка — `dev`; `main` обновляется через PR и запускает CD ([deploy.md](deploy.md)).
- Коммиты и тексты PR — на английском, в стиле Conventional Commits; документация
  (`README.md`, `docs/**`) — на русском.
- Секреты не коммитятся: production-значения живут только в приватном `.env`
  ([environments.md](environments.md)).

## 7. Куда смотреть дальше

- Карта сервисов — [services.md](services.md), диаграммы — [architecture-diagrams.md](architecture-diagrams.md).
- Пользовательские сценарии — [user-flows.md](user-flows.md).
- Все маршруты API — [api-reference.md](api-reference.md).
- Термины — [glossary.md](glossary.md), частые вопросы — [faq.md](faq.md).
