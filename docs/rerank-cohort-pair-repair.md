# Ограниченная гибридная ревизия парных правил R₀

`matching_engine.rerank_cohort_pair_repair` создаёт отдельный namespace
`rerank-grounded-pair-repair-v3` для завершённой `rerank-grounded-cohort-v9`.
Исходные 60 источников, 420 профилей, raw receipts, признаки, main scores и
независимые grades сохраняются. Генерация и grade API запрещены. Это явная
ревизия синтетических правил сравнения; она не даёт права на активацию.

Все исходные 16 class-only семейств (01/train и 05/validation по восьми
направлениям) получают `class-only-policy-v1` и вычисляются чистым
[class-only oracle](rerank-class-only-oracle.md). Их пары и receipts помечены
`rule_derived_class_only`, `oracle_version`, policy/input/source hashes,
фактическим `oracle_code_hash` и false для human evaluation. Локальные 21 пары
и три обратных сравнения не создают API cache/jobs и не расходуют ledger.
Локальный proof отличается от LLM формата: decisive grade имеет одну own
цитату; production/duration — три. Essential unknown получает per-side
`absence_evidence` с точным path и state absent/null/excluded_prose; primitive
цитаты могут быть пустыми. Present rejected измерение не считается missing
для essential gate; последующий optional own-fact step его пропускает.
Oracle запрещён во всех 12 golden и в calibration. Формальный grade не зависит
от родительского статуса; nullable собственные факты сравниваются только когда
известны у обеих сторон и nonrejected. Essential unknown проверяется первым.

Nonclass policy и её hash сохраняются дословно. Qwen `qwen3.8-27b` получает
`rerank-uniform-cohort-pair-v5`: явно запрошенные coverage/declared fit/depth
предшествуют лишь явно вторичному confirmation. Собственный статус навыка
не заменяется статусом профиля. Stacks означает только vacancy.stacks,
domains — только vacancy.domens; generic stated requirements включает все
три группы. Пустой неэксклюзивный group пропускается без подстановки другого.
Если применимых различий нет, tie; отсутствие явно обязательного exclusive
измерения у любой стороны требует insufficient первым шагом. Весов, уровней
и технических требований учитель не придумывает.

Response schema содержит четыре **полных объекта** anyOf: A/B допускают только
reason `prefer_known_facts`, tie — `comparable_fit`, insufficient —
`insufficient_source_facts`. Это ограничение протокола не выбирает победителя.
Сохраняются семь wire fields, точные собственные статусы и 2–4 собственных
типизированных цитаты. Raw labels не нормализуются и не заменяются автоматически.

## Фиксация пилота, состава и бюджета

До HTTP закрепляются четыре исходных declared fixtures v1 и восемь ориентаций.
Ожидание A вручную выведено из raw level5 > level4 и explicit declared-fit/depth
policy; оно не получено от учителя или oracle и не передаётся в request.
Проход требует 8/8 валидных, 8/8 ожидаемых меток и 4/4 reverse comparisons.
Class-only в LLM пилоте отсутствует; ветви его правила проверяются отдельно.

До первого family HTTP после sealed base фиксируются все 16 class-only и
объединение **всех** quarantined nonclass с **всеми** nonclass семействами,
номинированными расхождением исходного учителя с original blind20 review.
Проверяются точный canonical packet hash, 20 unique IDs и полный review.
Manual labels служат только номинацией целого семейства и не входят в prompt.
Лимит объединения — девять nonclass: превышение останавливает проход без
усечения. В manifest закрепляются причины, source/base/policy hashes,
`policy_scope`, `label_source` и оригинальные packet/review hashes.

Глобальные ceilings остаются generation120 / pair3000 / grade600. V3 проверяет
обе неизменные предыдущие попытки: v1 24 attempts, checkpoint `7585ee7`, и
v2 28 attempts, checkpoint `6d41798`; все 52 raw receipts и closure pins
сохраняются. Неизвестный prior namespace, изменённый receipt или usage блокирует
запуск. Новый максимум **264**: pilot8 initial+8 retry, девять nonclass×24=216
family initial и32 общих family retry. План 2268+52+264=2584≤3000.
Перед каждым HTTP actual ledger резервирует оставшиеся обычные base calls и
оставшийся repair allowance. Сброса ledger и автоматического увеличения cap нет.

