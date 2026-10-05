# ML-workstream Mike: что именно требуется (S2 → S3 → S5)

> **Обновление 05.10, цикл 1:** Codex реализовал настоящую проекцию candidate→pool,
> strict+semantic/PDF, durable worker и миграцию координат; моков эмбеддингов
> в runtime нет. Текущий вход — уже собранный upstream snapshot через PUT profile,
> заменяющий прежние отдельные vector/upsert контракты ниже в рамках этого цикла.
> Правила статусов и двух векторов — [[35-candidate-pipeline]] v3;
> фактические DTO — [[../docs/api.md]]. IRT, обучение и vacancy combine остаются
> ML-workstream следующего этапа; старые session-контракты — ещё постановка.

> Твоя часть конвейера соискателя ([[35-candidate-pipeline]]) и последующее обучение.
> Ниже сохранён исходный ML-план. На06.10.2026 candidate/vacancy/main/R₀ backend
> работают через реальные durable PG контракты; текущая реализация — в `docs/`.
> Первый R₀ следует [[51-reranker-and-feed-learning]]: teacher pilot остановлен,
> поэтому обучение на его разметке и активация не выполнены. Testing-часть плана
> этим проходом не изменялась. Всё в существующих сервисах matching/testing.

## 0. Твои артефакты по спринтам

| Спринт | Артефакт | Приёмка |
|---|---|---|
| S2 (вт) | 3 эндпоинта: сессии+IRT (testing), vector+pool/upsert (matching) + минимальный банк заданий | юнит-тесты + OpenAPI + сквозняк с моками Codex |
| S3 (ср) | комбайн: strict → score (ручные веса) → top-k → Selection с объяснением; добор по событиям | вакансия получает подборку в демо |
| S5 (пт) | обучение: веса LTR, реранкер, IRT-калибровка, дотюн Harrier (с гейтом), симуляции тиндер-лупа | валидационная дока с цифрами (критерии 35%+25%) |

---

## 1. testing-engine: сессии и IRT-скоринг (S2)

### 1.1 Контракты (pydantic-схемы = OpenAPI)

```jsonc
POST /sessions
{ "candidate_id": "uuid",
  "kind": "grade" | "quiz",            // grade = направление (несколько доменов), quiz = один субъект
  "subject_keys": ["algorithms","sql"], // grade: домены направления; quiz: [один домен|стек]
  "claimed_level": 3 }                  // 0–7, для quiz — целевой уровень квиза
→ 201 { "session_id", "seed", "items": [ { "item_id", "subject_key", "type": "code|quiz|approach",
          "statement", "params", "time_limit_s" } ] }
//  NOTE: a/b (сложность) и ответы наружу не отдаются никогда

POST /sessions/{id}/answers
{ "answers": [ { "item_id", "payload" } ] }   // code: текст решения; quiz: номер(а); approach: текст
→ { "accepted": true, "session_status": "in_progress|finished" }

GET /sessions/{id}
→ { "status", "theta", "se", "confirmed_level", "decision": "confirmed|offered_lower|offered_higher|in_progress",
    "suggested_level": 0–7|null, "item_stats": [{ "item_id", "correct": true|false|null }] }
```

### 1.2 Адаптивная сессия (CAT-lite, 2PL)

- Модель: `P(correct | θ, a, b) = σ(a·(θ − b))`; шкала θ ∈ [0, 7] — та же, что уровни (b хранится в тех же единицах; латентный −4..4 при калибровке переводим линейным маппингом).
- Старт: `θ₀ = claimed_level` (для quiz — целевой уровень квиза), SE₀ = 1.5.
- Выбор следующего задания: из пула blueprints субъекта с `b ≈ θ` максимум информации `I(θ) = a²·p·(1−p)`; не повторять blueprint внутри сессии.
- Апдейт после каждого ответа: EAP по сетке θ ∈ [0,7] шаг 0.1, prior N(claimed_level, 1).
- Стоп: `SE < 0.35` или `N ≥ 12` (grade) / `N ≥ 8` (quiz), либо исчерпан пул уровня ±1.
- Решение (пороги в конфиге, стартовые значения):
  - `confirmed`: `θ − 0.5·SE ≥ claimed_level − 0.25` → `confirmed_level = clamp(round(θ), 0, 7)`
  - `offered_higher`: `θ − 0.5·SE ≥ claimed_level + 1.25` → предложить тест/подтверждение уровня выше
  - `offered_lower`: `θ + 0.5·SE < claimed_level − 0.75` → `suggested_level = round(θ)`
  - иначе — `confirmed` с пониженным `confirmed_level = round(θ)` не делаем: недотяг до заявленного = `offered_lower` (ТЗ: принудительно не понижаем).
- Приоритет реализации: точная математика вторична по отношению к **воспроизводимости и объяснимости** — фиксированный сид, лог решений в `item_stats`.

### 1.3 Варианты из blueprints

- Реестр уже есть (`blueprints/*.yaml`). Добавить: `instantiate(blueprint, seed) → item` — детерминированная генерация параметров в заявленных диапазонах; для `quiz` — выбор N вопросов из банка уровня-диапазона `[level−1, level+1]`.
- Проверка ответов: code — автотесты в песочнице (subprocess, таймаут 10s, без сети, лимит памяти; Docker-изоляция можно отложить на S3, для S2 достаточно subprocess); quiz — ключи; approach — рубрика-ключевые-структуры (честно пометить в доке, LLM-оценка в S4).

### 1.4 Банк заданий (контент — твоя зона, минимальный объём цикла 1)

