# Конвейер соискателя: данные и путь до пула комбайна (цикл 1 / спринт S2)

> Скоуп: от момента, когда данные зафиксированы, до попадания в пул комбайна с категорией, грейдом и данными. Распределение: **алгоритмы — Codex** (CRUD, стейт-машина, очки, оркестрация), **ML — Mike** (IRT-скоринг сессий, вектора, комбайн). Канон: ТЗ п. 2.1 (кабинет кандидата), решения Q5/Q8/Q12 ([[80-open-questions]]), хранение — одна Postgres + JSONB ([[30-domain-model]]).
>
> ⚠️ Два решения проведены как **дефолты Mike-агента, ожидают подтверждения** (05.10, вопросы заданы, ответа нет): (1) зарплатные ожидания кандидата обязательны для пула и видимы работодателю в анонимной карточке; (2) пересдача — тест уровнем ниже сразу, повтор заявленного грейда через cooldown 3 месяца. Если Mike решит иначе — меняются пункт B (salary_expectations), §2 (гейт) и пункт E (cooldown-логика).

## 0. Границы: Codex / Mike

| Часть | Кто | Состав |
|---|---|---|
| Пайплайн-алгоритмы | **Codex** | миграции+модели, auth+consent, профиль/приватность, PDF-загрузка+эвристический парсер, опрос, оркестрация грейд-теста (вызовы testing-engine), cooldown, claims+ledger+очки, триггеры вектора, автогенерация PDF-профиля, юнит-тесты |
| ML-компоненты | **Mike** | IRT-скоринг и решение по грейду в testing-engine; построитель вектора (фичи + Harrier 640D) и pool-upsert в matching-engine; генератор синтетики |
| Готово | — | integration-engine: мок ФСП (404-семантика отсутствия истории), health-контракты, compose |

## 1. Инвентарь данных соискателя

### A. Идентичность и согласия (`users`, `consents`) — Codex
| Поле | Тип | Примечание |
|---|---|---|
| email | string, уникальный | подтверждение кодом (dev: код в лог — явное решение, не заглушка) |
| password_hash | string | argon2/bcrypt |
| role | enum | candidate \| employer |
| consent_processing | {granted, version, ts} | 152-ФЗ, обязателен для всего |
| consent_publication | {granted, version, ts} | согласие на показ в базе работодателям; **без него нет входа в пул** |

### B. Профиль (`candidate_profiles`) — Codex
| Поле | Тип | Источник | Примечание |
|---|---|---|---|
| display_name | string | ручной/PDF | приватность: полное имя / имя / ник |
| contacts | JSONB {email, phone?, telegram?, preferred} | ручной/PDF | видимость контактов — только после принятия приглашения (ТЗ 2.2.2), не настраивается |
| city, ready_to_relocate | string, bool | ручной/PDF | участвует в strict-фильтре |
| work_format | enum | ручной | remote/office/hybrid |
| employment | enum | ручной | full/part/internship |
| about | text | ручной/PDF | **сырьё для плотного эмбеддинга** |
| salary_expectations | {from, to} RUB | ручной | предлагаю **обязательным для пула** и видимым работодателю — иначе матчинг по ЗП фиктивен (вопрос к Mike ниже) |
| privacy_settings | JSONB | ручной | show_name_mode, show_salary, search_visible (pause пула) |

### C. Резюме-документ (`resume_documents`) — Codex
- file (хранение: volume/локальный диск в MVP), uploaded_at.
- `parsed_json` — результат extraction: {full_name, contacts[], stack[], roles[], years_total, positions[], soft_skills[]} (ТЗ 2.1).
- status: `parsed → confirmed | discarded`; человек **подтверждает/правит** поля — только после подтверждения создаются claims (`source=pdf_confirmed`). Extraction никогда не пишет в верифицированные данные напрямую.
- Реализация extraction: `LlmClient` интерфейс; MVP-реализация — эвристики (pdf-parse + словари стека), облачная (Sverk/РФ-облако) подключается в S4 по env.

### D. Верифицируемые заявления (`verified_claims`) — Codex (реестр), очки — общая формула
| type | payload JSONB | source | верификация | очки |
|---|---|---|---|---|
| work_experience | {company, title, from, to, stack[], summary} | manual / pdf_confirmed | **mock-модератор** (демо-кнопка; в концепции — реальная проверка) | 10/место, кап 30 |
| project | {title, role, link?, repo?, stack[], summary} | manual / pdf_confirmed | evidence-ссылка обязательна → auto-verified | 8, кап 24 |
| skill | {name, level} | manual / test_result | **skill-квиз в testing-engine** либо привязка к evidence | 3/навык, кап 15 |
| education | {institution, degree, year} | manual / pdf_confirmed | без верификации — **0 очков, только показ** | 0 |
| fsp_event | {title, year, place} | **fsp_registry** (integration-engine) | auto по мок-реестру | 2/событие, кап 10 |
| fsp_rank | {rank} | fsp_registry | auto | III=5, II=10, I=15, КМС=20, МС=30 |

