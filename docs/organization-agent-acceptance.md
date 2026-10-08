# Приёмка backend глобального агента — 07.10.2026

Контур: отдельное рабочее дерево `organization-agent`, отдельная синтетическая
PostgreSQL `comit_org_agent_acceptance_test`. Основная рабочая копия, её staged
документы, production и реальные кандидатские переписки не изменялись.

## Проверено

- Полный agents-service suite с PostgreSQL: **91 passed**. Очереди/leases,
  восстановление provider/tool receipts, резервирование бюджета, CAS памяти,
  длинная история и сжатие, приватный vault, pending answer → продолжение,
  multi-process отчёты и отключение дублёра проверены тестами.
- Полный API suite с organization PG fixture: **49 passed, 18 skipped**.
  Отдельно auth/store/routes/bridge/server registration с PostgreSQL:
  **16 passed, 0 skipped**. Пропущены прочие независимые интеграционные контуры.
- `ruff check services/agents-service/src services/agents-service/tests`,
  `pnpm -r typecheck`, `pnpm -r build`, `git diff --check`: passed.
- `make smoke`: **15 checks passed**, включая agents `/ready` с реальной БД,
  пять сервисов, четыре уже существующих web shell, nginx и FSP semantics.
  Smoke не доказывает наличие frontend глобального чата.
- Финальная независимая инспекция: READY FOR COMMIT после исправления замечаний.

Команды для воспроизведения (URL должен указывать на отдельную тестовую БД):

```sh
PYTHONPATH=services/agents-service/src AGENTS_TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  python -m pytest services/agents-service/tests -q
ORGANIZATION_AGENT_TEST_DATABASE_URL="$TEST_DATABASE_URL" pnpm --filter @comit/api test
ruff check services/agents-service/src services/agents-service/tests
PYTHONPATH=services/agents-service/src python tests/generate_agents_contract.py
pnpm -r typecheck
pnpm -r build
WEB_PORT=38088 API_PORT=38080 MATCHING_PORT=38101 TESTING_PORT=38102 \
  INTEGRATION_PORT=38103 AGENTS_PORT=38104 make smoke
```

## Реальные модели на синтетических данных

DeepSeek Flash: turn через `OrganizationRuntime`, **472 input / 1 output**,
успешное завершение, в ledger **95.100000 micro-USD** ($0.0000951).

SystemOne `kev-latest`: протокол подтверждён, **93 input / 73 output**.
Затем отдельный вызов через `OrganizationJev` с синтетической вакансией/карточкой:
**215 input / 73 output**, в ledger **9.030000 micro-USD**, резерв закрыт.
Повтор того же ключа получил сохранённый результат без дополнительного списания.
Источник карточек и применение решения в этой проверке — тестовый адаптер;
реальные карточки и сообщения кандидатам не использовались. Проверки бизнес-guard
и API receipts выполнены отдельно.

Первая попытка SystemOne дала 401: операторский файл с несколькими ключами был
ошибочно прочитан целиком. Исправлен выбор единственного Sverk-labelled ключа;
state отправляется строкой JSON. Regression test покрывает оба ограничения.
Ключи не сохранены в репозитории, БД usage или отчёте.

## Границы приёмки

Backend и typed SDK готовы; frontend глобального чата не реализован в этом
проходе. В знаниях агента явно описан текущий employer shell, без выдуманных меню.
Живые переговоры с людьми, календарные конфликты работодателя между независимыми
процессами и production deployment не проверялись. Модель должна согласовать
предоставленные интервалы; полноценного внешнего календаря нет.

См. также: [контракт и запуск](agents.md), [требования](../context/45-organization-agent.md).
