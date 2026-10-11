# Конвейер соискателя: данные и путь до пула комбайна (цикл 1 / спринт S2, v3)

> **Уточнение и реализация цикла 1, 05.10.2026 (v3, заменяет связанные правила v2 ниже).**
> Текущий контур принимает account-owned source profile через CAS и durable
> projection outbox. Проведение теста остаётся trusted upstream контуром, но
> пользовательская декларация грейда может быть self_reported до его принятия.
> Два самостоятельных вектора: strict (15 доменов + 16 стеков + 6 soft skills,
> все заявленные уровни /7) и semantic (Harrier-270M 640D по описанию, проектам,
> опыту, достижениям и предпочтениям). Каждая группа может быть подтверждена
> или нет: уровни не отбрасываются, статус/происхождение — отдельные входные данные.
> Rejected остаётся в профиле/evidence, но координата равна 0; отсутствующее значение
> имеет статус null. Числовой strict нельзя трактовать без evidence.
> Подтверждение отдельно влияет на tie-breaker ранга; grade confidence в main-v2
> влияет только на rating contribution self_reported. Source profile и evidence
> принадлежат API-core, exact snapshot и очередь projection доставляются в
> matching-engine защищённым сервисным транспортом.
> Контракты и запуск: [[../docs/api.md]], [[../docs/data.md]], [[../docs/runbook.md]].
> Публикация атомарна после strict + semantic + public/owner PDF; изменение профиля
> и отзыв согласия снимают старую публикацию. Новая координата вызывает backfill
> всех прошлых профилей с fail-closed переходом поколения. Разделы v2 ниже описывают
> более широкий S2-план; UI и реальный testing provider не входят в этот контур.
> Email auth/account session boundary и profile CRUD реализованы отдельно в API-core.
> При добавлении vacancy intake общий справочник расширен самостоятельным `ml`;
> он не объединяется с `data`. Оба пула мигрируют по одному реестру координат.

> v2 — модель данных от Mike (05.10, вечер): домены/стеки/софтскиллы на единой шкале 0–7, «образ» кандидата вместо грубых специализаций. Распределение: **алгоритмы — Codex**, **ML — Mike** (IRT-скоринг, вектора, комбайн). Канон: ТЗ п. 2.1, решения [[80-open-questions]], хранение — одна Postgres + JSONB ([[30-domain-model]]).
>
> ⚠️ Дефолты агента, ожидают подтверждения Mike: (1) софтскиллы — self-declared, показываются, 0 очков; (2) пересдача: уровень ниже — сразу, повтор заявленного грейда — cooldown 3 месяца; (3) якорная рубрикация шкалы 0–7 (§3).

## 0. Ключевое решение v2: «образ» вместо специализации

Кандидат не размечается меткой «бэкенд/ML». **Образ** = все заявленные уровни по 15 доменам + 16 стекам (+6 soft skills для показа), каждый на шкале 0–7, с отдельным статусом и provenance. Грейд и категория — производные declared/trusted grade snapshot с явным статусом (§4), а не данные резюме.

Стыковка с ТЗ (обязательно для жюри): ТЗ требует «опрос → самостоятельный выбор предполагаемого грейда → тестирование на заявленный грейд» и категории «специализация × грейд». Читаем так:
- **Направление** (пресет доменов, например «Backend»: алгоритмы, SQL, RestAPI, архитектура + postgres/python) выбирается при регистрации как primary — это и есть «опрос по отрасли и специализации».
- **Заявленный грейд** выбирается кандидатом; **грейд-тест** проверяет домены направления на заявленном уровне → подтверждает/понижает/повышает (правила ТЗ без изменений).
- **Категория работодателя** = {direction, grade, grade_status}. `approved` приходит из
  trusted теста, `self_reported` — явная временная декларация без выдуманного UUID;
  обе категории выводятся из intake, а не из свободного текста резюме. После принятия
  теста self_reported snapshot заменяется approved.

## 1. Инвентарь данных соискателя (модель Mike)

### A. Идентичность и согласия (`users`, `consents`) — Codex
| Поле | Примечание |
|---|---|
| name | display-имя, приватность: полное/имя/ник |
| email | глобально уникальный, подтверждение одноразовым OTP через durable SMTP outbox; код не пишется в лог |
| phone | контакт, видимость — только после принятия приглашения (ТЗ 2.2.2) |
| role | неизменяемый `candidate` или `employer`, один email — одна роль |
| account consent | version + server timestamp; это не согласие на публикацию профиля |
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

Поля строки: `subject_type, subject_key, self_level 0–7, confirmed_level 0–7|null, source (manual|quiz|grade_test|project|experience|fsp), status (self_reported|pending|confirmed|partial|rejected), points`.

- `self_level` — кандидат заявляет при заполнении (стартовый сигнал для подбора заданий).
- `confirmed_level` — **системно**: домены и стеки — из квизов/грейд-теста (ML, Mike), стеки также через evidence проектов/опыта; софтскиллы — **self-declared, confirmed_level = null, 0 очков** (⚠️ дефолт: объективной верификации нет — честно помечаем работодателю «самооценка»).
- В strict vector попадают все заявленные уровни и отдельный evidence status; rejected получает нулевую координату, отсутствующее значение остаётся `null`. Ссылки и narrative text не создают evidence автоматически.

