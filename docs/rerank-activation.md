# Ручное включение исследовательского R₀

Оператор явно разрешил использовать текущий R₀ для сортировки основного top-k, несмотря на failed quality gate. Это отдельное решение о rollout: исходный артефакт, quality gate, `training.test_only=true` и сведения о review остаются неизменными. Обычный `RerankStore.promote()` по-прежнему требует прохождения всех gates.

Упакованный артефакт: `services/matching-engine/src/matching_engine/artifacts/r0-c18f4bc1.json`, точный canonical content hash:

```text
c18f4bc1f2926b58643f68d985e5598f6bc28ac8b8d03bfaeeab00472dcbbcc9
```

Статус решения (08.10.2026): артефакт доведён до
[mvp-prod-ready](rerank-mvp-status.md) с пересмотренным операторским gate;
обычный promote() по-прежнему требует старых численных gates.

Решение находится рядом, в `r0-c18f4bc1-manual-decision.json`. Оно фиксирует actor, reason, исходные gate/reviews, hash evaluation, отдельные hashes существующих assistant review/comparison и явное признание ограничений. Assistant review не считается независимым человеческим review. В sealed artifact остаются исходные `assistant_pairs=0`, `user_pairs=0`; отдельное evidence указывает фактически проведённые 20 assistant comparisons и ноль выявленных factual errors. Это не доказательство качества на реальных кандидатах.

## Активация и запуск

После миграции 008 оператор запускает из окружения matching-engine с заданным `DATABASE_URL`:

```bash
python -m matching_engine.rerank_activation bootstrap-packaged
```

Startup rerank-worker вызывает `bootstrap_packaged(store)` для этого же решения. Оно применяется только при `activation_revision=0` и отсутствии активного артефакта. Если оператор уже выбрал модель или выключил её, последующий startup сохраняет решение. При несовместимой текущей strict schema bootstrap отклоняется; worker сохраняет доступный main fallback.

Для явного повторного включения используется JSON receipt, подготовленный `make_receipt()` и доступный оператору для проверки перед записью:

```bash
python -m matching_engine.rerank_activation activate \
  --artifact src/matching_engine/artifacts/r0-c18f4bc1.json \
  --receipt src/matching_engine/artifacts/r0-c18f4bc1-manual-decision.json \
  --expected-artifact-hash c18f4bc1f2926b58643f68d985e5598f6bc28ac8b8d03bfaeeab00472dcbbcc9
```

Операция выполняется в одной транзакции под общей блокировкой matching: регистрирует неизменяемый artifact и receipt, меняет active pointers, увеличивает activation revision, пишет audit event и ставит в очередь все актуальные готовые main batches. Такой же artifact/receipt уже активен — операция идемпотентна. Новое решение для того же model hash создаёт новую revision; старые claim/publish и feed sessions становятся неактуальными. Публичного HTTP endpoint активации нет.

Runtime проверяет persisted DB receipt, точный artifact hash, `tail_gap=None`, protocol/label validation, отсутствие factual errors, feature ordering/hash, strict schema, main policy и pinned Harrier space. JSON, внедрённый в artifact или аргументы scorer, не выдаёт runtime authority. При отказе сохраняется полный main order с явным `fallback_reason`. Learned result сохраняет полный score и все feature contributions; strong filter и состав main top-k сохраняются. У этого rollout нет reserve/tail exclusion.

API карточек и rerank status показывают `activation_kind=manual_override`, `approval_receipt_hash` и `activation_revision`. Эти поля позволяют отличить исследовательское операторское решение от прошедшего gated promotion.

## Откат

```bash
python -m matching_engine.rerank_activation deactivate \
  --expected-artifact-hash c18f4bc1f2926b58643f68d985e5598f6bc28ac8b8d03bfaeeab00472dcbbcc9 \
  --actor operator --reason 'Откат исследовательской сортировки'
```

Откат проверяет текущий hash, сохраняет immutable deactivation receipt и audit event, очищает active pointers, увеличивает revision и пересчитывает готовые main batches через fallback. Event ссылается на deactivation receipt; settings больше не содержит active approval. Startup после отката не включает модель обратно. Для смены на другой reviewed artifact оператор подготавливает отдельное решение; прежние receipts и events сохраняются.

## Проверки

`tests/test_rerank_activation.py` проверяет сохранность sealed gate/reviews, отказ при injected dict и подмене receipt, обязательные protocol/label/factual/tail ограничения, runtime compatibility, immutable DB receipts, atomic queue, stale publish, повторное включение того же hash, feed freshness и деактивацию. PostgreSQL tests требуют отдельного `PIPELINE_TEST_DATABASE_URL`; они очищают только изолированный тестовый контур. Fit, генерация профилей, model API и production DB в этих тестах не вызываются.
