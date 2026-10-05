# Конвейер соискателя: данные и путь до пула комбайна (цикл 1 / спринт S2, v3)

> **Уточнение и реализация цикла 1, 05.10.2026 (v3, заменяет связанные правила v2 ниже).**
> Текущая задача начинается с уже собранных данных и принятого грейд-теста;
> регистрация, extraction, проведение теста и модерация не входят в эту реализацию.
> Два самостоятельных вектора: strict (15 доменов + 16 стеков + 6 soft skills,
> все заявленные уровни /7) и semantic (Harrier-270M 640D по описанию, проектам,
> опыту, достижениям и предпочтениям). Каждая группа может быть подтверждена
> или нет: уровни не отбрасываются, статус/происхождение — отдельные входные данные.
> Rejected остаётся в профиле/evidence, но координата равна 0; отсутствующее значение
> имеет статус null. Числовой strict нельзя трактовать без evidence.
> Подтверждение отдельно влияет на tie-breaker ранга; доверительные веса комбайна
> относятся к следующему этапу. Проекция и очередь — matching-engine,
> защищённый транспорт — API-core; будущий домен пользователей/профилей остаётся API-core.
> Контракты и запуск: [[../docs/api.md]], [[../docs/data.md]], [[../docs/runbook.md]].
> Публикация атомарна после strict + semantic + public/owner PDF; изменение профиля
> и отзыв согласия снимают старую публикацию. Новая координата вызывает backfill
> всех прошлых профилей с fail-closed переходом поколения. Разделы v2 ниже описывают
> более широкий S2-план; пункты auth/CRUD/testing в этом цикле ещё не реализованы.

> v2 — модель данных от Mike (05.10, вечер): домены/стеки/софтскиллы на единой шкале 0–7, «образ» кандидата вместо грубых специализаций. Распределение: **алгоритмы — Codex**, **ML — Mike** (IRT-скоринг, вектора, комбайн). Канон: ТЗ п. 2.1, решения [[80-open-questions]], хранение — одна Postgres + JSONB ([[30-domain-model]]).
>
> ⚠️ Дефолты агента, ожидают подтверждения Mike: (1) софтскиллы — self-declared, показываются, 0 очков; (2) пересдача: уровень ниже — сразу, повтор заявленного грейда — cooldown 3 месяца; (3) якорная рубрикация шкалы 0–7 (§3).

## 0. Ключевое решение v2: «образ» вместо специализации

Кандидат не размечается меткой «бэкенд/ML». **Образ** = совокупность подтверждённых уровней по 15 доменам + 16 стекам (+6 софтскиллов для показа), каждый на шкале 0–7. Грейд и категория — **производные** от образа (§4), а не самоописание.

Стыковка с ТЗ (обязательно для жюри): ТЗ требует «опрос → самостоятельный выбор предполагаемого грейда → тестирование на заявленный грейд» и категории «специализация × грейд». Читаем так:
- **Направление** (пресет доменов, например «Backend»: алгоритмы, SQL, RestAPI, архитектура + postgres/python) выбирается при регистрации как primary — это и есть «опрос по отрасли и специализации».
- **Заявленный грейд** выбирается кандидатом; **грейд-тест** проверяет домены направления на заявленном уровне → подтверждает/понижает/повышает (правила ТЗ без изменений).
- **Категория работодателя** = {direction, grade}, где grade — функция от подтверждённых уровней образа (§4). Категория выводится системой из тестов, а не из резюме — требование ТЗ «выдача по категориям из опроса и тестирования, а не самоописанных данных» выполняется.

## 1. Инвентарь данных соискателя (модель Mike)

### A. Идентичность и согласия (`users`, `consents`) — Codex
| Поле | Примечание |
|---|---|
| name | display-имя, приватность: полное/имя/ник |
| email | уникальный, подтверждение кодом (dev: код в лог) |
| phone | контакт, видимость — только после принятия приглашения (ТЗ 2.2.2) |
| password_hash | argon2/bcrypt |
| согласие на обработку (152-ФЗ) | version + ts |
| **согласие на попадание в пул** | version + ts; без него нет входа в пул |

