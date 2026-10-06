# Сбор реальных карточек для следующего R₀

Основной отбор автоматически сохраняет признаки **каждой карточки своего top-k**
до реакций работодателя, включая невыданные карточки и резерв. Кандидатам вне
top-k и не прошедшим фильтры не приписываются показы: counts
scanned/passed/selected/outside_top_k сохраняются отдельно.

Миграция `008_real_card_learning.sql` добавляет `learning_pipeline`. Capture
выполняется в транзакции публикации main; выдача и реакции — в транзакции feed.
Сбой записи откатывает операцию, worker повторяет durable job. Повторы не создают
второй snapshot или first-event. Для готового main до миграции snapshot также
фиксируется при открытии новой сессии; старые показы задним числом не придумываются.

## Данные и события

В `cohorts` — main run, owner/vacancy/revision, strict/feature/main-policy hashes,
counts, время публикации и объявленный источник `operational|synthetic`.
В `samples` — candidate/revision, исходные позиция/score, feature vector единого
builder с упорядоченными names и availability, hash и stamp согласия.
Имя, контакты, произвольный текст, PDF и приватный provenance не копируются.
Snapshot не заменяется актуальным профилем после реакции.

| Событие | Когда записывается | Что доказывает |
|---|---|---|
| `allocated` | Включение карточки в сессию | Карточка могла быть выдана |
| `delivered` | Возврат страницы из matching API | Сервер отправил карточку; gateway может ещё отклонить ответ |
| `impression` | Клиент подтвердил видимость | Подтверждение клиента, не независимое доказательство внимания |
| `like` / `dislike` | Принята scoped реакция | Явное поведенческое предпочтение |

`served_at` остаётся совместимым полем выдачи. Клиент вызывает
`POST /api/employer/vacancy-drafts/{draft_id}/feed/impressions` с
`{exposure_id,idempotency_key}` при видимости карточки; retry использует тот же UUID.
Ответ: event_id, exposure_id, recorded_at, replayed. Shared/api-client предоставляют
`FeedImpression*` и `acknowledgeFeedImpression`. Проверяются владелец, публикация,
доставка, session/model identity, revision и согласие; gateway проверяет source
watermark. Один exposure имеет один first-impression receipt. Чужой либо повторно
использованный для другой карточки key отклоняется. Receipt можно прочитать
после устаревания сессии без создания нового показа.

Этот проход реализует backend и клиентский контракт. Автоматическая отправка
viewport-событий из будущего Tinder UI не реализована; серверная доставка не
объявляется пользовательским просмотром.

## Метки и будущая оценка

Экспорт включает snapshots без exposures. Explicit like получает `label=1`,
dislike — `label=0`, с `label_kind=explicit_behavior`. Это слабые поведенческие
метки, **не пригодность и не вероятность найма**. Отсутствие реакции всегда
`label=null`; истёкшая сессия добавляет censoring без отрицательной метки.
Invitation/interview/offer остаются отдельными recruitment outcomes и в текущем
экспорте не подменяют реакции.

Порядок детерминирован, exploration нет. `propensity=null`: нельзя исправить
селекционный сдвиг, подставив 1. Сохраняются позиции main/R₀, score,
model/approval/activation identity, session и события. Резерв и невыданные карточки
не смешиваются с отрицательными примерами. Синтетический contour явно маркируется
через `learning_pipeline.settings.source`; источник не угадывается по UUID.

Сборщик сам обучение не запускает. Отдельный операторский
[теневой цикл предпочтений вакансии](preference-learning.md) использует
приватный export, заранее сохранённый temporal plan и проверку актуального согласия.
Manifest содержит
`split_state=pending_group_and_temporal_assignment`: перед обучением нужно
зафиксировать временную golden-выборку и проверить пересечения owner/vacancy/candidate
групп. Повторные сессии не считаются независимыми; пары не строятся из неоткрытых
карточек. Историческая synthetic golden уже оценена и не используется для tuning.

## Согласие и экспорт

Matching intake с отзывом processing/pool согласия в той же транзакции очищает
learning feature payloads, оставляя audit hash/withdrawn_at. Новое согласие не
восстанавливает старый payload. Export проверяет актуальное согласие matching и,
для `operational`, **public.candidate_profiles**, включая отзыв, ещё не доставленный
projection worker. Без authoritative source таблицы operational export запрещён.
Обнаруженные отозванные payloads очищаются перед экспортом. Исторические revisions
при действующем согласии сохраняют исходные признаки; удалённые кандидаты исключаются.