**Уточнение реализованного предметного контура, 2026-10-11.** Кандидат выбирает
каноническую координату из активной versioned strict-schema и именованный уровень
Intern/Junior/Junior+/Middle/Middle+/Senior/Senior+ (1–7). Принятый trusted тест
`algorithms/sql/rest_api → domain`, `python/react/docker → stack`,
`teamwork → soft_skill` подтверждает только эту координату. Четыре грейда теста
соответствуют 1/2/4/6; «+» evidence не создаётся. Для измеренной координаты API
проецирует подтверждённый уровень, сохраняя исходную самооценку отдельно.
Иные координаты остаются declared. Teamwork — результат предметного теста,
не доказательство поведения в реальной команде; прочие soft skills остаются
самооценкой. Этот реализованный контур уточняет дефолт про отсутствие тестов
soft skills выше; общая категория `declared_grade` skill-тестом не меняется.
Повтор предметного теста — через 7 дней, повышение — через 30 дней (серверные
cooldown); общий проектный дефолт про три месяца не используется для этих семи
предметов. См. [контракт профиля](../docs/candidate-profile.md).

### D. Достижения (`achievements`) — Codex
`{title, type, level, evidence?, source}`. Типы (справочник, расширяется): olympiad, hackathon, certificate, fsp_rank, fsp_event, publication, other. Уровень → очки: 5 / 10 / 15 / 20 (таблица в конфиге). ФСП-типы наполняются из integration-engine (fsp_id — nullable; **отсутствие истории ФСП — норма**, обязательный кейс ТЗ).

### E. Проекты (`projects`) — Codex
`{title, description, link|file, stack[], my_role, participants}`. Ссылка или файл
сохраняются как заявленный источник и сами по себе не становятся evidence и не
дают очки; подтверждение требует отдельного trusted/manual статуса. Стек проекта
остаётся declared до такой проверки и не переводит профилизацию в confirmed
автоматически.

### F. Опыт (`work_experiences`) — Codex
Минимум Mike: `место : должность : срок`. Рекомендую добавить `stack[]` и `summary` — иначе опыт нечего скармливать эмбеддингу и нечего подтверждать стекам. Верификация — mock-модератор в демо (решение Q8), очки 10/место, кап 30.

### G. Системно-заполняемое (read-only для кандидата)
| Артефакт | Кто/как |
|---|---|
| Типизированное резюме (PDF) | генерится из данных — автогенерация PDF-профиля (ТЗ 2.1), Codex |
| **Образ** (заявленные уровни + статусы) | система хранит декларации, evidence и подтверждённые значения отдельно |
| Два вектора | matching-engine: strict = 37 заявленных уровней /7 + evidence; semantic = Harrier-640D по description+projects+experience+achievements+preferences; `vector(640)` |
| Подтверждения (статусы) | система: quiz/grade_test/evidence/mock-moderator/fsp — поля status/confirmed_level/points |
| rating_points + ledger | формула §5 |
| freshness | decay + регулярные задачи (S5) |

## 2. Условие входа в пул

`обязательные поля ∧ processing/pool согласия с version/time ∧ approved ИЛИ self_reported grade primary-направления ∧ зп min/max ∧ strict/semantic построены ∧ PDF готов`

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

- При регистрации: выбор **primary-направления** (пресет доменов) + declared grade. Browser projection выдаёт `grade_status=self_reported`; подготовленный контракт будущего trusted test producer заменяет snapshot на `approved` с реальной session UUID и `approved_at` только через API authoritative evidence и новую outbox запись.
- Остальные домены/стеки добираются квизами (короткие, по 5–8 заданий на уровень) и evidence — неблокирующий фон, растит образ и очки.
- Категория в БД: `candidate_categories {direction, grade, grade_status, derived_from snapshot}` — self_reported допускается в пул, но public PDF явно показывает badge. В `main-v2` коэффициент 0.6 применяется только к rating contribution self_reported. Мульти-категорийность остаётся за рамками MVP.

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

Правила: skill/narrative очки дают только confirmed/evidence-статусы; grade confidence не превращает самоотчёт в evidence и учитывается только в main rating contribution. Каждое начисление — запись в `rating_ledger`; пересчёт вектора — событие.

## 6. Стейт-машина

```
registered → profile_draft → direction_chosen(primary + declared grade) → self_reported|testing
self_reported → vector_building → in_pool ⇄ paused
testing → graded (approved: confirmed | принял ниже | принял выше) → vector_building → in_pool
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
2. Source profile: CAS revision, stable claim UUID, canonical evidence hash, server-owned profile consent и exact snapshot outbox.
3. Auth/account уже реализован отдельной API-core миграцией: register/OTP verify, JWT и revocable DB session; согласие на обработку аккаунта не заменяет pool consent.
4. Профиль: поля B, privacy/visibility, self_reported grade badge, approved test replacement и matching-ready checklist; контакты остаются за post-accept boundary.
5. Заполнение профилизаций: self_level + пояснение уровня (рубрикация §3 в UI).
6. PDF: загрузка → эвристический extraction → экран подтверждения → профилизаций/claims (source=pdf_confirmed).
7. Грейд-тест: оркестрация по §7, attempts, cooldown, присвоение категории, UX offered_lower/higher.
8. Достижения/проекты/опыт: CRUD + evidence-верификация + mock-модерация; очки по §5 (конфиг YAML).
9. Триггеры ML: после self_reported snapshot и после approved/evidence update → vector + pool/upsert (гейт §2).
10. Автогенерация типизированного PDF-резюме.
11. Тесты: стейт-машина, cooldown, формула с капами, happy-path register→pool.

Не входит: чат, приглашения (S4), регулярные задачи (S5), витрина, мульти-категорийность.
