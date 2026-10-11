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
| GET | `/candidate/skills/catalog` | активная схема с названиями, точными предметами тестов и приватными сроками повторной проверки |
| GET | `/candidate/taxonomy` | текущая strict-schema (ключи доменов/стеков/soft skills) из matching-engine |

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

Новый навык выбирается из активного каталога matching-engine. В интерфейсе уровни:
Intern, Junior, Junior+, Middle, Middle+, Senior, Senior+; внутренние значения —
1–7. Ноль читается только для совместимости со старыми профилями.
Новый неизвестный ключ или неверная пара группы и ключа отклоняется API с
`400 unknown_skill_coordinate`; изменение уровня на ноль — `invalid_skill_level`.
Старый неизвестный навык можно оставить без изменений при редактировании других
полей, явно заменить каноническим или удалить. Свободный дополнительный опыт
пишется в `description`, не создавая координату strict vector. Если registry
недоступен, новые/изменённые навыки не сохраняются; неизменённые значения сохраняются.

Навык хранит заявленный `level` отдельно от результата теста. Narrative claim имеет стабильный серверный
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
самому назначить себе proof. Предметные тесты — отдельные независимые измерения:
изменение декларации не переписывает принятое сервером измерение. Matching получает
измеренный уровень и `confirmed` для той же координаты, декларация остаётся в source.

Точное соответствие тестов: `algorithms`, `sql`, `rest_api` → `domains`;
`python`, `react`, `docker` → `stacks`; `teamwork` → `soft_skills`.
Intern/Junior/Middle/Senior теста дают 1/2/4/6 соответственно. Уровни «+» тестом
не подтверждаются. Teamwork подтверждает результат предметного теста, а не
наблюдение поведения человека в реальной команде. Skill test не меняет
`declared_grade`, и отдельного списка несвязанных грейдов в профиле нет.

`grade:<subject>` и ledger `candidate_grade_results` читаются совместимо:
старые результаты получают координатное evidence с hash текущей декларации;
потерянные старым редактором `grade:*` восстанавливаются из accepted ledger.
Старые отсутствующие или пустые группы восстанавливаются по принятому результату.
Явное удаление в новом редакторе записывает серверный marker
`external:testing_removed:<subject>`: он исключает навык из восстановления и matching,
пока кандидат не добавит его снова. Так legacy-пустота отличается от удаления. Новый trusted
результат пишет координатное evidence, source revision и outbox в одной транзакции.

## Согласия и грейд

Profile consent отдельный от account consent и использует версию `profile-v1`.
Клиент передаёт только `processing`, `pool` и `version`; `accepted_at` ставится
сервером в той же транзакции, что и source snapshot. Отсутствующее или ложное
согласие не подменяется дефолтом. Без обоих согласий matching projection остаётся
draft.

`declared_grade` — пользовательская декларация. Browser contour выдаёт
`self_reported`: UUID сессии не придумывается. Внутренний trusted producer
`/internal/testing/session-result` атомарно пишет evidence и exact outbox.
Предметные тесты подтверждают собственную координату, не общую профессию;
legacy результаты направлений могут дать `approved` с настоящими session id
и временем, но не создаются выбором предмета в новом интерфейсе.
Передача approved напрямую в matching не заменяет source.
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

## Сквозная локальная проверка

`tests/candidate_profile_acceptance.py` проверяет реальный HTTP-контур на свежей
локальной БД: email-code auth → source profile → durable projection → настоящий
Harrier 640D и PDF → создание вакансии → main combine → две карточки из пяти.
Для OTP используется зашифрованный тестовый mailbox через приватный IPC, без
отправки писем через SMTP. Все аккаунты синтетические `@example.test`.

Прогон 2026-10-06 подтвердил строгие фильтры класса, зарплаты и города; запрет
поддельных proofs; роль/owner/origin; коэффициент грейда 0,6 и обезличенный PDF.
Отзыв согласия закрывает выдачу до projection, затем оставляет одного кандидата;
повторное согласие и пересборка возвращают двух. Доставка событий отдельно
проверена: 8 ACK, без pending, retry и quarantine.

Для повторения сначала запустить API, matching и agents на одной **свежей** БД
`comit_profile_acceptance_<суффикс>` на loopback PostgreSQL 25433, с одинаковыми
синтетическими auth/service credentials и уже скачанным pinned Harrier. Сценарий
сам применяет явные миграции и запускает реальные workers, но не поднимает
серверы и не очищает данные:

```bash
PROFILE_ACCEPTANCE_DATABASE_URL="$DATABASE_URL" \
PYTHONPATH=services/matching-engine/src:services/agents-service/src \
  services/matching-engine/.venv/bin/python tests/candidate_profile_acceptance.py
```

Порты по умолчанию API 39580, matching 39581, agents 39584; переопределяются через
`PROFILE_ACCEPTANCE_API_URL`, `PROFILE_ACCEPTANCE_MATCHING_URL` и
`PROFILE_ACCEPTANCE_AGENTS_URL`. `AUTH_ALLOWED_ORIGINS` должен включать
`PROFILE_ACCEPTANCE_ORIGIN` (по умолчанию `http://127.0.0.1:39580`). Auth API
требует валидную конфигурацию SMTP, однако mail worker для этой проверки не
запускается. Результат без секретов сохраняется в игнорируемом
`tests/artifacts/candidate-profile-acceptance.json`.

Полный backend-путь до main top-k замкнут. Future verification producers и их
подключение к evidence/outbox остаются отдельным контуром. UI, production deploy,
реальная доставка email в этом прогоне и качество обученного ML-реранкера здесь
не проверяются.

## Добровольное обучение моделей

`consent.training` отделено от `processing` и `pool`. По умолчанию false, включая
прежние профили. Opt-in требует `training=true, training_version="training-v1"`
при действующих processing/pool. Сервер сохраняет отдельное training_accepted_at.
В GET возвращаются training, training_version (или null), training_accepted_at
(или null). Обычная правка fields без consent сохраняет выбор и время; передача
consent без training безопасно отключает обучение. Для отзыва передайте
`{processing:true,pool:true,version:"profile-v1",training:false}` без training_version.

В пуле, витрине и обычном main/rerank обучение не является условием допуска.
Учебные features, выгрузка, shadow/live preference fit, активная модель и research
hypothesis pairs требуют отдельного разрешения. Авторитетная source-запись
проверяется до доставки в matching; source locks удерживаются при export/fit.
Отзыв удаляет feature/model/hypothesis payload при доставке или preference
reconciliation, а повторное согласие не восстанавливает отозванные исторические
features. Копии экспортов контролирует оператор.

Тексты: [тестовые документы](legal/README.md), [согласие](legal/processing-consent.md),
[добровольное обучение](legal/training-consent.md). UI в этот проход не входит.

## Восстановление проекций предметных тестов

Profile worker при старте обрабатывает не более 100 профилей с accepted предметным
результатом без маркера `external:testing_mapping:v1`. Под обычными блокировками
account/profile он восстанавливает coordinate evidence, сравнивает точный snapshot,
и лишь при изменении увеличивает source revision и ставит новую проекцию в outbox.
Повторный старт не меняет отмеченные профили; маркер сохраняется при редактировании.
Source самооценка остаётся прежней. Проекция не выполняется в обход worker/consent.

Для большего накопленного объёма оператор может повторять
`pnpm --filter @comit/api profile:worker --backfill-only` с штатным окружением,
пока очередной проход не найдёт необработанных профилей. Это ограниченный проход,
не сканирование на каждом polling cycle. В рамках разработки live-backfill не запускался.
