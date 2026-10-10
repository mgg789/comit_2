# Общая встречающая витрина

Публичная витрина показывает краткие карточки вакансий и обезличенные карточки
соискателей. Она не даёт доступ к резюме, деталям, контактам или действиям подбора:
переход с карточки открывает выбор роли для входа. Публичные UUID соискателей,
ФИО, evidence, векторы и ссылки на PDF не передаются.

## API и фильтры

`GET /api/public/showcase/vacancies` и `GET /api/public/showcase/candidates`
принимают `q` (до 500 символов), `limit` (1–50, по умолчанию 24), `cursor`,
`specialization` (восемь канонических направлений), `grade`
(`intern/junior/middle/senior`), `stack` (ключ реестра), `sort` (`newest/oldest`).
Фильтры применяются сервером; сортировка идёт по времени публикации и UUID.
Ответ: `{items, limit, has_more, next_cursor}`.

Вакансия содержит только `vacancy_id`, `title`, `company_name` (или null), `city`,
`salary`, `work_types`, `schedule`, `category`, `stacks`, `published_at`.
Компания берётся из объявленных metadata вакансии, никогда из email владельца.
Отсутствующий заявленный грейд остаётся null.

Карточка соискателя: `candidate_id` (необратимый SHA256 alias), `display_name`
(всегда «Специалист»), `city`, `salary`, `category`, `stacks`, `published_at`.
Это краткая публичная классификация без персонального имени, частного источника,
проектов/доказательств или контактов. Метки стека с rejected-статусом не выводятся.
Идентификатор карточки нельзя использовать для чтения защищённого профиля.

## Достоверность и приватность

Matching читает PostgreSQL в repeatable-read/read-only транзакции с statement
timeout 5 секунд. Соискатели попадают в ответ только из текущего ready-пула с
активной схемой и обоими согласиями processing/pool; API дополнительно проверяет
текущий исходный профиль и подтверждённую доставку его ревизии. Отозванные либо
устаревшие записи пропускаются. Само наличие сессии не расширяет публичный DTO.

Вакансии берутся из текущих ready/open-снимков matching; API сверяет владельца,
исходную ревизию и accepted-публикацию с актуальным editorial draft. Closed,
archived, непубликованные и ожидающие lifecycle-перехода карточки скрыты. Витрина
не подменяет отсутствие matching/agents-сервиса пустым «успешным» пулом.

Внутренние read-only endpoints `/internal/public/showcase/{vacancies,candidates}`
требуют service token и не требуют фиктивного X-Owner-ID. Только проверка конкретного
editorial draft использует его реального владельца внутри доверенной сети. Модели
и inference не запускаются; поиск кандидатов использует имеющийся PostgreSQL FTS,
вакансий — буквальный поиск по публичному названию/компании/городу.

Продолжение связано с типом каталога, фильтрами и текущим снимком. API шифрует
внутренний keyset cursor через AES-256-GCM: ключ производен от auth secret с
отдельным назначением `comit-public-cursor-v1`, AAD — тип каталога; случайный nonce
на каждый ответ. Cursor не является полномочием и не раскрывает внутренний UUID.
Изменение источника/фильтров, подмена cursor или смена каталога дают HTTP409 и
требуют начать список заново. Перезапуск с новым auth secret также инвалидирует
старые continuations. Ответы no-store; private-service HTTP-работа имеет общий
15-секундный deadline, размер ответа ограничен 4 MiB.

HTTP503 означает недоступность либо неверный upstream DTO; данные не выдумываются.
Если API исключил уже отозванные карточки, текущая страница может быть пустой при
`has_more:true`: клиент должен продолжить по cursor до непустой страницы или конца.

## Проверка

`apps/api/tests/public-showcase.test.ts`: anonymous allowlist, скрытые identity и
cursor, отсутствие detail/PDF, валидация, отзыв согласия и editorial publication.
`services/matching-engine/tests/test_public_showcase*.py`: service authentication,
filters/date/keyset, буквальные `%/_`, текущие ревизии и lifecycle/consent fences.
Integration suite требует отдельную `PIPELINE_TEST_DATABASE_URL`; fixture очищает
только явно выбранную тестовую БД. Контракт gateway регенерируется командой
`PYTHONPATH=services/matching-engine/src python tests/generate_candidate_contract.py`.
