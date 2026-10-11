# Данные — Comit

Миграция matching `008_real_card_learning.sql` добавляет append-only activation
receipts/events, revision активной модели и learning_pipeline cohorts/samples/events.
Признаки всех main top-k сохраняются до реакций; issued/delivered/impression/action
различаются. Отзыв согласия очищает feature payload, historical hash остаётся
аудитом. Operational export требует согласия authoritative candidate_profiles
и matching, псевдонимизирует IDs и не запускает обучение.
[Подробный контракт хранения и экспорта](real-card-learning.md).

> Статус: backend первого цикла. Auth-схема API-core уже имеет явную миграцию;
> остальные доменные таблицы появляются по своим миграциям. Доменная модель — `context/30-domain-model.md`.

## Хранилище

- **PostgreSQL 16 + pgvector** (образ `pgvector/pgvector:pg16`), одна БД, схемы по сервисам.
- Расширение `vector` включается инициализационным скриптом `infra/postgres/init/01-extensions.sql` (только на пустом volume).
- Dev публикует БД на `localhost:5433` (`POSTGRES_PORT`), prod — только внутренняя сеть.

## Синопсис домена (детали — context/30)

- `accounts` (один нормализованный email → одна неизменяемая роль candidate|employer)
  → будущие `CandidateProfile`/`Company`; account consent не является согласием на публикацию профиля
- `VerifiedClaim` + `RatingLedger` → `ratingPoints`
- `TestBlueprint` / `TestSession` / `GradeAttempt` (cooldown смены грейда)
- `Vacancy` (строгие параметры + вектор + ЗП от–до обязательна), `VacancyVector`
- `Selection` (score + объяснение + стадия: in_pool→selected→visited|attractive→invited→…)
- `Invitation` (vacancyId nullable, ЗП обязательна, статусы обеих сторон), `Feedback`

## Правила видимости (п. 2.2 ТЗ — проверяются на ревью)

1. ЗП «от–до» обязательна в вакансии и приглашении.
2. Контакты кандидата раскрываются только после принятия приглашения/отклика.
3. Статусы приглашений и откликов видят обе стороны.
4. Регистрация по email с подтверждением.

## Auth-схема API-core

`apps/api/migrations/001_accounts.sql` применяется только явной командой
`pnpm --filter @comit/api auth:migrate`. Runner берёт PostgreSQL advisory lock,
сверяет SHA-256 с таблицей `auth_migrations` и не запускается при старом checksum;
serve этот шаг не выполняет.

| Таблица | Назначение |
|---|---|
| `accounts` | email, фиксированная роль, verified-флаг и серверная версия/время account consent |
| `auth_challenges` | role-bound OTP HMAC, expiry, attempts и одноразовый статус; raw code не хранится |
| `auth_mail_outbox` | AES-GCM payload, lease/retry/status; payload очищается после ACK/expiry/exhaustion |
| `auth_issue_events` / `auth_verify_events` | глобальные email/IP rate limits; IP хранится только HMAC |
| `sessions` | hash 256-bit sid, role/account ownership, expiry и revoke timestamp |

SMTP worker получает plaintext code только в памяти после расшифровки валидного
outbox job и проверяет challenge/lease непосредственно перед SMTP. Crash между SMTP
ACK и записью delivery может повторить письмо; это at-least-once delivery, тогда как
OTP verify остаётся at-most-once. Фактическая доставка и production состояние этой
схемой не подтверждаются.

## Source profile кандидата

Миграция `apps/api/migrations/002_candidate_profiles.sql` хранит account-owned
source profile отдельно от matching projection:

| Таблица | Назначение |
|---|---|
| `candidate_profiles` | editable fields, CAS revision, server-owned profile consent и updated_at |
| `candidate_profile_evidence` | отдельные proof rows: stable evidence key, canonical input hash, status, verified value, provenance, checked_at |
| `candidate_profile_outbox` | полный exact source snapshot, source revision, lease/retry/ACK state и безопасный error code |

Нарративные claim ID генерируются сервером и сохраняются между PATCH. Facts
(domains, stacks, duration, role, production support) входят в canonical JSON
отдельно от narrative text. SHA-256 считается после сортировки ключей и без
`undefined`; ссылки не переводят claim в confirmed автоматически.

