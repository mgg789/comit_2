# Проверка витрины, 2026-10-07

Рабочая ветка `feat/matching-candidate-showcase`, исходный dev `a47063f`.
Все данные синтетические, PostgreSQL — отдельные именованные базы на
loopback25433. Production, LCT, SMTP и пользовательский ML worktree не менялись.

| Проверка | Команда / контур | Результат |
|---|---|---|
| Полный matching до уточнения override | `python -m pytest services/matching-engine/tests -q`, PIPELINE_TEST_DATABASE_URL отдельной БД | 1138 passed, 3 skipped |
| Manual likes + Tinder после override | `python -m pytest services/matching-engine/tests/test_showcase_likes_integration.py services/matching-engine/tests/test_feed_integration.py -q` | 19 passed |
| Затронутые engine-модули после override | pytest: search, manual likes, feed policy/integration, live preferences, learning store, pipeline integration, resume | 81 passed, без skips |
| API после override | из apps/api: `pnpm exec tsx --test --test-concurrency=1 tests/*.test.ts`, отдельные *_TEST_DATABASE_URL | 45 passed, 1 skipped |
| Типы всех workspace | `pnpm typecheck` | passed |
| Сборка workspace / последующая API-сборка | `pnpm build`, затем `pnpm --filter @comit/api build` | passed |
| Python lint | `python -m ruff check services/matching-engine/src services/matching-engine/tests/test_showcase* tests/candidate_showcase_acceptance.py` | passed |
| Реальный HTTP / Harrier | `python tests/candidate_showcase_acceptance.py`, SHOWCASE_ACCEPTANCE_DATABASE_URL отдельной пустой БД | 6 сценариев passed, 3 кандидата |
| Smoke | `API_PORT=39790 MATCHING_PORT=39791 TESTING_PORT=39792 INTEGRATION_PORT=39793 AGENTS_PORT=39794 WEB_PORT=39798 SMOKE_TRIES=2 make smoke` | все 14 проверок passed |
| Patch / UTF-8 | `git diff --check`, decode новых source/docs как UTF-8 | passed |

Matching skips — три opt-in проверки authoritative learning consent на
отдельном заранее подготовленном `comit_cards_learning_*`, этот контур не
запускался. API skip — существующая chat/files проверка с внешним QGramm
bridge; чаты в этом проходе не менялись и не перепроверялись.

HTTP проверил source/email auth через реальный encrypted mailbox без SMTP,
публикацию профилей с локальным закреплённым Harrier, гибридный поиск без
вакансии, фильтры, cursor, публичный профиль/PDF, собственную открытую
вакансию, replay лайка, структурированное предупреждение для несовпадения
класса/зарплаты/города, подтверждённый override и приглашение, принятие и
контактный gate, мгновенный отзыв source consent до projection и stale cursor.
API и matching запускались из текущего дерева; nginx использовал собранные
четыре приложения и loopback API. Для testing-engine указан реальный
`BLUEPRINT_DIR=services/testing-engine/blueprints` абсолютным путём.

Проверки PostgreSQL также подтвердили конкурентную уникальность saved,
отсутствие side effects до acknowledgement, устаревший mismatch_token при
новой ревизии вакансии, запрет override source/owner/visited/pending guards,
сохранение реально fitted active preference learner и ограниченный lock wait.

Независимый финальный reviewer/inspector не запустился из-за лимита агентских
потоков; итоговый diff проверен основным агентом. UI, нагрузочная приёмка и
server deployment — следующие этапы. Правила privacy/free-text и исключение
manual реакций из learned-cohort изложены в [контракте](candidate-showcase.md).
