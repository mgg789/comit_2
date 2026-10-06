# Контур после like

Уточнение Mike от 06.10.2026: **like → saved → приглашение → предложение
соискателю → принятие → контакты, собеседования, оффер или отказ**.
Будущий внутренний чат разрешён уже на стадии приглашения, до раскрытия
контактов. В этом проходе чат и UI отсутствуют; API честно возвращает
`chat_available:false`. Никаких fake chat endpoints или комнат нет.

## Владелец данных и границы

`api-core` хранит hiring aggregate пары owner/vacancy/candidate. Matching
продолжает владеть like/saved/attractive и их историей: API не меняет score,
вектора или правило N>K. Работодатель читает saved и список recruitment;
по candidate_id объединяет их, чтобы отличить ещё не обработанных кандидатов,
приглашённых и завершённые процессы. Историческое like не удаляется при отказе;
attractive продолжает исключать эту пару из комбайна. Новые приглашения для
уже существующей пары запрещены, включая завершённый процесс. Явное
возобновление — следующий отдельный контракт, не автоматический N>K.

Для отправки требуется принадлежащая работодателю опубликованная вакансия,
saved-членство, актуальный liked exposure и source/profile projection ACK.
Matching предоставляет точечный owner-scoped `GET /saved/{candidate_id}`.
При создании API держит account/profile locks до commit и повторно проверяет
processing/pool consent и точный ACK. Приглашение фиксирует разрешённый снимок
и условия; дальнейшее изменение вакансии или снятие pool consent не отзывают
уже отправленное приглашение. Общей распределённой транзакции с engines нет.
Saved — durable grant: в данном контуре операции unlike/delete отсутствуют.

Компания пока задаётся работодателем в приглашении и не считается проверенной.
Описание и обязательная зарплата от–до видны соискателю до принятия. Если salary
опущена, берётся диапазон опубликованной вакансии. Название, город, daily и
формат также фиксируются; редактирование предложения после отправки не реализовано.

## Состояния и действия

| Состояние | Работодатель | Соискатель |
|---|---|---|
| sent / viewed | Отклонить | Просмотреть, принять или отклонить |
| accepted | Предложить/отменить/завершить собес, отправить оффер, отклонить | Принять/отклонить время, выйти из процесса |
| offer_sent | Собеседования остаются доступны, отклонить | Собеседования, принять/отклонить оффер, выйти |
| hired / declined / rejected / offer_declined / withdrawn | Новые переходы запрещены | Новые переходы запрещены |

Ответ возвращает вычисленные `allowed_actions`, отдельные interviews/offers,
revision и историю результатов. Общие карточки, процесс и events не содержат
контактов. Только `GET /employer/recruitment/{id}/contacts` открывает текущие
имя/email/phone после принятия (accepted/offer_sent/hired), своему работодателю,
при действующем processing consent. Pool consent управляет будущим поиском:
его отзыв после приглашения не блокирует существующий процесс. Отзыв processing
немедленно закрывает контакты и новые положительные действия; отказ/выход/отмена
и доступ к собственной истории сохраняются. Все ответы `Cache-Control:no-store`.

Отказ до отправки приглашения создаёт employer-only rejected-запись с
`terms:null` и `invited_at:null`: соискатель не видит попадания в shortlist,
этого отказа, его причины или события. Отказы после приглашения требуют причину,
сохраняют видимую обеим сторонам `closure` и закрывают процесс. Внутренний
короткий отказ не выдаётся за отправленное приглашение.

## Собеседования

Максимум один **проведённый** technical и один general на процесс. Для каждого
типа существует только один proposed/scheduled/completed slot; DB unique index
и process lock защищают лимит при двух вкладках. Отмена/отклонение времени не
расходуют слот, новое назначение создаёт отдельную запись, история сохраняется.
Перенос: отменить старую запись, предложить новую. Другие виды собеседований
и повторные раунды в этой версии не поддерживаются.