Экспорт локальный и операторский, использует ограничение объёма и PostgreSQL cursor.
Pipeline lock и source consent row locks удерживают согласие до commit; большие
выгрузки выполняйте вне пиков. Файлы содержат чувствительные псевдонимизированные
данные, а не анонимные: перед обучением после отзывов их нужно удалять/пересобирать.
Внешней отправки, автоматического обучения и публичного dataset endpoint нет.

Из `services/matching-engine`, после миграции, с `DATABASE_URL`:

```bash
PYTHONPATH=src .venv/bin/python -m matching_engine.learning_export status
PYTHONPATH=src .venv/bin/python -m matching_engine.learning_export export \
  --directory ../../data/learning/export-001 \
  --key-file ../../data/learning/pseudonym.key --limit 10000
```

Оператор один раз создаёт приватный случайный key минимум32байта (mode0600)
вне Git и переиспользует его для стабильных HMAC IDs; key не экспортируется.
Каталог имеет mode0700, JSONL/manifest — mode0600; существующий каталог не
перезаписывается. Завершённость подтверждает только manifest с checksum;
частичная выгрузка без manifest не пригодна для обучения. `truncated=true`
означает, что лимит не охватил весь набор. По умолчанию экспортируется operational;
`--source synthetic` используется для локальной приёмки. `data/learning/`
исключён из Git вместе с ключами.

## Проверено локально

[Машинная приёмка](../data/validation/real-card-learning-acceptance.json) фиксирует
пять синтетических профилей, одну вакансию, настоящий Harrier640 и упакованный
R₀: learned/manual_override, три выбранные карточки, один passed вне top-k,
две явные реакции, replay impression и экспорт трёх snapshots. Source-only отзыв
до доставки в matching исключил запись и очистил payload. Добавление38-го
параметра дало `feature_schema_mismatch` и полный main fallback.

Регрессия Python/PostgreSQL —1088passed; после сужения migration guard —31 узкая
проверка, отдельно source-consent concurrency —1passed. API с тестовыми БД
—43passed, один внешний QGramm test skipped. `pnpm -r typecheck`, `pnpm -r build`,
scoped Ruff и `make smoke` (12checks) прошли. Compose config валиден.
Production и ingest реальных пользователей не запускались; запуск pipeline с
новым кодом включает сбор operational данных автоматически. Actual viewport
events требуют вызова клиентского контракта, отсутствующий вызов не подменяется.

См. также [активация](rerank-activation.md), [matching](matching.md),
[историческая synthetic-приёмка](rerank-grounded-cohort-acceptance.md),
[канон обучения](../context/51-reranker-and-feed-learning.md).

## Подключение фонового обучения

Помимо операторского shadow CLI подключён `preference-worker`:
[расписание, проверка, реестр и доставка](preference-learning.md#фоновое-обучение-в-контуре).
Реакция и постановка задания атомарны. Learning status сборщика продолжает
описывать только collection/export, поле `training_started` в старом export
не является статусом worker. Его состояние доступно через vacancy preferences API.

## Исследовательские пары

[Hypothesis policy](hypothesis-learning.md) сохраняет pair ID/role/контраст, условные selection/order probabilities до показа. Экспорт HMAC-псевдонимизирует pair ID и показывает sample_scope для новых outside-top-k samples. Global propensity остаётся null; известные условные вероятности не дают поддержки всех альтернативных действий в полном пуле. Like/dislike учат существующий learner; no-action без метки.

## Разрешение на обучение

С 2026-10-07 processing/pool недостаточно: требуется отдельное training=true
с версией training-v1 и серверным временем. capture_main не сохраняет учебный
feature payload без текущих разрешений; operational capture/export сверяется
также с public.candidate_profiles под source row lock. Main top-k и Like/Dislike
как сервисные операции сохраняются и без разрешения на обучение. Export,
shadow fit, live fit/activation и hypothesis comparisons учитывают training;
source-only отзыв немедленно блокирует применение персональной модели.
При доставке или reconciliation очищаются features и затронутые model/pair
payloads. [Согласие](legal/training-consent.md), [миграция](data.md).
