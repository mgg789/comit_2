# Восстановление вызовов pair teacher

API вызовы восстановлены после HTTP-ошибок предыдущего сравнения.
Один запрос с исходной v3 wire-схемой воспроизвёл HTTP400; безопасная
классификация ответа указала JSON-схему и `uniqueItems`. В opt-in wire v4
убран этот атрибут схемы, локальная проверка уникальности сохранена.
После исправления все четыре запроса Gemma/Qwen завершились и прошли
строгую проверку обеих сторон и зарплатной арифметики.
[Исходные receipts, usage и разбор](../data/validation/rerank-transport-recovery.json).

`classify_http_error` читает не более32KiB ответа, закрывает HTTPError и
сохраняет только фиксированные коды классификации/allowlisted keywords.
Тело, headers, URL, ключи и текст исключения не пишутся в кэш. Keyword mentions
сами по себе не доказывают причины; исходные8HTTP failures остаются без кода.
Старые prompts, request identities, ответы и failure receipts не изменены.

На frontend-05 0/1 обе модели симметричны. Gemma выбирает B/A, но придумывает
приоритет технической силы над экономией/командностью. Qwen выбирает
insufficient/insufficient, однако свободная rationale обеих ориентаций называет
self_reported опыт подтверждённым. Поэтому фактический quality gate не пройден.
Следующая коррекция убирает свободную речь из учебного ответа; источники
подтверждения должны сохраняться как точные факты, объяснение строится кодом.

Это этап восстановления транспорта, не общий тест качества учителя.
Выполнены1reproduction+4wire calls, без retry. Actual wire usage4586input+
714output=5300tokens; reproduction HTTP400 не вернул usage. На конце этого
этапа ledger28/696/119, старый diagnostic allowance20 полностью использован
и закрыт. Последующие bounded diagnostics используют общий прежний pair
потолок3000; не увеличивают ни один глобальный лимит и не переписывают старые20.
Полный synthetic cohort/default teacher, обучение, активация и production
на этом этапе не менялись.

```sh
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_transport_recovery
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_transport_recovery --recover
```

Первый режим воспроизводит один исходный запрос, второй проверяет переносимую
схему, максимум4calls. Завершённые результаты читаются без HTTP/key reads и
изменения ledger. Прерывание оставляет started receipt, не запускает implicit
retry. Тампер источников, mapping или raw receipts отвергается.
Public API/shared/backend модели не меняются; все source profiles синтетические.

Проверки всего текущего рабочего дерева:606Python/PostgreSQL tests passed;
scoped Ruff, `pnpm typecheck`, `pnpm build`, smoke12 passed.
API `pnpm --filter @comit/api test`:27passed/4skipped из-за ненастроенных отдельных
auth/profile test DB. Smoke запускался с собственными кратковременными app
factories и built API; существующие web/PG контейнеры сохранены. Production,
удалённый CI и полноценная learned R₀ acceptance не выполнялись.
