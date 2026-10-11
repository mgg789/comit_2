# Проверка кандидатского backend, 7 октября 2026

Замечания проверены по реальному коду и HTTP-сценариям. Указанный в запросе
checkout `29d6/comit_2` находится на detached `d47c86c` и содержит чужие
незавершённые ML-изменения; его не переключали и не изменяли. Проверка и дополнения
выполнены в чистом checkout `qgramm-chat/comit_2`, ветка
`feat/api-candidate-privacy-audit`, исходный актуальный dev — `d3ae471`.

## Результат по шести замечаниям

| Замечание | Текущее состояние и первичные источники | Практическая проверка |
|---|---|---|
| Нет входа соискателя | [auth routes](../apps/api/src/routes/auth.ts), [sessions](../apps/api/src/auth/session.ts): email OTP, JWT, DB session, роль, отзыв | Реальный HTTP OTP через защищённый тестовый mailbox, без SMTP; API auth/store tests |
| Нет собственного профиля | [profile routes](../apps/api/src/routes/candidate-profile.ts), [store](../apps/api/src/profiles/store.ts): GET/PUT/PATCH, draft, CAS, владелец из сессии | Реальные source→projection→matching; API проверки чужой роли/владельца/origin, CAS и запрета поддельных proofs |
| Заявленный грейд не допускается | [models](../services/matching-engine/src/matching_engine/models.py): approved ИЛИ self_reported; старое имя checklist key approved_grade_result сохранено для совместимости | Пять заявленных профилей в пуле, top-2 с grade_status=self_reported и confidence=0.6 |
| Нельзя отозвать согласия | PATCH собственного профиля; source consent watermark и exact outbox | До доставки revoke выдача/PDF блокируются; после пересборки top-2→1, повторное разрешение возвращает top-2 |
| Нет собственного PDF | [account routes](../apps/api/src/routes/accounts.ts): /candidate/resume.pdf с ID из сессии | Реальный приватный PDF содержит контакты владельца; employer JWT получает 401; PDF работодателя до принятия редактируется |
| Нет кандидатского пути после Like и чатов | [recruitment](../apps/api/src/routes/recruitment.ts), [chat](../apps/api/src/routes/chat.ts), [QGramm](chat.md) | Реальные приглашение/принятие/контакты, два собеседования, оффер/отказ, события; настоящие QGramm сообщения/файлы и проверка шифрования после restart |

Эти шесть механизмов уже были в актуальном dev; повторная реализация и перенос
поверх грязного старого checkout не требуются. Источник несоответствия — состояние
checkout, а не отсутствие пользовательских API в текущей ветке.

## Устранённый дополнительный пробел

Ранее processing/pool одновременно разрешали учебные features, экспорт и обучение.
По прямому решению пользователя добавлено отдельное добровольное training-v1,
по умолчанию false, с отдельным server timestamp. Оно проверяется при capture,
export, shadow/live fit, применении preference model и hypothesis comparisons.
Отказ сохраняет обычный профиль/пул/main top-k. Source-only отзыв блокирует export
и активную preference model; delivery/reconcile удаляет учебные payload.
Миграция API005 не включает прежние аккаунты в обучение.

Тексты согласия и политики: [карта тестовых документов](legal/README.md).
Реквизиты оператора/размещения добавляются по отдельному решению пользователя; это
тестовые тексты хакатона, подготовка к промышленной публикации — отдельный этап.

## Воспроизведение

Все DB/HTTP проверки выполняются только на отдельных синтетических БД loopback
PostgreSQL25433, без изменения production, SMTP/LCT или чужих контейнеров.
`tests/candidate_profile_acceptance.py` требует новую пустую БД
`comit_profile_acceptance*`; затем `tests/candidate_privacy_acceptance.py` выполняет
проверку PDF/обучения на этой же завершённой фикстуре и с теми же env.
Recruitment использует отдельную БД `comit_profile_acceptance_recruitment*`.

```sh
pnpm typecheck
pnpm build
# PROFILE_ACCEPTANCE_*: отдельные DB и URL API/matching/agents, одинаковые для двух команд.
python tests/candidate_profile_acceptance.py
PYTHONPATH=services/matching-engine/src python tests/candidate_privacy_acceptance.py
python tests/recruitment_acceptance.py
# PIPELINE_TEST_DATABASE_URL: отдельная тестовая БД; suite очищает её таблицы.
PYTHONPATH=services/matching-engine/src python -m pytest services/matching-engine/tests -q
# *_TEST_DATABASE_URL и CHAT_TEST_*: отдельная БД и настоящий QGramm.
pnpm --filter @comit/api test
# API/MATCHING/AGENTS/TESTING/INTEGRATION/WEB_PORT: порты запущенного локального стенда.
make smoke
```

Проверки выдачи используют настоящий локальный Harrier и реальные workers.
Числовые unit/integration tests имеют свои явные test encoders; они не подменяют
результат HTTP-приёмки. Изолированные проверки QGramm подтверждают file round-trip,
durable idempotency, encrypted storage и restart.

## Границы

Самостоятельные API удаления кандидатского и работодательского аккаунтов реализованы:
до удаления API-графа очищаются связанные комнаты, сообщения и файлы QGramm.
Ошибки очистки блокируют удаление аккаунта; повторы идемпотентны. Отзыв согласия
и удаление аккаунта не равны физическому уничтожению всех экспортов и резервных копий.
Перед публичным запуском нужны
операторский процесс удаления экспортов/резервных копий, реквизиты и договоры
обработчиков, утверждение сроков хранения и юридическая проверка. UI для этого — тоже следующий этап.
Реальная SMTP-доставка, production и внешний DeepSeek проверяются отдельными проходами.
Независимый inspector не запустился: лимит агентских потоков; ревью выполнено
в основном потоке. Никаких production-сервисов/секретов не меняли.

## Зафиксированные результаты

- `pnpm typecheck`, `pnpm build`, scoped `ruff check`, `git diff --check`: passed.
- Полный matching pytest с выделенной PostgreSQL: **1172 passed, 3 skipped**
  (опциональные тесты отдельных historical acceptance datasets не настроены).
- Полный API suite с PostgreSQL и настоящим QGramm: **47 passed, 1 skipped**
  (opt-in hypothesis gateway DB не была задана в полном прогоне);
  затем пропущенный hypothesis test запущен отдельно: **1 passed**, итого
  все **48 API-тестов проверены**.
- Profile HTTP: **8 сценариев**, privacy HTTP: **5**, recruitment HTTP: **7**,
  все passed; реальные Harrier/PDF, processing/pool/training revocation и top-k.
- Финальные tests learning/hypothesis после записи training stamps: **17 passed**;
  API profile/store после последнего schema/SQL guard: **4 passed**.
- QGramm restart/files/encrypted storage/durable idempotency: passed.
- Smoke: **14 проверок passed** на собственных loopback-портах39780–39788.

Первый широкий прогон выявил несовместимость synthetic consent fixture с новым
контрактом отзыва; контракт исправлен без ослабления gates. Последующий focused
прогон получил один временный `worker.process_once()==false` при подготовке
hypothesis fixture; отдельный повтор и полный финальный набор прошли.
API migration005 была усилена до первого коммита; после изменения checksum
проверки выполнялись на новой выделенной DB, старые checksum не переписывали.

Реализация и тестовые тексты: checkpoint `65d4626`. Эта проверка относится к
актуальному dev и локальному синтетическому стенду; она не меняет фактическое
состояние старого detached checkout и не означает production deployment.