`expected_revision` защищает PUT/PATCH compare-and-swap; revision zero создаёт
первый snapshot. Profile consent версии `profile-v1` и server timestamp пишутся
в одной транзакции с source profile. Account consent не даёт pool consent, а
клиент не может прислать `accepted_at`, evidence или `verified_value`.

Outbox worker передаёт ровно сохранённый snapshot во внутренний matching API и
принимает ACK только для того же candidate/revision. Lease fencing и bounded retry
защищают от старого worker; одна неисправная projection не блокирует другие
профили. ACK означает доставку source snapshot, но не completion строгого и
semantic vectors, PDF или pool-ready.

## Миграции

- api-core: явный Node/pg runner с checksum/advisory lock; файлы в `apps/api/migrations/`.
- matching-engine: `python -m matching_engine migrate` (advisory lock, checksum в `candidate_pipeline.migrations`); SQL 001–006 в схемах `candidate_pipeline`, `vacancy_pipeline`, `matching_pipeline`, `rerank_pipeline`; 006 добавляет `pool.grade_status` (approved|self_reported).
- agents-service: `python -m agents_service migrate`, схема `agents_service` (см. ниже).

## Синтетика

Готовых датасетов нет (ТЗ §2) — только собственные данные: `data/seed/` (фикстуры) и генератор
(S5): 200+ кандидатов, 10 вакансий с разметкой релевантности, компании, кандидаты с ФСП-историей
и обязательный кейс «без ФСП-истории».

## Реализованное хранилище проекции кандидатов

`candidate_pipeline` — схема matching-engine, **проекция готовых upstream данных**,
не замена будущих auth/users/profile-таблиц API-core.

| Таблица | Назначение |
|---|---|
| candidates | полный private JSONB, source_revision, внутренняя revision, хеш и состояние |
| strict_schemas / settings | append-only координаты, версии/хеши, активное поколение |
| jobs | durable очередь, unique(candidate_id,revision,schema_version), lease/heartbeat/retry |
| pool | снимок категории (включая `grade_status`), ранга, строгого float8[], semantic vector(640), evidence и двух PDF bytea |
| embedding_cache | SHA-256(model_version + semantic_text) → vector(640); исходного текста нет |
| outbox | candidate.ready / candidate.withdrawn, revision/schema_version, ordered id, delivered_at |
| migrations | имя SQL, SHA-256, время применения |

Пакетные SQL-миграции `services/matching-engine/src/matching_engine/migrations/`
применяются явно: `python -m matching_engine migrate`. Advisory lock сериализует
миграции; изменённый checksum уже применённого файла останавливает запуск.
Применённые файлы не исправлять: следующая миграция — новый SQL-файл.

Изменение профиля сначала отзывает старую публикацию и создаёт новое задание
в одной транзакции. Worker публикует в одной транзакции только при совпадении
profile revision, active schema и непросроченного lease token. Контактный JSONB
и owner PDF не входят в `PoolPage`; публичное имя — `Candidate UUID`.

## Расширение строгого вектора

Исходные 37 координат: 15 доменов + 16 стеков + 6 soft skills. Новая координата
всегда дописывается в конец; старые индексы не меняются. Размерность хранится
в реестре, поэтому `float8[]` не требует ALTER vector(N) при каждом расширении.
Схема хеширует порядок координат, нормировку level/7 и правила статусов.

Расширение атомарно активирует следующую версию, отзывает старый пул с outbox
событиями, инвалидирует старые jobs и ставит **все прежние профили** в backfill.
Draft сохраняет своё состояние; у старого профиля новая координата равна нулю
со статусом null (неизвестно, а не подтверждённый нулевой уровень).
В этот период пул временно меньше/пуст; это осознанная fail-closed миграция,
несовместимые поколения не сравниваются. При ошибке модель/worker чинятся,
failed jobs повторяются через retry; отката индексов или данных нет.
Cached semantic vectors используются повторно, если текст и модель не менялись.

Полный синтетический вход: `data/seed/candidate-pipeline.json` (4 вымышленных
профиля). Проверка `tests/candidate_pool_acceptance.py` предназначена только
для отдельной локальной БД: она добавляет rust и отзывает согласие у одного профиля.

## Проекция вакансий

