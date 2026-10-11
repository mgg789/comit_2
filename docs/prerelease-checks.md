# Проверки prerelease-ветки

Ветка `fix/pre-release` остаётся отдельно от `dev` до решения владельца. Локальный стенд использует один origin `http://localhost:8088/`. Эти проверки не означают публикацию изменений на production.

## Открытые GitHub issues

На 11 октября 2026 года исправления следующих issues находятся в prerelease-ветке. Закрытие issue не заменяет слияние и проверку релиза.

| Issue | Исправление и проверка |
|---|---|
| [29](https://github.com/mgg789/comit_2/issues/29) | Анонимный поиск ограничен видимыми полями карточки, без narrative и UUID. Регрессии `test_public_showcase_integration.py`; API `public-showcase.test.ts`. |
| [32](https://github.com/mgg789/comit_2/issues/32) | Неуверенный вердикт и сбой инфраструктуры не объявляют ответ неправильным. Повторы ограничены; аварийная попытка не создаёт новый кулдаун. `test_judge_recovery.py`, `test_judge_sandbox_regression.py`. |
| [33](https://github.com/mgg789/comit_2/issues/33) | Актуальный брифинг `judge-agents.md` описывает подключённые тестирование, судью и режимы ФСП. |
| [35](https://github.com/mgg789/comit_2/issues/35) | По умолчанию локальный dev запускается без судьи; тестирование отключено. Включение реального судьи явно; подготовка pinned Harrier cache проверяет готовность. `tests/compose-config.py`, `test_disabled.py`. |
| [36](https://github.com/mgg789/comit_2/issues/36) | SSE проверяется по готовности HTTP, а не ожиданию 10 мс. `chat-sse.test.ts` проверен с реальными HTTP и PostgreSQL, 2/2 без пропусков. |
| [37](https://github.com/mgg789/comit_2/issues/37) | Ошибка публикации 409 сохраняет причину и срок; code-judge использует ограниченную параллельность без блокировки ready; SSE бюджеты раздельны; nginx заголовки совместимы, metrics закрыты, production порт loopback, OAuth callback не попадает в access log. `test_publisher.py`, `test_concurrency_and_budget.py`, API тесты и `tests/compose-config.py`. |

## Дополнительная граница работодателя

`employer-gateway-boundary.test.ts` проверяет точные allowlist для agents и matching, новые методы/пути, закрытые candidate worker endpoints, настоящую сессию и server-derived owner. Полная PostgreSQL проверка — 2/2, без пропусков.

## Проверки нового интерфейса и артефактов

- PDF: `resume-design.md`; публичные и личные данные, длинные страницы, пакетные Inter/капсула, обновление сохранённых PDF под публикационными блокировками без изменения оценок.
- Огонёк: `matching.md`; серверная награда текущей недели, отмена награды, текущая ревизия, исчезновение после срока. Профиль присоединяется одним запросом, без запроса на каждую карточку.
- Справка: `service-help.md`; публичный `/help/`, поиск, роли и реальные ссылки поддержки. Smoke проверяет доставку страницы; браузер проверяет работающий интерфейс.
- ФСП: production остаётся shadow, dev active. Ограничения перестановок проверяются отдельно от качества. Эталон v7 воспроизведён, но не содержит FSP/weekly labels и не доказывает улучшения от overlay.

PostgreSQL регрессии выполняются в отдельных тестовых базах, а не в базе пользователей. Пропуски DB/provider тестов в локальной unit-сборке не считаются проверкой этих контуров.