Remote pilot и nonclass21+3 используют bounded executor с пятью threads,
общий cache, shared five slots и lazy credential read. Короткий `.budget.lock`
защищает reserve/started receipt; per-job locks сохраняют идемпотентность.
Каждому job разрешён лишь attempt0 и один отдельный attempt1. Started без raw
receipt не пересылается неоднозначно; auth/endpoint/schema HTTP ошибки
останавливают проход. Семейство принимается только при всех24 валидных ответах;
при сбое прежние пары сохраняются целиком и остаётся quarantine.

## Артефакты, проверка и запуск

Каталог v3 содержит pilot manifest/report, closure observation, selection
manifest, remote jobs/budget, `oracle-receipts/`, admitted families, полный
derived dataset и repair report. Source hash oracle закреплён отдельно от
основного module hash и перепроверяется при replay. `source_metrics` различает
16 rule-derived families/48 reverse proofs и44 teacher families/132 comparisons;
`human_review_verified=false`. Original independent grade policy отдельно
отмечена: NDCG по grades и смешанный synthetic pair prior имеют разные цели.

Quality требует **≥119/132 teacher reverse comparisons**, каждое nonclass
семейство≥2/3, отсутствие quarantine, отдельно48/48 oracle proofs и минимум
шесть decisive pairs **в каждом из 12 nonclass golden семейств**. A/B, tie и
insufficient включены в reverse denominator. Teacher/oracle gates объединяются
логическим conjunction, а не выдаются за180 LLM либо human comparisons.
Попарный пропуск optional сигналов может создавать циклы, даже у чистого
oracle: production true/duration0 > false/12 > unknown/6 > true/0.
Policy сохраняется; `cyclic_components` и точный `scalar_order_ceiling`
публикуются для каждого admitted family. Reverse quality не утверждает
транзитивность или100% воспроизводимость меток линейной моделью.
Macro/golden и policy scope reports публикуются отдельно. После каждого
selected family CLI печатает JSON progress с swaps/decisive/quarantine/admission.

`validate_pair_repair(artifact_root, cohort_id, derived_dataset_path,
repair_report_path)` возвращает verified dataset/report cache-only: воспроизводит
восемь pilot answers, nonclass raw jobs и чистые local receipts; проверяет
base/source/code/policy/ledger/quality pins и неизменность grades/features.
`verify_repair_dataset(repair_output_dir)` возвращает только dataset.
Выполнение не обучает, не прогнозирует golden и не активирует prior.

Сначала пилот, только после sealed base dataset/report. Подготовка manifest и
blind packets может предшествовать closure, но новый v3 HTTP до неё запрещён:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_pair_repair --directory tests/artifacts/reranker --pilot-only
```

После sealed base и успешного пилота:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_pair_repair --directory tests/artifacts/reranker --original-review-file tests/artifacts/reranker/rerank-grounded-cohort-v9/reviews/assistant-review-canonical.json
```

Actual original review ошибочно использовал SHA256 файла вместо canonical seal.
`assistant-review-canonical.json` исправляет только hash metadata, сохраняя все20
решений; исходный файл и `review-metadata-repair-receipt.json` сохранены. Путь
не подменяется автоматически. Новые `reviews/{assistant,user}-packet.{json,md}`
сохраняют прежние20 IDs/raw/source hashes и меняют только class-only policy,
добавляя base_packet_hash, pair_repair_pilot_manifest_hash и policy_revision.
В пакетах нет labels/main/features/predictions. Нужны новые reviews;
старое adjudication автоматически не переносится. Disagreements отдельно
от reverse quality; assistant review не является независимым human review.

## Фактические предыдущие пилоты

V1:16/20 valid иcorrect,8/10 reverse,24 attempts. Все ответы двух проблемных
fixtures завершились stop с корректными model/status/citations, но class-2
не пропустил rejected/missing дополнительные факты, essential-unknown
подменил обязательное неизвестное измерение formal grade. Family repair не шёл.
V2:12/20 valid,11/20 correct,4/10 reverse,28 attempts. Основная ошибка —
несогласованные label/reason поля; class-only и essential-first также остались
семантически ненадёжными. Ни одна попытка не меняла исходные v9 family labels.
V3 сохраняет эти raw artifacts и Git checkpoints, структурно связывает
label/reason для nonclass и явно отделяет rule-derived class-only источник.

Closure receipt фиксирует terminal pilot jobs, raw hashes,
`job_receipts_hash=digest({filename:parsed_job})`, distinct reservations,
`ledger_after_pilot` и наблюдение состояния base. Это локальная фиксация counter,
а не удалённая аттестация времени. Снимок `base_dataset_existed=true` не заявляет,
что пилот завершился до base closure.