Работодатель задаёт абсолютное starts_at, IANA time_zone, длительность1–480мин,
remote/onsite и URL/адрес. Соискатель подтверждает либо отклоняет время.
Предложение и его принятие допускаются только до starts_at. Прошедший proposal
можно отменить. Проведение отмечает работодатель не раньше starts_at; outcome
passed/failed/neutral и feedback обязательны. Failed не означает автоматический
отказ. После двух completed остаются оффер или отказ. Meetings не создают
звонок, календарный event или отправку на внешний URL.

## Оффер, время и идемпотентность

Оффер доступен сразу после принятия приглашения, независимо от собеседований.
Нужны точная зарплата в целых RUB>0, описание условий и expires_at. Одновременно
один sent offer. Принятие → hired; отклонение → offer_declined. Завершение
процесса отменяет незавершённые встречи и закрывает outstanding offer, сохраняя
записи. Принятие оффера не меняет бизнес-статус вакансии.

При `expires_at <= clock_timestamp()` оффер expired, процесс возвращается в
accepted и можно отправить новый. Expiry фиксируется лениво на get/list и перед
action, под process lock, отдельным revision/event. Позднее принятие запрещено
также SQL-предикатом непосредственно при записи. Invites без автоматического
срока, no-show/SLA/напоминания в этот проход не входят.

Каждый action требует expected_revision и UUID idempotency_key. Старый revision
даёт409. Ключ имеет область actor_id + key, fingerprint включает путь и весь
command: конфликтующий payload даёт409. Повтор возвращает исходную contact-free
receipt даже после смены источника или недоступности engines; для текущего
состояния клиент отдельно делает GET. Один process на owner/vacancy/candidate.

## События и проверка

Изменение process, receipt и event пишутся одной транзакцией. Event содержит
только id, глобальный sequence, process_id, revision, type, state и created_at;
нет контактов, условий, причин, meeting URL или model output. Participant polling
возвращает only-own events, `next_sequence` и `has_more`; потребитель дедуплицирует
event_id. Вставки сериализованы до commit, чтобы курсор не пропустил lower
uncommitted sequence. События не удаляются автоматически. Внешняя email-доставка,
consumer leases, WebSocket, чат и система персональных предпочтений — будущие
потребители, в этом проходе их успешная доставка не заявляется.

Запуск API SQL: `pnpm --filter @comit/api auth:migrate`; сервер не мигрирует сам.
Тесты API: `pnpm exec tsx --test --test-concurrency=1 tests/*.test.ts` из apps/api
с явными отдельными AUTH/PROFILE/RECRUITMENT test database env.
Реальный HTTP: `python tests/recruitment_acceptance.py` с
RECRUITMENT_ACCEPTANCE_DATABASE_URL и локальными API/MATCHING/AGENTS URLs.
Приёмка требует пустую отдельную `comit_profile_acceptance_recruitment*` БД и
использует настоящие auth/projection/Harrier/PDF/main/R0/like и обе hiring ветки.

См. [API](api.md), [данные](data.md), [контекст](../context/30-domain-model.md).

## Приёмка 06.10.2026

На свежей loopback БД `tests/recruitment_acceptance.py` прошёл полный HTTP-путь
трёх synthetic accounts через encrypted email-code mailbox, source projection,
настоящий локальный pinned Harrier640/PDF, опубликованную вакансию, main top-k3,
явный R₀ main_fallback и три like. Далее проверены две invitation/accept ветки:
немедленный оффер→hired и две реально согласованные/проведённые по server clock
встречи→оффер→declined. Третий saved отклонён до приглашения и невидим кандидату.
Проверены чужой owner404, contact gate403→200, durable own events, no-chat,
pool-only withdrawal с сохранением доступа и processing withdrawal с запретом
контактов409. SMTP и внешние модели не вызывались.

Полный API набор —42 passed с PostgreSQL без пропусков; matching —689 passed с
PostgreSQL без пропусков. `pnpm typecheck`, `pnpm build`, scoped Ruff, smoke14,
code review и final inspection зелёные. Результаты и ограничения:
[recruitment-acceptance.json](../data/validation/recruitment-acceptance.json).
