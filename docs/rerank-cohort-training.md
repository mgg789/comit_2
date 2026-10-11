# Изолированное test-only обучение sealed cohort

Модуль `matching_engine.rerank_cohort_training` выполняет один исследовательский цикл обучения над полностью завершённым корпусом. Он сохраняет результат в `<cohort>/test-only-training/`, не изменяет API, production-модель, исходные family/dataset-файлы или provider cache и не разрешает активацию.

## Условия запуска

Адаптер требует все 60 sealed families и 420 профилей с исходным распределением `32 train / 8 validation / 8 calibration / 12 golden`. Он проверяет manifest, source ancestry, raw generation receipts, family/features/dataset/report hashes, текущие hashes замороженного кода и seed-профиля, глобальный пул каждого направления, бюджеты генерации/пар/suitability и лимит 600 сгенерированных либо вручную производных вариантов.

Все terminal base job reservations точно совпадают с attempt budget. Проверенные cache-only pilot reservations v5 (20 pair, 10 grade) вычитаются из charged calls. Для исторических pilots v1/v2 проверяются frozen manifest, 20 initial requests и bounded retries, endpoint/model/prompt/nonce, неизменность исходных фактов/policy в retry, raw response hashes, terminal jobs, budget и closure observation. Код закреплён на checkpoints `7585ee7` / `6d41798`, наблюдения — на точных observation hashes. Итоговая разность глобального ledger точно равна charged base calls плюс проверенным 24+28 pair reservations; неизвестный расход, started job или преждевременные family calls блокируют fit. Closure закрепляет наблюдение локального assistant-оператора и не объявляется удалённым подтверждением времени. Оба неуспешных исторических пилота учитываются только в бюджете, их labels не допускаются в обучение.

Все HTTP reservations v3 происходят после sealed base closure. Его дополнительные charges точно равны всем remote jobs: 8 initial pilot requests, максимум 8 pilot retries, максимум 216 initial nonclass family requests и 32 family retries. Локальные oracle receipts не входят в provider jobs или глобальные calls; generation/grade counters не меняются. Перенос pilot charges v3 в base ledger запрещён.

Провал или незавершённость пилота текущей зарегистрированной версии pair-repair блокирует даже запуск над base dataset до создания train-request. Исторический failed v2 разрешён только при проверенном completed hybrid v3 repair, прошедшем собственный pilot и quality; это не обход через старые labels.

Pair и suitability ответы повторно разбираются из точных cached requests: модель, cohort, attempt, prompt, citations, статусы и арифметика зарплат должны соответствовать сохранённым ответам. HTTP, чтение ключа и Harrier-инференция в этом адаптере отсутствуют. Требуется подтверждённый pilot и пересчитанный quality gate: не меньше 162 корректных reverse audits из 180, ни одной quarantined family и не меньше шести decisive pairs в каждой golden family.

Текущие `select_candidates()` и `build_features()` заново выполняются над теми же frozen профилями и сохранёнными Harrier-векторами. Проверяются unit norm, hash каждого seed-вектора или receipt нового вектора, snapshot профиля/вакансии/candidate и точное совпадение всех feature values/masks/main scores. Encoder не вызывается, недостающий или изменённый вектор блокирует fit.

Далее требуется завершённый score-blind assistant-review на ровно 20 заранее выбранных парах и неизменяемый comparison report, связанный с тем же dataset. Pending user20 не препятствует этому исследовательскому циклу. Assistant-review не является независимой человеческой оценкой.

## Разбор расхождений

Любое расхождение assistant/teacher или factual issue блокирует обучение, пока не появился отдельный sealed adjudication. Адаптер ничего не выбирает автоматически и не считает blanket approval разрешением исправить labels.

Adjudication содержит `dataset_hash`, `packet_hash`, `review_hash`, `labels_changed: false`, `resolutions` и `adjudication_hash`. Последний равен `digest()` объекта без собственного hash-поля. Каждая resolution имеет ровно поля:

- `pair_id` из проблемных пар текущего blind packet;
- точные `teacher_label` и `assistant_label`;
- `outcome`: `teacher_label_confirmed` либо `assistant_label_confirmed`;
- `factual_issue_resolved: true`;
- `reason`: отдельное обоснование конкретной пары длиной 20–2000 символов.

Все проблемные пары должны быть разобраны ровно один раз. Повторение одного обоснования для нескольких пар не принимается. Проверка формы и seals не доказывает правильность человеческого или assistant-суждения: факты и порядок критериев проверяет автор adjudication.

При `assistant_label_confirmed` изменяется только соответствующая pair label в производном `TrainingFamily`. Исходные teacher answers, source/family/dataset-файлы и независимо размеченные suitability grades сохраняются. `corrections.json` фиксирует исходные и производные labels/hashes, оба суждения, outcome и обоснования. Лимит — 20 заранее выбранных reviewed pairs; произвольные и новые пары недопустимы. Golden-исправления остаются привязанными к blind packet, подготовленному до predictions; golden metrics не выбирают C.