Миграция `002_vacancy_pipeline.sql` создаёт отдельную схему `vacancy_pipeline`:
vacancies (canonical/raw JSONB, owner, две ревизии, business status/state/checklist),
jobs (lease/retry, unique vacancy/revision/schema), ready (strict float8[],
boolean requirement_mask[], nullable vector(640), fingerprints), outbox
(vacancy.ready/withdrawn). Миграция 001 остаётся неизменной.
Кандидаты и вакансии используют один реестр координат и embedding_cache.
Изменение схемы в одной транзакции отзывает оба ready-пула и создаёт backfill
для всех сохранённых кандидатов и вакансий, включая неполные снимки.
Новая координата вакансии: значение 0, mask=false. Старые значения/маски не меняются.
Ready чтение и публикация требуют текущую ревизию и активную схему;
публикация дополнительно проверяет непросроченный lease.

`semantic_space_id` в обеих проекциях обозначает совместимое пространство модели;
`semantic_model_version` отдельно фиксирует способ построения document/query.
Bio без содержимого не создаёт embedding cache entry. Отсутствующий вектор
хранится как NULL вместе с NULL fingerprints, не как нулевая заглушка.
Реальный synthetic acceptance: `data/seed/vacancy-pipeline.json` (10 случаев) и
`tests/vacancy_pool_acceptance.py`; только свежая изолированная локальная БД.

Миграция `003_vacancy_city_required.sql` отзывает legacy ready вакансии без city,
помечает активные jobs obsolete и сохраняет draft с недостающим city; raw_input
не изменяется. Новый город и предпочтения кандидата хранятся в JSONB snapshots.
Повтор старого intake с той же source_revision сравнивается после нормализации
новых дефолтов, сохраняя идемпотентность ранее записанных снимков.

## Основной отбор: миграция 004

`data/seed/matching-scenarios.json` содержит 20 паттернов × 10 кандидатов и 10
вакансий с независимым ручным oracle допустимости. `tests/combine_acceptance.py`
проверяет весь поток через реальные HTTP API/worker/Harrier/PDF; нужна свежая
отдельная локальная БД. Полные вымышленные входы, снимки карточек, PDF и отчёт
записываются в ignored `tests/artifacts/combine/`. Этот oracle проверяет контракт
фильтрации, не реальное качество найма: Precision/MRR без разметки не заявляются.

`matching_pipeline` хранит settings (pool_generation), controls (top_k/ревизия/текущий
run), runs (неизменяемые snapshots, policy/hash, поколения, lease/retry и статистика),
selections (карточки с позициями и факторами), selection_states (независимые исключения),
manual_requests (ключ ручной идемпотентности), inbox (дедупликация доставки),
delivery_errors (bounded retry/quarantine). SQL trigger candidate outbox увеличивает
generation для всех writers, включая расширение схемы координат.

Run уникален по vacancy revision/schema/pool generation/policy hash/top_k/control revision.
Новый run делает прежний obsolete; lease предыдущего worker отзывается.
Публикация проверяет актуальность всех входов, затем сохраняет весь batch одной
транзакцией. Чтение проверяет те же входы и полный набор candidate ID/revision;
карточки разных поколений не смешиваются. Private contacts/narratives не входят
в candidates_snapshot или cards; PDF сохраняется отдельно в candidate pool.
Текущая ручная policy — `main-v2`: raw class rating сохраняется в explanation,
а коэффициент declared-grade confidence `0.6` применяется только к rating
contribution для `grade_status=self_reported`; strict contribution не меняется.
Старые snapshots с `main-v1` продолжают читаться по сохранённому policy JSON;
новая версия не требует destructive migration и получает новый policy hash.
ACK outbox, inbox и постановка задания атомарны; при storage failure событие
остаётся недоставленным. При malformed snapshot savepoint изолирует одну запись,
после ограниченных повторов она остаётся в quarantine до ручного retry.

## Типизированные факты и миграция005

`NarrativeClaim.facts` необязателен: domain/stack ссылки, `duration_months`,
`role`, `production_support`. Каждое значение имеет собственный статус и приватный
provenance. Отсутствие означает неизвестность; произвольные `details` не преобразуются
автоматически. Ссылки проверяются по общему реестру, включая rejected.
Новые поля не меняют текущий текст Harrier и fingerprint документа. Проверка ссылки
по реестру может записать отдельный status, но ссылка или narrative text не создают
confirmed evidence автоматически.