Правила: в рейтинг и вектор попадают только `verified`; `rejected` — с причиной; все начисления — записи в `rating_ledger` (claim_id, points, reason, ts) + денормализованный `rating_points` на профиле. Кандидат без истории ФСП — норма (блок просто отсутствует).

### E. Тестирование (`surveys`, `grade_attempts`, `test_sessions`) — оркестрация Codex, скоринг Mike
- `surveys`: industry, specialization (из справочника), answers JSONB — обязательный шаг до теста (ТЗ).
- `grade_attempt`: claimed_grade, specialization, sessions[], decision `confirmed | offered_lower | offered_higher | failed`, assigned_grade, `cooldown_until`.
- Логика: не прошёл на заявленный → предложение теста на уровень ниже (можно сразу или позже); прошёл уверенно → предложение повысить; **повторная попытка того же грейда — через cooldown 3 месяца** (антифарминг); смена грейда в любую сторону ограничена cooldown'ом (ТЗ).
- Регулярные задачи (доп. функционал, S5): результат обновляет `freshness`.

### F. ФСП-блок — integration-engine (готово), Codex — связка
- `fsp_id` (nullable), `linked_at`, снапшот из `GET /fsp/participants/{id}`; ранг/рейтинг → очки + отдельная ось `fsp_boost` (нормализация 0..1) для скоринга.

### G. Производные (только система, кандидату read-only)
| Поле | Откуда |
|---|---|
| category = {specialization, grade} | **только тест** (не резюме, не очки) |
| rating_points + ledger | формула выше |
| vector: `features` JSONB (интерпретируемые, из verified) + `dense vector(640)` (Harrier по about/описаниям) + `model_version` | **matching-engine (Mike)** |
| freshness_score | decay по времени, апдейт регулярными задачами |
| pool_status | draft → in_pool ⇄ paused (кандидат может скрыть себя) |
| profile_completion % | UI-подсказка «что докрутить» |

## 2. Условие входа в пул (гейт конвейера)

`consent_publication ∧ categoryassigned ∧ salary_expectations заполнены ∧ vector built`

Невыполнение = кандидат остаётся в `draft` с явным чек-листом в UI («что сделать, чтобы попасть в базу»). Очки 0 — не препятствие (ТЗ: рейтинг начинается с 0).

## 3. Стейт-машина конвейера

```
registered → profile_draft → survey_done → grade_claimed → testing
testing → categorized (confirmed | принял offered_lower/higher)
categorized → vector_building (auto) → in_pool ⇄ paused
параллельно всегда: claims draft → pending → verified/rejected
  verified → +очки (ledger) → событие recompute_vector → порог изменчивости добора (S3)
grade_attempt failed → (a) тест на уровень ниже когда угодно, (b) повтор заявленного — через 3 мес
```

## 4. Контракты ML-сервисов (граница Codex ↔ Mike)

### testing-engine (Mike)
```
POST /sessions          {candidate_id, kind:"grade", specialization, claimed_grade}
                        → {session_id, seed, items[]}
POST /sessions/{id}/answers {answers[]} → {…}
GET  /sessions/{id}     → {status, theta, decision: confirmed|offered_lower|offered_higher,
                           suggested_grade, item_stats[]}   ← IRT 2PL
```

### matching-engine (Mike)
```
POST /internal/candidates/{id}/vector
     {claims_verified[], about, category} → {features, dense[640], model_version}
POST /internal/pool/upsert
     {candidate_id, category, features, dense, rating_points, fsp_boost, freshness}
     → {pool_version}          ← идемпотентно, по candidate_id
```

### integration-engine (готово)
```
GET /fsp/participants/{fsp_id} → 200 | 404 (истории нет — норма)
```

## 5. Задача для Codex (acceptance criteria цикла 1)

1. Миграции + типизированные модели всех таблиц A–G (ORM: предлагаю **Prisma** — JSONB, миграции, типы; решение за Codex при обосновании).
2. Auth: register → email-подтверждение (dev: код в лог), login (JWT, Keycloak-совместимые claims), consent-запись.
3. Профиль + приватность: CRUD, правила видимости в ответах API (контакты — только после accept; анонимная карточка для подборки).
4. PDF: загрузка → эвристический extraction → экран подтверждения → создание claims (`source=pdf_confirmed`).
5. Опрос + справочник специализаций/грейдов (seed из `packages/shared`).
6. Грейд-тест: оркестрация testing-engine по контрактам §4, запись attempts, cooldown, присвоение категории, обработка offered_lower/higher (UX выбора).
7. Claims + ledger + формула очков (таблица вынесена в конфиг YAML), капы, recompute-события.
8. Триггеры ML: после categorized и после каждого verified → vector + pool/upsert (при выполнении гейта §2).
9. Автогенерация PDF-профиля (ТЗ 2.1).
10. Тесты: стейт-машина, cooldown, формула очков (капы), happy-path интеграционный (register→pool).

Не входит в цикл 1: чат, приглашения (S4), регулярные задачи (S5), витрина.
