# Чистый oracle для синтетических class-only пар

`matching_engine.rerank_class_only_oracle` реализует
`class-only-pair-oracle-v1`. Это явный источник синтетических меток
`rule_derived_class_only`, а не ответ учителя, независимая человеческая
оценка или решение о найме. Модуль не читает файлы, ключи, API и ledger.

`CLASS_ONLY_POLICY` задаёт точный контракт `class-only-policy-v1`.
`class_only_decision(A, B, policy)` возвращает метку, решающий критерий,
типизированное доказательство и поля для цитирования. `class_only_label`
возвращает только метку. `class_only_answer(vacancy, A, B, policy)` добавляет
точные собственные цитаты, исходные статусы, арифметическую копию сравнения
зарплат и provenance. Вакансия должна иметь пустые domens/stacks/soft_skills.

Порядок вычисления фиксирован:

1. Если явно эксклюзивное обязательное измерение отсутствует/null у любой
   стороны, результат insufficient до сравнения любых других признаков.
2. Формальный принятый grade: senior > middle > junior > intern. Статус
   родительского профиля, включая rejected, не отменяет результат теста.
3. При равном grade production сравнивается лишь при двух известных фактах
   с собственными статусами, отличными от rejected: true > false. Затем
   сравнивается собственное подтверждение этого факта:
   confirmed > partial > self_reported > pending.
4. Длительность сравнивается лишь при двух известных nonrejected фактах;
   большее количество месяцев предпочтительнее. Подтверждение duration
   отдельно не ранжируется.
5. Неприменимые неэксклюзивные шаги пропускаются; отсутствие применимого
   различия даёт tie. False и ноль — известные значения, не пропуски.

Policy проверяется целиком: совпадают ID, version, ordered priorities,
инструкция и набор полей. Менять разрешено только explicit exclusive metadata
`exclusive_criterion` и `required_measurements`. Подмена приоритетов под тем же
ID отвергается. Значения профиля проходят существующий строгий validator;
unknown/null/status не исправляются и не выводятся из prose.

`decision_criterion` и `proof` создаёт та же чистая функция, которая выбирает
метку. Локальный proof-протокол отделён от LLM wire schema: решающий grade
имеет одну собственную grade-цитату; production/duration имеют три реальные
цитаты (grade, собственное значение, собственный статус). При essential unknown
primitive evidence может быть пустым: `absence_evidence` содержит отдельно
для A/B точные `{path, state}` с state absent/null/excluded_prose, проверенные
по полному input. Witness не приписывает отсутствующему полю значение.
Проверка required measurements определяет только наличие: известный факт со
статусом rejected остаётся present для essential gate; собственный fact step
позже пропускает его как rejected. Gate не заявляет подтверждённость измерения. Цитаты указывают на фактически решающий собственный факт: duration после
равного production цитирует duration, а не production. Родительский статус
копируется отдельно и не используется как доказательство предпочтения.
`policy_hash` и `input_hash` связывают ответ с полными исходными данными;
`human_authorship_verified` и `independent_human_evaluation` всегда false.

В гибридном [pair repair](rerank-cohort-pair-repair.md) разрешены только исходные
16 class-only семейств: family01/train и family05/validation по восьми
направлениям. В 12 golden и calibration oracle запрещён. Фактический hash
исходного oracle дополнительно закрепляется в pilot manifest, dataset,
report и каждом локальном receipt. 48 обратных сравнений — доказательства
симметрии правила, отдельно от 132 сравнений LLM-учителя и human agreement.

Тесты содержат вручную заданные ожидаемые ветви, все пять собственных статусов,
null/false/zero, parent-status invariance, swaps, essential-first и защиту от
поддельной policy. Они не получают ожидаемые метки от учителя или oracle.

Попарный пропуск optional шагов может создавать циклы. При равном grade:
A=(production true, duration0), B=(false,12), C=(unknown,6), все известные
статусы confirmed. A>B по production; B>C и C>A по duration, потому что
production для C неприменим. Policy не изменяется ради транзитивности.
Operator публикует существующие `cyclic_components` и `scalar_order_ceiling`
для каждого принятого семейства. В этом примере любая scalar ordering
удовлетворяет максимум двум из трёх decisive пар: ceiling2/3, не100%.
48/48 reverse proofs доказывают симметрию swaps, а не отсутствие циклов.
