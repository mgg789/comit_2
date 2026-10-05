# Данные — Comit

> Статус: каркас. Схема появляется с миграциями S1/S3; доменная модель — `context/30-domain-model.md`.

## Хранилище

- **PostgreSQL 16 + pgvector** (образ `pgvector/pgvector:pg16`), одна БД, схемы по сервисам.
- Расширение `vector` включается инициализационным скриптом `infra/postgres/init/01-extensions.sql` (только на пустом volume).
- Dev публикует БД на `localhost:5433` (`POSTGRES_PORT`), prod — только внутренняя сеть.

## Синопсис домена (детали — context/30)

- `User` (candidate|employer) → `CandidateProfile` (+`FspProfile` nullable), `Company`
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

## Миграции

- api-core: инструмент миграций выбирается с первой схемой (кандидат: Prisma migrate / node-pg-migrate); файлы в `apps/api/migrations/`.
- matching-engine: вектора/индексы pgvector — в тех же миграциях или отдельной схемой `matching`.

## Синтетика

Готовых датасетов нет (ТЗ §2) — только собственные данные: `data/seed/` (фикстуры) и генератор
(S5): 200+ кандидатов, 10 вакансий с разметкой релевантности, компании, кандидаты с ФСП-историей
и обязательный кейс «без ФСП-истории».
