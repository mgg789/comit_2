# Служба поддержки (support console)

Живой диалог между пользователями и платформой Comit. Пользователи пишут в
системный чат Comit прямо из кабинета; оператор читает и отвечает из отдельной
консоли от имени Comit. Контент лежит в тех же durable-таблицах, что и
уведомления (`system_notifications`), без второго хранилища.

## Пользовательская сторона

- В кабинете кандидата и работодателя чат `Comit` (`/chat/comit-system`)
  получил поле ввода: `POST /api/{role}/system-chat/messages`
  `{idempotency_key: uuid, text: 1..2000}` → созданное уведомление.
- Тип сообщения пользователя — `support.user_message`; собственное сообщение
  закрывает доставку до текущего момента (`read_sequence = delivered`), поэтому
  непрочитанным остаётся только ответ Comit или платформенное уведомление.
- Повтор того же `idempotency_key` возвращает исходное сообщение без дублей.
- Ответы поддержки приходят с типом `support.reply` и увеличивают
  `system_unread_count` как обычные уведомления.

## Консоль оператора

Адрес: `/support/` (тот же web-шлюз). Вход — почта оператора и ключ доступа:

- `SUPPORT_EMAIL` — точный адрес (сравнение constant-time, нормализуется);
- `SUPPORT_ACCESS_KEY` — длинный случайный секрет (≥16 символов).

Оба значения задаются только в env. Пара не задана — консоль
отключена (маршруты не регистрируются); задана наполовину или некорректно
(ключ короче 16 или длиннее 200 символов, невалидный email) — API пишет
предупреждение `support_config_invalid` и тоже не регистрирует маршруты.
В этом случае `/support/` показывает сообщение «Консоль поддержки не настроена
на этом стенде» (ответ 404 на `/api/support/login`). Сессия оператора живёт 7 дней. Дополнительно к глобальному
rate limit действует per-IP троттлинг неудачных входов (10 за 15 минут → 429).

| Метод и путь | Контракт |
|---|---|
| `POST /api/support/login` | `{email, access_key}` → `{operator:{email}, expires_at}` + HttpOnly cookie `comit_support_session` (`__Host-` при HTTPS) |
| `GET /api/support/me` | `{email}` текущего оператора |
| `POST /api/support/logout` | Отзыв сессии и очистка cookie |
| `GET /api/support/conversations?offset&limit` | Страница диалогов: `{items:[{account_id,email,role,last_sequence,last_text,last_created_at,unread_count}], has_more}`, сортировка по свежей активности |
| `GET /api/support/conversations/{account_id}/messages?after_sequence&limit` | Возрастающая страница сообщений диалога (без чужих read-квитанций) |
| `POST /api/support/conversations/{account_id}/messages` | `{idempotency_key, text}` → ответ от имени Comit (`support.reply`) |
| `POST /api/support/conversations/{account_id}/read` | `{read_sequence}` → `{read_sequence, unread_count}` операторская квитанция |

Мутации требуют разрешённый Origin (`AUTH_ALLOWED_ORIGINS`), ответы идут с
`Cache-Control: no-store`. Токен сессии — случайные 256 бит; в БД только
SHA-256 hash (`support_sessions`), logout отзывает немедленно. Операторские
квитанции чтения хранятся в `support_conversation_reads` и монотонны
(миграция `021_support_console.sql`). Unread оператора считают только
сообщения пользователей; отклонённое по границам чтение даёт `422`, чужой
UUID аккаунта — тот же `404`, что и несуществующий.

Чтение диалогов оператором не меняет пользовательские `delivered/read` —
пользователь не получает ложных «прочитано». Диалог можно открыть и для
аккаунта, который ещё ничего не писал: ответ создаст inbox, пользователь
увидит его при следующем входе.

## Экраны

- Список диалогов переиспользует плитки чатов кабинетов (`RecruitmentChatList`);
  счётчик непрочитанных — русский лейбл «N новых сообщений».
- Экран диалога: пузыри (`support.reply` — исходящие справа), polling 2.5 с
  с курсором `next_sequence`, отправка через общий композер, открытие диалога
  отмечает его прочитанным для оператора.
- Стиль — общий тёмный контур `comit-design`, тексты RU, ошибки человека
  читаемыми кодами (`support_login_rejected`, `support_login_throttled`, …).

## Запуск и проверка

```sh
# локально: api с настроенным оператором
SUPPORT_EMAIL=you@example.test SUPPORT_ACCESS_KEY='long-random-secret' pnpm --filter @comit/api dev
pnpm --filter @comit/web-support dev    # http://localhost:5177, /api проксируется на :8080
```

В docker-контурах переменные проходят через compose (`SUPPORT_EMAIL`,
`SUPPORT_ACCESS_KEY` в `.env`); миграция применяется штатным `auth-migrate`.
Тесты: `SUPPORT_TEST_DATABASE_URL=<disposable db> pnpm --filter @comit/api
exec tsx --test tests/support-console.test.ts` — проверяет логин, изоляцию
сессий, счётчики, идемпотентность и отзыв сессии.

См. также: [system-inbox.md](system-inbox.md) — общий чат и правила
последовательностей; [auth.md](auth.md) — origins и cookie-механика.

Курсор прочтения оператора монотонный и не может превышать последний номер сообщения: будущий курсор отклоняется с `409 support_read_not_available`, неизвестный диалог — с `404 account_not_found`. Отклонённая попытка не меняет счётчик непрочитанных.
