# Профиль кандидата: источник и проекция

Документ описывает текущую границу между account-owned source profile в `apps/api`
и snapshot, который получает `matching-engine`. Профиль можно сохранять неполным:
это draft источника, а не обещание готового пула.

## Доступ и ревизии

Браузерные методы доступны только аутентифицированному аккаунту с ролью
`candidate`:

| Метод | Путь | Результат |
|---|---|---|
| GET | `/candidate/profile` | текущий private source profile, evidence, consents и projection status |
| PUT | `/candidate/profile` | полная замена editable fields при `expected_revision` |
| PATCH | `/candidate/profile` | shallow patch при `expected_revision`; переданные массивы заменяются целиком |
| POST | `/candidate/profile/projection/retry` | retry последней terminal `failed` projection |
| GET | `/candidate/pipeline` | статус matching projection кандидата |
| GET | `/candidate/resume.pdf` | собственный owner PDF после актуальной projection |

`candidate_id` и owner выводятся из JWT/DB session. Клиент не передаёт owner UUID,
email, evidence или server timestamps. Создание выполняется с
`expected_revision=0`; успешная запись увеличивает `revision` монотонно. PUT/PATCH
с устаревшей ревизией возвращают `409 profile_revision_conflict` и не создают новый
outbox snapshot.

Editable fields включают display name, телефон, город, форматы работы, переезд,
зарплатный диапазон, primary direction, declared grade, домены, стеки, soft skills,
описание, проекты, опыт, достижения и preferences. ID профиля, `candidate_id`, email,
`grade_result`, `test_session_id`, `evidence`, `verified_value`, provenance,
projection state и consent timestamp запрещены в source mutation.

## Декларации и evidence

Навык хранит заявленный `level` 0–7. Narrative claim имеет стабильный серверный
UUID: существующий ID можно вернуть для обновления того же claim, а новый ID всегда
выдаёт сервер. Это не запрещает round-trip ID вложенного claim; ID профиля и
`candidate_id` не являются editable полями. Claim нужен для адресации отдельных
facts/evidence. Поля `facts` (домены, стеки,
duration, role, production support) структурированы отдельно от текста. Ссылка
сама по себе ничего не подтверждает.

Evidence — отдельный read-only канал ответа. Для каждого ключа хранятся:

- `input_hash`: SHA-256 канонического JSON входного значения;
- `status`: `self_reported | pending | confirmed | partial | rejected`;
- `verified_value`, provenance и server `checked_at`.

Канонизация сортирует ключи объектов, сохраняет порядок массивов и исключает
`undefined`. При изменении уже подтверждённого значения projection возвращает
`self_reported`, пока источник не подтвердит новый hash. Это не даёт source profile
самому назначить себе proof.

## Согласия и грейд

Profile consent отдельный от account consent и использует версию `profile-v1`.
Клиент передаёт только `processing`, `pool` и `version`; `accepted_at` ставится
сервером в той же транзакции, что и source snapshot. Отсутствующее или ложное
согласие не подменяется дефолтом. Без обоих согласий matching projection остаётся
draft.

`declared_grade` — пользовательская декларация. Browser contour выдаёт
`self_reported`: UUID сессии не придумывается. Контракт предусматривает будущий
trusted testing source, который сможет заменить его на legacy `approved` с
настоящими `test_session_id` и `approved_at`; этот producer пока не интегрирован.
Он должен сначала обновить authoritative evidence в API и поставить новый exact
snapshot в outbox: передача approved напрямую в matching не заменяет source.
Оба состояния могут получить категорию и попасть в пул при наличии остальных
обязательных полей; `pending`/`rejected` остаются заблокированными.

Публичный PDF показывает badge самооценочного грейда. В `main-v2` raw rating
остаётся доступен в explanation, а confidence `0.6` применяется только к
rating contribution категории `self_reported`; strict contribution не меняется.
После интеграции trusted producer принятый тест заменяет declared snapshot, а
статус становится `approved` с реальной сессией и временем принятия.

## Durable projection

После успешной CAS-записи source profile и полный canonical intake snapshot
вставляются в `candidate_profile_outbox` одной транзакцией. Worker:

1. claims один snapshot с lease token и bounded attempts;
2. отправляет его во внутренний `PUT /internal/candidates/{id}/profile`;
3. проверяет `candidate_id` и `profile_revision` в ACK;
4. ACK/failure записывает только пока lease принадлежит этому worker.

`delivered` означает принятие snapshot matching-engine. Это не означает, что
strict/semantic vectors и PDF построены или что кандидат уже `ready` в pool.
Проблема одной projection не блокирует другие profile rows: retry и error code
локальны для source revision. API gateway проверяет текущий source watermark и
consents, а matching отдельно проверяет собственную ревизию и артефакты перед
выдачей.

CLI worker подключается только через environment-backed `DATABASE_URL`,
`CANDIDATE_PIPELINE_TOKEN` и `MATCHING_ENGINE_URL`:

```bash
pnpm --filter @comit/api profile:worker
```

`--once` предназначен для локального прогона одного job; токены и исходные
исключения не попадают в логи.

## Ограничения проверки

Unit/API contract проверки не являются доказательством production deployment,
живого PostgreSQL контура или acceptance с настоящим Harrier. Эти проверки должны
выполняться отдельно на свежей изолированной БД с pinned model cache; текущая
документация не утверждает, что такой runtime acceptance уже пройден.
