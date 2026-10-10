# Ограниченное продолжение qa06 поверх неизменного v3

`matching_engine.rerank_cohort_pair_completion` создаёт новый namespace
`rerank-grounded-pair-repair-v4`. Из sealed hybrid v3 меняется только целиком
непринятое calibration семейство qa06:21 прямых пар и3 обратных проверки.
Все остальные59 семейств, включая16 rule-derived class-only и12 golden,
копируются точно. Источники, профили, признаки, main scores и независимые
grades остаются исходными. Генерация, grade API, fit и активация запрещены.

Actual v3 прошёл пилот8/8, но qa06 pair3–5 оба раза вернул повторяющиеся evidence
токены. Дедупликация ответа не выполнялась. Whole-family admission не состоялся:
127/132 teacher reverse comparisons и48/48 oracle proofs оставили qa06
quarantined с исходными1/3 swaps. V3 потратил78 HTTP charges:8pilot initial,
69family initial и1retry; global ledger generation99/pair2398/grade579.
Все v3 artifacts и frozen source сохраняются неизменными.

Перед v4 выполняется публичный cache-only v3 replay через временное локальное
представление исторического ledger: base/v1/v2/v3 и cache entries — symlinks,
только usage.json содержит копию sealed v3 ledger. Исходный ledger не изменяется.
Дополнительно воспроизводятся точные request identity, nonce, raw response и
валидность **всех78 jobs**, включая failed. Passed/failed состояние должно
совпадать с фактическим строгим validator. Неизвестная дополнительная попытка,
подмена source/code/receipt или другие quarantine блокируют продолжение.

## Протокол и предварительная фиксация

Исходная `global-baseline-prior-3` policy сохраняется дословно: primary declared
fit к stated stacks, затем собственное confirmation этих matching skills.
Здесь единственный stated stack — python4. Больший заявленный уровень
предшествует confirmation; при равных уровнях
confirmed > partial > self_reported > pending; при равенстве обоих — tie.
Accepted profiles имеют известные nonrejected python claims. Grade, root status,
project refs, domains, soft skills, salary, duration, role и biography не заменяют
этот критерий. Полные raw профили передаются как provenance/context.

Prompt `rerank-policy-scoped-pair-v1` действует одинаково для initial и одного
repair. Evidence каждой стороны задаётся whole-array enum:
`[[own python level token, own python status token]]`. Только этот массив
допустим, ровно в указанном порядке; дубли/лишние refs структурно невозможны.
Четыре полных anyOf object branches отдельно связывают label и reason:
A/B→prefer_known_facts, tie→comparable_fit,
insufficient→insufficient_source_facts. Root statuses копируются отдельно.
Raw responses не переписываются, не дедуплицируются и не нормализуются.

До любого HTTP фиксируются толькоqa06, parent/source/policy/code/raw hashes,
четыре raw cases и8 ориентаций:0–1tie,1–3tie (production не решает),3–5A,
2–6B. Ожидаемые метки вручную выведены из python facts и исходной policy,
не получены от прежнего учителя, human review либо модельных scores и не входят
в request. Пилот требует8valid/8expected/4reverse. Failure не допускает family
calls; семейство принимается только целиком при всех24 валидных ответах.

## Бюджет, артефакты и проверка

Cap64logical reservations:8pilot initial+8retry+24family initial+24retry.
Shared global limits120/3000/600, пять remote slots, lazy key и immutable cache
сохраняются. Каждый job имеет максимум attempts0/1 с отдельной identity;
started без raw receipt не пересылается неоднозначно. Per-job locks и короткий
budget lock защищают конкурентный запуск пяти threads. Actual global usage
проверяется перед HTTP с резервом оставшегося allowance, без сброса ledger.

Raw pilot и family могут иметь одинаковые identities и переиспользовать cache.
Logical budget считается отдельно от **distinct actual HTTP charges**. Report
фиксирует actual_pair_reservations и exact cumulative delta от sealed2398;
parent_repair содержит namespace/dataset_hash/report_hash/ledger_after,
actual78/raw_jobs_hash и raw/code hashes. Ledger_before остаётся base2320,
ledger_before_pilot=parent2398. Pilot closure receipt фиксирует terminal raw
jobs, counter и завершённый base; это локальное наблюдение, не remote attestation.

V4 содержит pilot manifest/report/closure, remote jobs/budget, accepted qa06
family, полный derived dataset и repair report. Oracle receipts не копируются:
`ORACLE_RECEIPT_NAMESPACE` и `PARENT_REPAIR_NAMESPACE` указывают на v3.
Quality пересчитывается по прежним132 teacher comparisons (≥119, каждый≥2/3,
без quarantine), отдельно48oracle proofs и12golden≥6decisive pairs каждое.
Исходные циклы и scalar ordering ceiling публикуются без принудительной
транзитивности. Новая LLM не размечает class-only или другие семейства.

`validate_pair_completion(artifact_root)` возвращает verified dataset/report
cache-only. `validate_pair_repair` предоставляет тот же interface с optional
registered dataset/report paths для training adapter. Проверяются все parent
receipts, новые raw jobs, whole-family admission, неизменность59 records и
features/grades, точное accounting и все activation/training flags=false.
Quality=true само по себе не разрешает обучение или активацию.

V4 blind packets сохраняют прежние20 IDs, полные raw facts, source hashes,
class-only policy и исходный `base_packet_hash`v9. Из receipt metadata меняется
только `pair_repair_pilot_manifest_hash` на v4; packet_hash пересчитывается.
Labels/main/features/predictions в них отсутствуют. V3 reviews не переписываются.

Сначала явный запуск пилота после code/tests/inspection:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_pair_completion --directory tests/artifacts/reranker --pilot-only
```

Затем полный проход тем же CLI без `--pilot-only`. Cached replay не читает ключи
и не вызывает inference. Любой провал публикуется как quality failure;
автоматической замены меток или выбора удобного subset нет.
