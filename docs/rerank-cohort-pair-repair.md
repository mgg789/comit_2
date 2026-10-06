# Ограниченная ревизия парных правил R₀

`matching_engine.rerank_cohort_pair_repair` создаёт отдельную версию
`rerank-grounded-pair-repair-v2` для завершённой выборки
`rerank-grounded-cohort-v9`. Это явное изменение правил сравнения, а не
автоматическое исправление прежней «истины». Старые dataset, источники,
raw receipts, признаки, main scores и независимые оценки пригодности сохраняются.
Генерация профилей и grade API этим оператором запрещены.

## Правила и заранее выбранный объём

Все 16 class-only семейств получают `class-only-policy-v1`:

1. Принятый формальный грейд: senior > middle > junior > intern. Исходный статус
   профиля, включая rejected, не аннулирует отдельный формальный результат теста.
2. При равном грейде production сравнивается только при двух известных фактах
   с собственными статусами, отличными от rejected: true > false; затем
   confirmed > partial > self_reported > pending для этого факта.
3. Длительность сравнивается только при двух известных фактах с собственными
   статусами, отличными от rejected. Большее значение предпочтительнее.
4. Неизвестный неэксклюзивный сигнал пропускается. Если применимых различий нет,
   результат tie. Insufficient допускается при явно обязательном эксклюзивном
   измерении, отсутствующем хотя бы у одной стороны.

Зарплата, biography, широта навыков и выдуманные требования не участвуют
в class-only сравнении. Арифметическое поле salary comparison остаётся
проверяемой копией исходных сумм и не определяет метку.
Для технических вакансий v4 prompt уточняет сравнение coverage и заявленной
глубины по общим явно запрошенным координатам. Confirmation используется
только как явно заданный вторичный критерий; статус родителя не подтверждает
навык или факт. Неизвестный уровень не превращается в ноль. Исходная nonclass
policy и её hash сохраняются дословно; class-only rubric её не заменяет.

После завершения sealed v9 dataset, **до первого family HTTP**, фиксируются
все 16 class-only семейства и объединение всех quarantined non-class семейств
со всеми nonclass семействами, где метка исходного учителя расходится с исходной
слепой assistant проверкой заранее выбранных 20 пар. Проверяются hash исходного
пакета, ровно 20 уникальных pair IDs и полный review. Это только номинация
целого семейства: manual labels не передаются новому учителю. Лимит объединения —
девять nonclass семейств. Если их больше, оператор публикует
`selection-blocked-report.json` и останавливается. Нельзя выбрать удобные
случаи по метрикам модели или исключить неудачные семейства из результата.
Для каждого выбранного семейства записаны исходный record hash, причина,
старый и новый policy hashes, фактическая policy, `policy_scope`, `policy_id`,
а также packet/review/source hashes исходной слепой номинации.

## Пилот и бюджеты

До HTTP замораживаются десять fixtures и 20 направленных сравнений:
пять class-only случаев, четыре преимущества заявленного уровня над
confirmation и один неизвестный обязательный exclusive signal.
Пилот проверяет tie, пропуск неизвестного неэксклюзивного факта, исходный
rejected статус без аннулирования формального грейда и собственные статусы.
Для продолжения обязательны 20/20 валидных ответов, 20/20 ожидаемых меток
и 10/10 корректных swaps. Цитаты проверяются по точным типизированным исходным
полям; class-only не может ссылаться на зарплату или несуществующие требования.

Общие ceilings остаются generation 120 / pair 3000 / grade 600. Оператор
использует только shared pair ledger, тот же cache, пять remote slots и lazy
credential read. Перед пилотом v2 проверяется план 2268 + 24 + 672 = 2964 ≤ 3000:
24 уже потраченных v1 attempts не сбрасываются. Frozen v1 pilot, все 24 raw
receipts и его code hash из Git checkpoint `7585ee7` проверяются до v2 HTTP.
Неизвестное дополнительное prior usage, новый prior namespace или изменённый
receipt блокируют запуск. V2 manifest фиксирует hashes всей предыдущей попытки.
Пилот и парные jobs каждого семейства выполняются через существующий
bounded executor с пятью threads и сохраняют исходный порядок результатов.
Короткий собственный `.budget.lock` защищает reserve и started receipt;
per-job locks и общий inference cache сохраняют идемпотентность.
Перед новым запросом дополнительно резервируется весь оставшийся обычный
pair объём v9 и оставшийся repair allowance по фактическому ledger.
Неожиданные дополнительные попытки base уменьшают headroom и могут остановить
ремонт; сброса ledger нет.

Repair максимум 672 reservations: pilot 20 initial + 20 retry,
не более 25 × 24 = 600 первых family запросов и 32 дополнительных family
ответа на весь проход. В любой job разрешены attempt0 и один attempt1
с отдельной identity. Ошибки учитываются. Started без raw receipt не отправляется
повторно автоматически. Auth/endpoint/schema HTTP ошибки прекращают проход.
При отсутствии всех 24 валидных ответов семейство целиком не принимается,
его прежние пары сохраняются, а результат остаётся quarantined.

## Запуск и результаты