Миграция005 добавляет `rerank_pipeline`: immutable artifacts/settings pointer,
runs/controls/cards/manual_requests. Идентичность запуска — main ID, artifact,
feature schema и tail policy hashes; nullable artifact участвует в uniqueness.
Fingerprint feature schema включает полную текущую `ScoringPolicy`, поэтому старый
обученный rerank-артефакт с другой policy несовместим; в таком случае runtime
использует явный fallback `R₀`, а не молча применяет устаревшие веса.
Safe narrative projection входит в main snapshot без текста/provenance/контактов.
Результаты одной транзакцией сохраняют candidate ID/revision и полный порядок.
Миграции001–004 не изменяются. Экспериментальные datasets/cache не коммитятся.

## Editorial-черновик вакансии

Схема `agents_service`, миграция `001_vacancy_drafts.sql`: `drafts` хранит поля,
владельца, CAS-ревизию, происхождение полей, недостающие требования, immutable
summary и состояние публикации. `jobs` — очередь publication/enrichment/agent_turn
с owner-scoped idempotency key, hash payload, lease/token, попытками и backoff.
`migrations` проверяет checksum применённых SQL; `settings` хранит версию схемы.

Approve не публикует данные: он сохраняет summary текущей ревизии. Только Create
с совпадающими revision/hash ставит полный matching intake в очередь. После Create
снимок заморожен, включая terminal failure; исправление доставки идёт через retry.
Истечение последнего lease переводит и job, и связанную публикацию в failed,
сбрасывает lease и оставляет безопасный код `max_attempts_exceeded`.
Владелец неизменяем; `owner_id` здесь учётный UUID, а не система аккаунтов.

`002_agent_sessions.sql`: сессии с pinned knowledge/definition hashes, turns с
идемпотентностью и бюджетами, сообщения, typed interactions, tool journal и
owner-scoped provider cache. `003_interaction_revision.sql` привязывает контрол
к ревизии черновика. Инструмент, его journal и новая turn revision фиксируются
одной транзакцией; provider checkpoint хранится до инструментов. Истечение lease
запрещает старому worker любые записи. Несовпадение packaged knowledge hash
останавливает старую сессию с явной ошибкой, не заменяя её инструкции.


## Лента работодателя и история решений

