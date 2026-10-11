# Профиль компании и системный чат Comit

Профиль компании хранится отдельно от приглашений в `employer_profiles`. Работодатель задаёт название в настройках или при первом входе; последующие приглашения используют серверное название. Существующие `terms.company_name` остаются снимком на момент приглашения. Миграция `014_employer_system_inbox.sql` переносит последнее валидное название из реального приглашения; новые аккаунты получают незаполненный профиль.

| Метод и путь API | Контракт |
|---|---|
| `GET /api/employer/profile` | `{company_name: string|null, revision, updated_at: string|null}`; незаполненный профиль имеет revision 0 |
| `PATCH /api/employer/profile` | `{expected_revision, company_name}`; trim, длина 1–200; конфликт ревизии — 409 |
| `GET /api/{role}/system-chat?after_sequence=0&limit=50` | `{id:"comit-system",title:"Comit",items,next_sequence,read_sequence,unread_count,has_more}` |
| `POST /api/{role}/system-chat/read` | `{read_sequence}` → `{read_sequence,unread_count}` |
| `POST /api/{role}/system-chat/messages` | `{idempotency_key: uuid, text: 1..2000}` → своё сообщение `support.user_message`; отправка закрывает доставку до текущего момента, поэтому unread растёт только от ответов Comit |
| `GET /api/{role}/chat-unread` | `{system_unread_count,recruitment_unread_count,total_unread_count}` |

Роли: `candidate` и `employer`. Владельца определяет действующая ролевая сессия; клиент не передаёт `account_id` или `owner_id`. Ответы имеют `Cache-Control: no-store`; cookie-мутации требуют разрешённый Origin. Несовпадение роли возвращает 401. Контракты описаны в OpenAPI и `packages/shared`, клиент — в `packages/api-client`.

Элемент inbox: `{id, sequence, type, text, created_at, target}`. `sequence` локален для аккаунта, время — UTC. `target` содержит внутренний путь, тип цели и опциональный UUID; это подсказка навигации, доступ к целевой записи проверяется отдельно. Клиент дополнительно проверяет разрешённые маршруты. В сообщениях нет контактов, приватного feedback, причин отказа или содержания переписки.

Системный чат доступен без приглашений и без QGramm. Первое приветствие записывается один раз с ключом `welcome:v1`. Помимо платформенных уведомлений аккаунт может писать в чат сам (`support.user_message`), а оператор отвечает от имени Comit (`support.reply`) — это живая служба поддержки, см. [support-console.md](support-console.md). Polling использует `next_sequence` и `has_more`; страницы содержат максимум 100 элементов. Последовательность чтения монотонна и ограничена самым большим номером, действительно возвращённым этому аккаунту. Попытка прочитать будущие неполученные сообщения возвращает `409 system_read_not_delivered`.

`SystemNotificationsStore.append(client, accountId, key, type, text, target)` добавляет уведомление в существующую транзакцию доменной операции; rollback откатывает и уведомление. Повтор ключа для одного аккаунта возвращает тот же ID и sequence. `publish(...)` открывает самостоятельную транзакцию. Блокировка inbox сериализует публикации и read receipts, поэтому параллельные повторы не увеличивают непрочитанное.

При чтении candidate inbox выполняется ограниченная сверка реальных новых `feed_pipeline.decisions` со значением `like` и ежемесячных заданий в `notification_mail_outbox`. Применяются действующие processing/pool consent и cutover установки миграции 014; исторические события не переотправляются. Одна сверка переносит максимум 100 элементов каждого источника, следующие страницы/опросы продолжают сверку. Interview-mail отдельно не копируется: соответствующие уведомления публикует recruitment-транзакция. Отсутствующие matching/mail таблицы допустимы.

Глобальный badge берёт все приглашённые процессы участника с текущим согласием обработки, независимо от страницы списка и статуса вакансии. Приватный сервис возвращает только unread metadata пакетами по 100; consent и process locks удерживаются до конца запроса metadata. Проверяются точный набор ID, уникальность, целые неотрицательные счётчики и ограничение размера ответа. Ошибка любого пакета возвращает `null` для recruitment/total вместо ложного нуля. При отсутствии доступных процессов recruitment unread равен нулю. Системный счётчик остаётся доступен независимо от приватного сервиса.

Проверки: `pnpm --filter @comit/api exec tsx --test tests/system-inbox.test.ts` и `tests/support-console.test.ts` (`SUPPORT_TEST_DATABASE_URL`). PostgreSQL часть требует `SYSTEM_INBOX_TEST_DATABASE_URL` на отдельную одноразовую БД с именем `comit_system_inbox_test` или `hiring_test_<timestamp>_<hex>`: тест пересоздаёт только её схемы. Проверяются CAS и backfill компании, конкурентная дедупликация, rollback, повторное создание store, ACL обеих ролей, отправка своих сообщений и семантика unread, read bounds/monotonicity, актуальные consent, source reconciliation и глобальные 205 чатов.