| Для чего | Что нужно |
|---|---|
| Направление Backend (grade) | домены algorithms, sql, rest_api, architecture_thinking × уровни {3, 5} × 2 blueprint'а = 16 записей |
| Квизы стеков | python, postgres, docker × уровни {3, 5} × 1 квиз-набор (по 8–10 вопросов) |
| Итого | ~20 blueprint'ов + 3 банка вопросов |

Писать можно с LLM-ассистентом, но каждый проходится руками (качество заданий = дискриминативность = 25% критерия). Заготовка `algo-hash-lookup.yaml` уже в репо — формат держать.

### 1.5 Приёмка S2 (testing)

- Юнит: EAP-апдейт сходится к истинному θ на симуляции; decision-правила на 4 сценариях (уверенно прошёл / на грани / провал / выше заявленного); seed-воспроизводимость вариантов.
- Интеграционный: lifecycle create→answers→result; повторное прохождение с новым сидом даёт тот же confirmed_level (тест-retest smoke).
- OpenAPI в `/docs`, схемы не отдают a/b/ключи.

---

## 2. matching-engine: vector + pool (S2)

### 2.1 `POST /internal/candidates/{id}/vector`

```jsonc
// вход (от Codex — только confirmed-данные, self_level не входит)
{ "proficiencies": [ { "subject_type": "domain|stack", "subject_key", "confirmed_level": 0–7,
                        "evidence": "test|project|experience|fsp" } ],
  "about": "…", "projects": [{ "title", "summary", "stack" }], "experiences": [{ "title", "summary" }],
  "direction": "backend" }
→ 200 { "features": {...}, "dense": [640 float], "model_version": "harrier-oss-v1-270m@<rev>",
         "vector_id": "uuid" }
```

- `features`: интерпретируемая часть — 31-мерный вектор confirmed-уровней (домены+стеки, нормировка /7) + флаги evidence + направление; JSONB-снапшот для объяснений «почему в подборке».
- `dense`: текст = about + проекты (title+summary) + опыт; конкатенация с разделителями, ограничение длины (первый чанк модели); `microsoft/harrier-oss-v1-270m`, **пinned HF revision**, L2-нормализация, 640D.
- Хранение: `matching.candidate_vectors` (candidate_id unique, features JSONB, dense vector(640), model_version, updated_at); идемпотентно по candidate_id.
- Инференс на CPU (mgg-server): модель грузится один раз при старте, батч-режим; если артефакт недоступен — сервис отвечает 503 по vector-эндпоинту (ready=false), но живёт (принцип деградации).

### 2.2 `POST /internal/pool/upsert`

```jsonc
{ "candidate_id", "categories": [{ "direction", "grade" }], "features", "dense",
  "rating_points", "fsp_boost": 0–1, "freshness": 0–1 }
→ { "pool_version": int }
```

- Таблица `matching.pool` (candidate_id unique, …, pool_version, updated_at). Идемпотентный upsert; при существенном изменении — событие для добора (S3).
- Для S3 комбайн читает пул + vacancies;严格的 фильтр и скоринг — там же (ручные веса w_r=0.45, w_m=0.35, w_f=0.15, w_a=0.05 стартовые).

### 2.3 Приёмка S2 (matching)

- Юнит: сборка features (нормировки, caps), идемпотентность upsert, воспроизводимость dense на фиксированном тексте (hash-тест).
- Интеграционный: vector→upsert→SELECT в pgvector возвращает запись 640D; модель не грузится — 503, сервис жив.

---

## 3. S5: обучение и валидация (твой главный ML-вклад в оценку)

Протоколы — [[50-matching-engine]] и [[60-testing-engine]], здесь сводка объёма:

1. **Генератор синтетики** (`data/generator/`): 200–1000 кандидатов с латентной пригодностью, 30 вакансий с эталонной релевантностью, 2 работодателя-агента с фиксированными предпочтениями.
2. **Веса скоринга**: линейный LTR (pointwise logreg) на ~30k пар → JSON-артефакт `make train` (seed), ablation + Precision@5/10 + MRR.
3. **Реранкер**: pairwise-логрегрессия на интеракциях фич; сравнение с базовым скором.
4. **IRT-калибровка**: фит a/b по симулированным ответам → отчёт test-retest стабильность + point-biserial.
5. **Дотюн Harrier**: контрастивный fine-tune (MNRL) на train-парах на твоём Mac; **гейт**: tuned vs base на отложенных парах — в продукт входит только победивший; артефакт + отчёт.
6. **Симуляции тиндер-лупа**: свайпов до сходимости вектора вакансии к предпочтениям агента (метрика M@90%).
7. **Валидационная дока** (`docs/validation.md`): протокол + таблицы + интерпретация — сдаётся в документации (критерий 25% оценивает именно это).

---

## 4. Входы, которые нужны от тебя (решить/принести)

| # | Что | Когда |
|---|---|---|
| 1 | Подтвердить контент-план банка (§1.4) и кто пишет тексты заданий (ты + LLM, ревью руками) | S2 утро |
| 2 | Harrier: точный HF id/чекпоинт (и свой ли?), лицензия OK для коммерческого демо; путь дотюна — MLX-скрипт на Mac | S2 |
| 3 | Пороги decision-правил (§1.2) — стартовые ставлю сам, тюнить будем на симуляции S5 | по факту |
| 4 | LLM для PDF/explanations (S4): endpoint Sverk или РФ-облако + ключ в env сервера | к S4 |

## 5. Стык с Codex

- Codex до конца S2 работает на **моках** §1.1/§2.1 (моки описывает в своей задаче из [[35-candidate-pipeline]] §8).
- Стыковка: конец S2 — твои реальные эндпоинты локально; сквозной smoke `register → graded → vector → pool` зелёный к началу S3.
- Изменение схем — только через правку этого файла + [[35-candidate-pipeline]] одним коммитом и уведомление Codex-задачи (правило контрактов AGENTS §3).