Контур и область наборов описаны в [постановке](../context/51-reranker-and-feed-learning.md#контур-выдачи-и-решений-работодателя--уточнение-06102026).
Порция фиксирует готовый top-k реранкера; like сохраняет кандидата, dislike
создаёт структурный снимок и блокирует новый подбор для этой вакансии.
`visited_updated` сохраняет историю и снимает блокировку только при N > K.
Политика и снимок последнего dislike неизменяемы; новая карточка после достаточного
обновления несёт метаданные прежнего просмотра и объяснение изменений.
Текст, контакты и изменения порядка массивов не участвуют в метрике.


Миграция007 создаёт `feed_pipeline`: sessions фиксируют vacancy/schema/main/rerank/
model/policy identities и истекают через24часа; exposures имеют уникальность
session+candidate и стабильную позицию. decisions уникальны по exposure и по
owner+vacancy+idempotency_key. saved хранит отдельное членство в наборе.
visited содержит последний исходный структурный снимок, полную frozen policy,
последнюю оценённую ревизию и assessment; visited_history сохраняет неизменяемые
снимки dislike и события возврата. Текстовые данные и контакты в baseline не входят.

При установке007 checksum runner под общим pipeline lock фиксирует текущие
профили старых visited со статусом `legacy_migration`; историческое время dislike
остаётся неизвестным. Это происходит до будущих candidate.ready.
Старая схема не переписывается:001–006 остаются checksum-compatible.
Публичный visited не отдаёт private baseline. Saved-членство сохраняется при
изменении/отзыве профиля, но его старая карточка становится недоступной.

## Recruitment persistence

API миграции003/004 создают recruitment_processes, interviews, offers, receipts
и events. Process уникален по owner/vacancy/candidate и привязан FK к accounts;
engine UUID vacancy/draft хранится без прямого доступа к engine таблицам.
Terms фиксируются при приглашении; employer-only shortlist rejection имеет
terms/invited_at=null. Closure хранит причину/сторону/дату, видима участникам
только после реального приглашения. Contacts не копируются в process/receipt/event.
Interviews имеют partial unique slot по process/kind для proposed/scheduled/completed;
completed feedback обязателен, outcomes отдельно от решения о найме. Один active
offer с точной RUB зарплатой, сроком и immutable history. Receipts уникальны
actor/key, fingerprint command включает endpoint. Event sequence сериализован
до commit и привязан к process revision, payload minimal, polling only participants.
Хранение историческое без auto deletion; ни001/002, ни engine migrations
не переписаны. [Подробные переходы и ограничения](recruitment.md).

## QGramm chat storage

Отдельный volume `chat-data` содержит SQLite QGramm и зашифрованные chunks.
Комната/серверные устройства детерминированы по приглашению; текст и имена
вложений остаются в зашифрованном QGramm payload, не в PostgreSQL.
Новая API SQL-миграция не требуется: lazy provisioning опирается на уже
существующий process UUID и неизменяемую пару участников. Метаданные и
секреты восстанавливаются вместе с volume; смена derivation key не является
безопасной ротацией. [Хранение и backup boundary](chat.md).

## Теневые модели предпочтений вакансии

Операторский цикл сохраняет приватный `plan.json` до fit и `report.json` с
контекстами, минимальными counts, validation/golden метриками и моделью15признаков.
JSON-модель включает коэффициенты, covariance, калибровку, trust heuristic,
полный исходный порядок признаков и hashes baseline/projection/transform.
Snapshot и temporal assignments запечатаны отдельно; повторный экспорт создаёт
новый план. Operational fit требует исходный HMAC key и актуальные API/matching
consents под source row locks до записи отчёта. Каталоги0700/файлы0600 не
перезаписываются и остаются вне Git. Эти модели не записываются в live registry
и не изменяют PostgreSQL-схему или HTTP-контракты.
[Структура, протокол и команды](preference-learning.md).

## Фоновый реестр предпочтений вакансии

Миграция matching `009_live_preferences.sql` добавляет `preference_pipeline`:
controls хранит контекст/epoch/CAS revision/active pointer; jobs — sealed план,
хеши входа, lease token/expiry, retries и отчёт; models — неизменяемую версию
модели до обязательного удаления payload при отзыве; participants — связи для
проверки и отзыва согласия; events — историю активаций, отказов, reset/rollback.
Отзыв стирает model и job plan/report, сохраняя аудит по хешам. Участники и
коэффициенты не публикуются через HTTP.

Feed sessions получают preference revision/model hash и order revision.
Уникальность session/position теперь DEFERRABLE для атомарной перестановки
unserved suffix. Delivered позиции и card payloads фиксированы. Export сохраняет
preference identity/order/score и исходный R₀; propensity остаётся неизвестной.
Политика и команды: [обучение предпочтений](preference-learning.md).

## Журнал сопоставимых гипотез

Миграция `012_hypothesis_exploration.sql` (исторически `011_…`, см. [matching-migrations.md](matching-migrations.md)) добавляет `hypothesis_pipeline.controls/pairs/batches/commands`: owner/context/epoch, policy и comparison hashes, immutable allocation draw/probabilities, ссылки двух exposures, контраст и frozen first20 decisive batch. Отзыв consent/ревизии удаляет parameters/report payloads, оставляет audit/hash и invalidated marker. `feed_pipeline.exposures.exploration_suppressed` скрывает отменённые не доставленные добавления; served cards не меняются. `learning_pipeline.samples.sample_scope=main_top_k|exploration` отличает исходные selections от явно выбранных вне top-k research inputs; cohort counts main не переписываются. HMAC экспорт псевдонимизирует pair IDs, условные вероятности идут отдельно от null global propensity. [Подробности](hypothesis-learning.md).

Миграция `011_candidate_showcase.sql`: GIN публичной проекции, query-embedding
cache 640D, источник feed-сессии tinder/showcase и manual exposure kind.
Предыдущие сессии сохраняют обязательные main/rerank ID; ручные сессии имеют
NULL ID. Мигратор backfill пересобирает расширенный allowlist public_profile
из точной исходной ревизии. Saved, decisions и selection_states общие для
обоих источников. [Хранение и границы](candidate-showcase.md).

## API миграция 005: отдельное обучение

`005_training_consent.sql` добавляет candidate_profiles.training_consent
(NOT NULL DEFAULT false), training_consent_version и training_consent_accepted_at.
CHECK требует отдельную версию training-v1 и timestamp при true, действующие
processing/pool; при false оба поля null. Прежние записи не получают разрешение.
Версии account-v1/profile-v1 сохранены; обучение имеет отдельный training-v1.
Exact outbox snapshots сохраняют все разрешения и серверные времена по ревизии.

Применять API миграции до запуска нового API/matching/preference worker.
Затем запустить preference-worker: reconcile удалит старые учебные features
и параметры затронутых моделей/гипотез без отдельного разрешения. Даже до очистки
активная preference-модель и новые операционные export/fit проверяют source
training_consent и не используют прежнее согласие как разрешение на обучение.
Экспортированные файлы требуют отдельного контроля оператора; массовое удаление
профилей/архивов не входит в эту миграцию. [Тексты и ограничения](legal/README.md).

Новые learning samples фиксируют именно training-v1 и training_accepted_at в
consent_version/consent_accepted_at, а не подменяют их временем profile consent.
# Внешняя верификация и документы

`candidate_profile_evidence` хранит canonical input SHA-256, provider status, verified value и provenance. `candidate_verification_ledger` append-only записывает каждый provider result до замены latest evidence. Каждое подтверждение поднимает source revision и создаёт новый immutable outbox snapshot; профильная правка с изменённым input сбрасывает старый status. `candidate_fsp_demo_auth_states` хранит только hash short-lived state, выбранный fixture, revision и одноразовую отметку consumption. Imported facts несут immutable `provider_version` и server `imported_at`. `candidate_verification_documents` хранит только AES-256-GCM ciphertext и metadata со статусом `pending`; загрузка не является подтверждением.

## ANN-индекс пула и durable feed refill

Миграция matching `013_pool_ann.sql` добавляет HNSW cosine index по Harrier `vector(640)` для семантического канала витрины ([search-index.md](search-index.md)).

Миграция `014_feed_refill.sql` расширяет `feed_pipeline.sessions` полями pool snapshot/hash/`refill_state`, создаёт `feed_pipeline.batches` и `feed_pipeline.refill_jobs` (partial unique на queued/running/retrying), а также lineage-поля на exposures. Уже выданные позиции не переписываются; [контракт](feed-refill.md).

## Статистика работодателя: чтение агрегатов

API статистики читает общую PostgreSQL без новых таблиц и изменений исходных событий: source vacancy drafts, feed exposures/decisions/impressions и recruitment events. Индексы для bounded owner/time чтений — API `008_statistics_reads.sql` и matching `015_statistics_reads.sql`. Один SQL-снимок, owner scope на всех источниках, таймаут 5 секунд; [определения метрик](employer-statistics.md). Приватные профили и learning features не входят в ответ.

## Глобальный агент и приватный vault

Новые миграции `005_organization_agent.sql`, `006_organization_documents.sql`
и `007_organization_workflows.sql`
в `agents-service` сохраняют глобальные чаты, сообщения, очередь, поручения,
context/memory, журнал инструментов, вызовы провайдера, резервы и usage ledger.
Уникальность чата задаётся `owner_id`; CAS применяется к memory.
`organization_documents` хранит исходные байты и извлечённый текст, SHA-256,
тип, имя и owner-scoped ключ повторной загрузки. Права чтения наследуются от
аккаунта работодателя; файлы не входят в общую витрину.

API миграции `009_organization_agent_tool_receipts.sql` и
`010_organization_agent_read_receipts.sql` сохраняют immutable hash
запроса и состояние бизнес-вызова по владельцу и ключу. Старые миграции
сохраняют checksum. [Потоки и ограничения](agents.md).

## Очередь событий email (API013)

`notification_mail_outbox` хранит тип, UUID адресата и авторитетного источника,
срок, attempts, fenced lease и состояния `pending/processing/delivered/failed/dropped`.
Email и приватные имена разрешаются из текущего источника только перед delivery.
`notification_mail_cutover` исключает исторические matching likes после обновления
worker. Вставка `recruitment_interviews` и обе записи outbox транзакционны.
[Типы, согласия, retry и отсутствующий producer ежемесячных заданий](email-notifications.md).


## Компания, inbox и процессы найма (API014–016)

Миграция 014 добавляет `employer_profiles`, `system_inboxes`, `system_notifications` и cutover источников. Номер сообщения локален для аккаунта; idempotency key уникален в его inbox. Read cursor монотонен и не превышает реально выданные сообщения. Схема и producer API: [system-inbox.md](system-inbox.md).

Миграция 015 хранит уникальные недельные recommendation jobs, lease, попытки и результаты; отзыв обязательных согласий немедленно удаляет записи. Matching017 отдельно хранит реальные candidate-scoped причины, срок хранения 90 дней и порог четырёх разных вакансий. Детали: [recommendations.md](recommendations.md).

Миграция 016 добавляет `deadline`, `deadline_suspended`, `employer_contacted_at`, `reminder_sent_at`, interview reason_categories, terminal expired и idempotent `recruitment_hire_applications`. Принятие оффера обновляет profile/source revision, опыт, pool consent и projection outbox внутри hiring-транзакции. Миграции уже применённого файла не редактируются. Таблица переходов: [recruitment.md](recruitment.md).

Недельные рекомендации требуют нового явного согласия `training-v2`. Версия
`training-v1` совместима с прежним ранжированием, но не включает рекомендации.
Миграция API `017_recommendation_consent_version.sql` расширяет допустимые версии
и очищает результаты без нового согласия. Обновления резюме сохраняют выбранную версию.

### Реестр расходов работодателя

API-миграция `018_employer_expenses.sql` добавляет `employer_expenses` (source key, owner, optional vacancy, category, integer kopeks, source time, frozen policy, reason) и `employer_expense_ai_days` (owner/Moscow date, cumulative provider micro-USD and billed kopeks). Категории: publication, invitation, ai, penalty. Данные не содержат контактов кандидатов и текста ОС. Владение в HTTP всегда берётся из сессии.

Триггеры записывают расходы атомарно с `recruitment_events` (`invitation.sent`, `deadline.expired`), подтверждённой `agents_service.drafts.published_at` и settled `organization_usage_ledger`. Сбой/rollback источника откатывает начисление, повтор source key не удваивает сумму. Дневная строка блокируется перед накоплением стоимости ИИ, включая конкурентные provider settlements. Zero-cost ledger rows сохраняют факт потребления дневной нормы, но не увеличивают число начислений.

Миграция backfill использует только известные даты публикаций и durable события/settlements, сохраняет исходные timestamps и маркирует исторические записи; неизвестные даты не синтезируются. API/agents migrations допускают оба порядка через deployment-only `install_employer_expense_sources()`; agents migration 011 устанавливает hooks, когда API-схема уже существует. Миграции выполняет владелец общих схем; функции не доступны PUBLIC. Функция поддержки сохраняет ссылку на ручное решение, не публикуя пояснение в статистике. Ни HTTP GET, ни агрегирование рекомендаций не создают расходов.


### Подтверждённые результаты тестирования

Миграция `019_testing_grades.sql` добавляет `candidate_grade_results`: владелец candidate_id, глобально уникальный session_id, подтверждённая оценка, направление, число решённых заданий, метка наблюдения и decided_at. FK удаляет записи вместе с профилем. Только доверенный producer создаёт эти записи; локальная дата для календаря вычисляется из decided_at в Europe/Moscow. API не считает текущую декларацию оценки тестом и не подменяет историю синтетическими событиями.

Миграция `020_training_consent_not_null.sql` закрывает SQL NULL-обход CHECK: включённое согласие требует явную версию v1/v2 и дату. Недопустимые старые строки не получают выдуманное согласие: миграция отклоняется до ручного исправления.

## Данные браузерного грейд-теста

`candidate_grade_results` хранит доверенный результат конкретного семейства.
Профиль получает evidence `grade:<семейство>`; результат SQL/React не заменяет
общий заявленный профессиональный грейд. Работодатель получает только семейство,
грейд, бейдж присутствия и состояние камеры через защищённый профиль кандидата.
Сырые счётчики, идентификатор попытки и видео в этот ответ не входят.

Testing-engine хранит попытку, ответы, checkpoint и принятые сервером согласия
`testing-media-v1` с обязательным processing и отдельным screen_recording во внутреннем `testing-data`. При режиме `full` запись разбита
на ограниченные фрагменты с последовательностью; повтор тех же байтов безопасен,
подмена существующего номера отвергается. Срок хранения 90 дней зафиксирован
в manifest; встроенная очистка удаляет просроченные материалы. Ни этот каталог,
ни внутренний service token не публикуются браузеру.

## Недельная практика и демо-каталог

Миграция `021_weekly_challenges.sql` хранит единственную попытку кандидата за UTC-неделю. Серверная отметка `external:weekly_challenge:current` и outbox обновляются атомарно. Импорт резюме не хранит исходный файл и не меняет профиль без его обычного сохранения. [Демо-каталог](demo-data.md) импортируется отдельной командой, без уведомлений и подтверждённых доказательств.