Из корня репозитория, сначала только пилот:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_pair_repair --directory tests/artifacts/reranker --pilot-only
```

Пилот разрешён до окончания v9: все 60 принятых источников и уже опубликованные
base audit hashes фиксируются заранее. Для него нужны подготовленные исходные
blind review packets v9. Полный проход допустим только после sealed dataset:

```bash
PYTHONPATH=services/matching-engine/src services/matching-engine/.venv/bin/python -m matching_engine.rerank_cohort_pair_repair --directory tests/artifacts/reranker --original-review-file tests/artifacts/reranker/rerank-grounded-cohort-v9/reviews/assistant-review-canonical.json
```

В actual v9 review исходный `packet_hash` был ошибочно записан как SHA256 байтов
файла пакета. Контракт использует встроенный canonical JSON seal с исключённым
полем `packet_hash`. Файл `assistant-review-canonical.json` исправляет только
эти метаданные и сохраняет все 20 строк решений. Исходный review сохранён;
`review-metadata-repair-receipt.json` фиксирует обе схемы hash, hashes файлов и
неизменный `labels_hash`. Оператор не подменяет исходный путь автоматически.

В отдельном repair каталоге находятся `pilot-manifest.json`, `pilot-report.json`,
`closure-observation.json` с локальным наблюдением завершения pilot jobs,
`manifest.json` с полным выбранным составом, `jobs/`, `budget.json`,
принятые `families/`, `dataset.json` и `repair-report.json`.
После каждого выбранного семейства CLI печатает JSON progress с family ID,
swap count, decisive count, quarantine и whole-family admission.
Prompt пары — `rerank-uniform-cohort-pair-v4`; исходный grade v2 сохраняется.
Формируется полный derived dataset из 60 семейств. Неизбранные records
копируются точно; выбранные сохраняют исходные manifest, vacancy, traits,
source hash, признаки, main scores, lineage и grade answers. Меняются только
парные ответы, audits, связанные cycle/order diagnostics и pair provenance.
Смешанная история v4 repaired/v2 preserved пар отмечена явно.

Quality пересчитывается по **180 teacher reverse comparisons**, а не человеческому
согласию: ≥162/180, каждое семейство ≥2/3, отсутствие quarantine и минимум
шесть решающих пар **в каждом из 12 golden семейств**. Отдельно публикуются
macro reverse accuracy и golden statistics. Обучение, прогнозы на golden
и активация не выполняются.
Отдельные группы `by_policy_scope` показывают class-only revision и original
nonclass policy с IDs. Исходные blind disagreements используются для номинации;
новые manual disagreements проверяются отдельно и не входят в 180 swaps.

## Blind review и проверка для обучения

`reviews/{assistant,user}-packet.{json,md}` сохраняют исходные заранее выбранные
20 case IDs, точные профили и source hashes. Только class-only policy заменяется
новой frozen policy. Добавляются `base_packet_hash`,
`pair_repair_pilot_manifest_hash` и `policy_revision` с version/hash.
В пакетах нет меток учителя, main scores, признаков или прогнозов.
Прежние пакеты и manual reviews не перезаписываются. Для новой семантики нужна
новая проверка этих policy-corrected пакетов; прежнее adjudication не переносится.

`validate_pair_repair(artifact_root, cohort_id, derived_dataset_path,
repair_report_path)` возвращает проверенные dataset/report, воспроизводя
pilot и все принятые whole-family ответы по точным raw cache receipts без HTTP,
ключей и записи кэша. Проверяются источники, базовые audits, неизменность
features/grades, фиксированный состав, semantic policy hashes, качество,
общий ledger и собственный budget. `verify_repair_dataset(repair_output_dir)`
— сокращённый интерфейс только для dataset. Quality=false не разрешает обучение
по правилам принятия; даже quality=true не разрешает активацию автоматически.

## Фактический пилот v1

Actual v1 завершился с 16/20 валидных и правильных ответов, 8/10 корректных
reverse comparisons. Потрачены 24 pair attempts: 20 первых и четыре ограниченных
retry. Все восемь ответов двух проблемных fixtures завершились `stop`, модель,
статусы и цитаты соответствовали контракту; это семантический сбой, не HTTP,
схема или truncation.

В `class-2` модель предпочла сторону с confirmed production и известной duration.
При одинаковом formal grade обе дополнительные проверки неприменимы: production
у другой стороны rejected, duration неизвестна. Frozen policy требует tie.
В `essential-unknown` модель выбрала более высокий grade, проигнорировав отсутствие
обязательного `uptime_percentage`; контракт требует insufficient до сравнения
grade. Generic repair-инструкция о JSON-полях не исправила эти нарушения.

Machine gate не пройден. Family repair не запускался; исходные v9 пары и
независимые grades этим пилотом не менялись. Raw receipts и manifest v1 сохранены.

## Семантический контракт v2 / prompt v4

Те же десять заранее зафиксированных fixtures проходят сначала обязательный
essential gate. Если exclusive criterion требует отсутствующее или null измерение
хотя бы одной стороны, сразу insufficient; grade и любые другие признаки
не подставляются. После этого class-only шаги выполняются строго последовательно:
formal grade; production value и его собственное confirmation только при двух
известных nonrejected фактах; duration только при двух известных nonrejected
фактах; tie, если ни один применимый шаг не дал различия. Отдельного duration
confirmation tiebreak нет. Rejected исключает применимость, а не становится
нижним рангом confirmation.

Одинаковый semantic contract присутствует в initial и единственном retry prompt.
В request добавлена проверяемая таблица `criterion_applicability`: только наличие
явно обязательных измерений и применимость production/duration; целевая метка,
manual labels и scores не передаются. V1 source сохранён в Git checkpoint;
в его каталог и raw cache новый оператор не записывает.

Pilot report фиксирует `ledger_after_pilot` после завершения всех jobs.
Closure observation содержит `job_receipts_hash` как digest объекта
`{filename: parsed_job}` по `pilot-*.json`, `raw_response_hashes` по request hash,
число фактических distinct raw reservations, тот же global counter и снимок
состояния base. Проверка требует terminal receipts и монотонный counter между
началом пилота и последующим ledger. Это локальное наблюдение, не доказательство
времени удалённого ответа. При завершённом base snapshot явно показывает
`base_dataset_existed=true`; такое наблюдение не заявляет, что пилот завершился
раньше base closure. Final repair report фиксирует closure observation hash.