### B. Профиль (`candidate_profiles`) — Codex
| Поле | Примечание |
|---|---|
| место жительства | strict-фильтр |
| готовность к переезду | bool |
| готовность к удалёнке | bool (вместе с форматом работодателя — strict-фильтр) |
| ожидаемая зп min / max | RUB; дефолт: обязательны для пула, видимы работодателю в анонимной карточке (⚠️ дефолт) |
| about | свободный текст — сырьё для плотного эмбеддинга |
| privacy_settings | show_name_mode, search_visible (pause пула) |

### C. Профилизации (`proficiencies`) — центральная таблица v2
Одна структура для трёх групп, справочники в `packages/shared` (заменяют старый SPECIALIZATIONS):
| Группа | Субъекты (ключи) | n |
|---|---|---|
| **domens** | algorithms; sql; doc_databases; rest_api; cybersecurity; data_analytics_stats; classical_ml; neural_nets; nlp_and_arbitrary_data; ai_agents; math; ux_userflow; architecture_thinking; devops; lowlevel_optimization | 15 |
| **stacks** | python; node; go; php; react; vue; angular; html_css_jquery; tailwind; electron; figma; postgres; mongo; sqlite; mysql; docker | 16 |
| **softskills** | teamwork; representation; time_management; project_management; leadership; self_organization | 6 |

Поля строки: `subject_type, subject_key, self_level 0–7, confirmed_level 0–7|null, source (manual|quiz|grade_test|project|experience|fsp), status (draft|pending|confirmed|rejected), points`.

- `self_level` — кандидат заявляет при заполнении (стартовый сигнал для подбора заданий).
- `confirmed_level` — **системно**: домены и стеки — из квизов/грейд-теста (ML, Mike), стеки также через evidence проектов/опыта; софтскиллы — **self-declared, confirmed_level = null, 0 очков** (⚠️ дефолт: объективной верификации нет — честно помечаем работодателю «самооценка»).
- В образ и вектор попадают только confirmed уровни; self_level — только для калибровки сложности заданий.

### D. Достижения (`achievements`) — Codex
`{title, type, level, evidence?, source}`. Типы (справочник, расширяется): olympiad, hackathon, certificate, fsp_rank, fsp_event, publication, other. Уровень → очки: 5 / 10 / 15 / 20 (таблица в конфиге). ФСП-типы наполняются из integration-engine (fsp_id — nullable; **отсутствие истории ФСП — норма**, обязательный кейс ТЗ).

### E. Проекты (`projects`) — Codex
`{title, description, link|file, stack[], my_role, participants}`. Evidence (ссылка/файл) обязательна → auto-verified, очки; стек проекта подтверждает соответствующие stack-профилизации (source=project).

### F. Опыт (`work_experiences`) — Codex
Минимум Mike: `место : должность : срок`. Рекомендую добавить `stack[]` и `summary` — иначе опыт нечего скармливать эмбеддингу и нечего подтверждать стекам. Верификация — mock-модератор в демо (решение Q8), очки 10/место, кап 30.

### G. Системно-заполняемое (read-only для кандидата)
| Артефакт | Кто/как |
|---|---|
| Типизированное резюме (PDF) | генерится из данных — автогенерация PDF-профиля (ТЗ 2.1), Codex |
| **Образ** (confirmed-профилизаций) | система: тесты + evidence-подтверждения |
| Два вектора | matching-engine: strict = 37 заявленных уровней /7 + evidence; semantic = Harrier-640D по description+projects+experience+achievements+preferences; `vector(640)` |
| Подтверждения (статусы) | система: quiz/grade_test/evidence/mock-moderator/fsp — поля status/confirmed_level/points |
| rating_points + ledger | формула §5 |
| freshness | decay + регулярные задачи (S5) |

## 2. Условие входа в пул

`обязательные поля ∧ processing/pool согласия с version/time ∧ принятый грейд primary-направления ∧ зп min/max ∧ strict/semantic построены ∧ PDF готов`

Не выполнено → `draft` с чек-листом «что докрутить». Очки 0 — не препятствие.

## 3. Шкала 0–7: якорная рубрикация (⚠️ дефолт, общая для всех групп)

| Уровень | Якорь |
|---|---|
| 0 | нет |
| 1 | слышал, читал — практических задач не решал |
| 2 | решал типовые задачи под присмотром |
| 3 | решаю типовые задачи самостоятельно |
| 4 | уверенно + краевые случаи, выбираю инструменты |
| 5 | проектирую решения, обучаю других |
| 6 | эксперт: нестандартные задачи, арбитраж |
| 7 | создаю методы/инструменты, лидер мнения |