Исходный teacher quality и производные исправления показаны отдельно. Старые reverse audits не объявляются проверкой consistency исправленных pair labels.

## Обучение и результаты

Используется существующий `rerank_training.train()` с `require_protocol: false`, `tuning_cycles: 0`, seed 42 и фиксированным `C_grid=[0.01, 0.1, 1, 10]`. Это один цикл с четырьмя заранее установленными вариантами регуляризации. Средние, масштабы и imputation вычисляются только по train; C выбирается по validation. Adjudication не меняет C-grid или признаки.

Артефакт сохраняет настоящие coefficients, scaling, validation/golden metrics, bootstrap intervals и tail calibration существующего trainer. Дополнительный calibration report вычисляется через portable artifact с уже обученной нормализацией. Фактический `gate.protocol_valid` не подменяется: он может быть `true` при корректной source allocation. При этом `training.test_only: true`, `gate.passed: false`, `activation_allowed: false` и `promotion_eligible() == false` сохраняют изоляцию исследовательского результата.

Запуск из корня репозитория, после окончания bulk labels, quality gate и ручного разбора всех assistant20 расхождений:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_training \
  --directory tests/artifacts/reranker \
  --cohort-id rerank-grounded-cohort-v9 \
  --assistant-review-file tests/artifacts/reranker/rerank-grounded-cohort-v9/reviews/assistant-review.json \
  --adjudication-file tests/artifacts/reranker/rerank-grounded-cohort-v9/reviews/adjudication.json
```

Последний аргумент можно опустить только при отсутствии расхождений и factual issues.

## Производный pair-repair dataset

Опция `--derived-dataset-file` выбирает только `dataset.json` зарегистрированного `rerank-grounded-pair-repair-v3` или его ограниченного продолжения `rerank-grounded-pair-repair-v4`. Произвольный путь, устаревший namespace или неподтверждённая подмена labels отклоняются. Public `validate_pair_repair()` проверяет без HTTP preregistered pilot, целые 21+3 receipts каждой выбранной семьи, manifest/report/operator/oracle code hashes, fixed selection, budgets, ledger и quality. Он повторно вычисляет локальные rules; training adapter не реализует второй comparator. Затем адаптер проверяет **все исходные** v9 sources, features, generation/pair/suitability receipts и пересчитывает исходный quality. Исходный failed quality разрешён только при проверенном completed repair с новым `quality.passed: true`. Исходная оценка остаётся в `raw_teacher_quality`, hybrid conjunction — в `effective_label_quality`; compatibility-поле `effective_teacher_quality` содержит тот же conjunction и явно отмечено `effective_quality_has_mixed_label_sources: true`.

V3 фактически остановился на qa-06: обе попытки одной пары содержали повторные цитаты. Все 78 remote reservations сохранены: 8 pilot + 69 family initial + 1 retry; ledger после него 99/2398/579. V4 переразмечает только это целое семейство после нового ограниченного пилота, без генерации и suitability API. Parent dataset/report/code/raw jobs и receipts проверяются отдельно; старый live ledger не подменяется. Адаптер требует точную цепочку base2320 + parent78 + все новые v4 charges, включая failures/retries, с неизменными generation/grade counters. Локальные 384 oracle receipts читаются из исходного v3 namespace и не считаются новым расходом или новой разметкой.

Пилот v4 использует исходные QA-пары, поэтому семь его точных request identities повторяются в полном 21+3. Логический attempt budget считает оба job, глобальный HTTP ledger — один distinct request hash: без retries это 32 jobs и 25 charges, а не двойное списание cache hits. Публичный validator воспроизводит каждый job из raw request/response; адаптер сверяет бюджет с terminal jobs, raw-job hash, distinct requests и отдельным числом cached reservations. Неизвестная попытка или необоснованный вычет блокируют fit.

V3 сохраняет 44 nonclass families с LLM labels и 16 class-only families с `label_source: rule_derived_class_only`: ровно 8 train и 8 validation, ни одной calibration/golden. Quality отдельно требует не меньше 119 корректных teacher reverse comparisons из 132, минимум 2/3 на каждой семье, все 48 локальных rule reverse proofs и минимум шесть decisive pairs во всех 12 golden families. Локальные 48 proofs не называются LLM или human agreement; все 384 local pair receipts и oracle code hash входят в fit/replay identity. `label_provenance` и `label_source_metrics` показывают источники и fixed-model метрики каждого split без дополнительного tuning.

**Все восемь validation families используют class-only rule-derived labels.** Поэтому C выбран на синтетическом class-only prior и не подтверждает независимое качество nonclass LLM предпочтений. Это структурное ограничение всего R0 test-only fit, а не основание изменить split или подобрать C по golden. Все 12 golden families имеют nonclass LLM labels; человеческая независимость не заявляется.

Features, raw traits, source/family manifests, vacancy, profile order и независимо размеченные suitability grades совпадают с v9. В effective `TrainingFamily` берутся только pair labels проверенного repair. Employer pair-policy revision хранится отдельно от неизменившейся runtime scoring policy. Manifest/pilot/report/policy/code/ledger hashes repair входят в identity одного fit и его replay.

Для 16 class-only семей pair policy изменена, а suitability grades сохранены по исходной v2 policy: повторной независимой разметки пригодности нет. `policy_evaluation` явно фиксирует hashes обеих policy для каждой семьи и список расхождений. Поэтому NDCG измеряет ранжирование исходной independently labeled suitability, а pair accuracy — предпочтения effective pair policy с отдельно записанными manual corrections. Эти метрики не объявляются оценками одного и того же критерия. `policy_scope_metrics` отдельно показывает `class_only_revision` и `original_nonclass` на validation/calibration/golden через существующий `evaluate()` и уже обученный fixed artifact; новый fit или tuning для этих срезов не выполняются.

Для этого пути обязателен assistant20 review с hash текущего policy-corrected packet из `<repair>/reviews`. Сохраняются прежние 20 selections, pair IDs и raw facts; class-only policy заменяется явно, а packet содержит `base_packet_hash`, `pair_repair_pilot_manifest_hash` и `policy_revision`. Старый manual или comparison с v9 packet/dataset hash не принимается. Nomination предыдущего blind review выбирает семьи для повторной разметки и не считается оценкой нового учителя.

Если между известными v1/v2/v3 и текущей версией изменился только pilot receipt hash, а все 20 cases, policies, источники и решения строго одинаковы, `review-metadata-rebind-receipt.json` может закрепить перенос прежних решений. Адаптер находит ровно один известный historical packet по точному `old_packet_hash`, проверяет старый и новый packet/manual, оба pilot hashes, равенство cases/labels, source/policy hashes и `new_blind_review: false`. Это metadata rebind, не новые 20 решений и не человеческая проверка; его receipt входит в comparison и train identity. Любое изменение фактов, policy, reviewer или решения запрещает такой перенос.

После завершения derived dataset создайте comparison отдельным cache-only вызовом с `--compare-only`. В обоих вызовах `<repair>` — конкретный текущий namespace `rerank_cohort_pair_repair.VERSION`:

```bash
PYTHONPATH=src .venv/bin/python -m matching_engine.rerank_cohort_training \
  --directory ../../tests/artifacts/reranker \
  --derived-dataset-file ../../tests/artifacts/reranker/<repair>/dataset.json \
  --assistant-review-file ../../tests/artifacts/reranker/<repair>/reviews/assistant-review.json \
  --compare-only