Грейд-маппинг (производная): intern 1–2, junior 3, middle 4–5, senior 6–7 (junior+/middle+ — интерполяция для UI). Рубрика служит и форматом вопросов (квиз «укажи уровень» валидируется против теста).

## 4. Грейд и категории как производные образа

- При регистрации: выбор **primary-направления** (пресет доменов) + заявленный грейд → грейд-тест направления на заявленном уровне → confirmed_level доменов направления (IRT 2PL, Mike) → грейд.
- Остальные домены/стеки добираются квизами (короткие, по 5–8 заданий на уровень) и evidence — неблокирующий фон, растит образ и очки.
- Категория в БД: `candidate_categories {direction, grade, derived_from snapshot}` — пересчитывается системой; мульти-категорийность (несколько направлений) — за рамками MVP (идея №12, [[95-beyond-mvp]]).

## 5. Формула очков (конфиг YAML, капы)

| Источник | Очки | Кап |
|---|---|---|
| confirmed домен: level × 2 | до 14/домен | 40 |
| confirmed стек: level × 2 | до 14/стек | 25 |
| достижение: по уровню | 5–20 | 30 |
| проект (с evidence) | 8 | 24 |
| место опыта (mock-verified) | 10 | 30 |
| достижение ФСП | см. D (ранг 5–30) | — |
| софтскиллы | **0** (самооценка) | — |
| регулярные задачи | +1, refresh freshness | — |

Правила: только confirmed/evidence-статусы дают очки; каждое начисление — запись в `rating_ledger`; пересчёт вектора — событие.

## 6. Стейт-машина

```
registered → profile_draft → direction_chosen(primary + заявленный грейд) → testing
testing → graded (confirmed | принял ниже | принял выше) → vector_building → in_pool ⇄ paused
параллельно: proficiencies/achievements/projects/experience → pending → confirmed/rejected → recompute
грейд-тест: не прошёл → уровень ниже сразу; повтор заявленного — cooldown 3 мес (⚠️ дефолт)
```

## 7. Контракты ML-сервисов (граница Codex ↔ Mike) — обновление v2

### testing-engine (Mike)
```
POST /sessions  {candidate_id, kind:"grade"|"quiz", subject_key, claimed_level 0–7}
                → {session_id, seed, items[]}
GET  /sessions/{id} → {status, theta, confirmed_level, decision:
                       confirmed|offered_lower|offered_higher, item_stats[]}
```

### matching-engine (Mike)
```
POST /internal/candidates/{id}/vector
     {proficiencies_confirmed[], about, projects[], experiences[], direction}
     → {features, dense[640], model_version}
POST /internal/pool/upsert {candidate_id, categories[], features, dense,
                            rating_points, fsp_boost, freshness} → {pool_version}
```

### integration-engine (готово)
`GET /fsp/participants/{id}` → 200 | 404 (норма).

## 8. Задача для Codex (acceptance criteria цикла 1)

1. Справочники доменов/стеков/софтскиллов/типов достижений/направлений — в `packages/shared` (+seed), заменить старый SPECIALIZATIONS.
2. Миграции: users/consents, candidate_profiles, **proficiencies**, achievements, projects, work_experiences, grade_attempts, rating_ledger, candidate_categories.
3. Auth: register/email-подтверждение (dev: код в лог)/login JWT + согласия (обработка + пул).
4. Профиль: поля B, приватность, правила видимости (контакты — после accept; анонимная карточка: образ-топ, грейд, очки, ФСП-бейджи, город, зп-ожидания — без ФИО-полного и контактов).
5. Заполнение профилизаций: self_level + пояснение уровня (рубрикация §3 в UI).
6. PDF: загрузка → эвристический extraction → экран подтверждения → профилизаций/claims (source=pdf_confirmed).
7. Грейд-тест: оркестрация по §7, attempts, cooldown, присвоение категории, UX offered_lower/higher.
8. Достижения/проекты/опыт: CRUD + evidence-верификация + mock-модерация; очки по §5 (конфиг YAML).
9. Триггеры ML: после graded и после каждого confirmed → vector + pool/upsert (гейт §2).
10. Автогенерация типизированного PDF-резюме.
11. Тесты: стейт-машина, cooldown, формула с капами, happy-path register→pool.

Не входит: чат, приглашения (S4), регулярные задачи (S5), витрина, мульти-категорийность.