```

Этот helper экспортирован как `compare_test_only_review(directory, assistant_review_file, cohort_id, derived_dataset_file=...)`. Он сохраняет сравнение с текущими labels, не выбирает outcome и не исправляет данные. После явного per-pair adjudication запускается тот же CLI без `--compare-only`, с `--adjudication-file`, если есть расхождения/factual issues. Adjudication закреплён на **derived** dataset и новом manual/packet; лимит manual corrections остаётся 20. Старые reverse audits не объявляются проверкой исправленных вручную labels.

Артефакт, corrections, train-request и completed report этого цикла сохраняются в `<repair>/test-only-training/`, отдельно от base v9. Оба raw datasets остаются неизменными. Протокол, метрики, fixed C-grid и отсутствие activation имеют те же ограничения, что и основной путь.

## Защита от повторного fit

Фактический v4 цикл завершён с C=0.01, 756 features; golden activation gate пока не пройден — это цель следующего цикла.
Повторный cache-only replay проверен без fit и без изменения training receipts.
[Метрики и область применения](rerank-grounded-cohort-acceptance.md).

До обучения под файловой блокировкой атомарно создаётся неизменяемый `train-request.json` со статусом `started` и полными hashes данных, review, adjudication, frozen code и самого adapter/trainer. После успешного цикла сохраняются `artifact.json`, `report.json` и завершающий `completed.json`.

Повторный запуск при целых completed receipts повторно проверяет источники и возвращает прежний report без fit. `started` без `completed` всегда означает обязательный ручной разбор сбоя; адаптер не повторяет обучение автоматически. Подмена данных, receipt, модели или кода также блокирует replay. Исходный immutable dataset продолжает хранить свой дообучающий `training_started: false`; реальный факт исследовательского цикла записан отдельно в training receipts.

Проверки модуля из `services/matching-engine`:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_rerank_cohort_training.py -q
PYTHONPATH=src .venv/bin/python -m ruff check src/matching_engine/rerank_cohort_training.py tests/test_rerank_cohort_training.py
```

См. также [корпус](rerank-grounded-cohort.md) и [score-blind review](rerank-cohort-review.md).
