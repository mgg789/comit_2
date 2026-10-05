# Аккаунты и вход по email-коду

Один нормализованный email соответствует **одному аккаунту и одной неизменяемой
роли**: `candidate` или `employer`. Контуры входа раздельные. Регистрация и вход
используют один протокол без пароля; второй аккаунт с другой ролью не создаётся.
UI в этот проход не входит.

## Протокол

Пути ниже указываются относительно API-core; nginx добавляет `/api`.

| Метод и путь | Вход | Результат |
|---|---|---|
| POST `/auth/{role}/code` | `{email}` | 202: challenge_id, expires_at, resend_after |
| POST `/auth/{role}/verify` | `{email,challenge_id,code,consent?}` | AccountView, JWT, expires_at и role cookie |
| GET `/auth/{role}/me` | JWT/cookie | Свой аккаунт |
| POST `/auth/{role}/logout` | JWT/cookie | Отзыв текущей сессии, очистка cookie |
| POST `/auth/{role}/logout-all` | JWT/cookie | Отзыв всех сессий своего аккаунта |

Для первого аккаунта verify требует
`consent: {processing:true,version:"account-v1"}`; действующая версия задаётся
`ACCOUNT_CONSENT_VERSION`. Время принятия сохраняется сервером. При отсутствии
согласия вернётся `409 consent_required`, код можно предъявить снова с согласием.
Вход существующего аккаунта не требует повторного согласия. Это согласие на данные
аккаунта: отдельное согласие на обработку/публикацию профиля остаётся в candidate
pipeline. Создание аккаунта само по себе не публикует человека в пул.

Первый валидный verify создаёт аккаунт; следующие создают сессию того же UUID.
`409 role_mismatch` после проверки кода означает, что email уже принадлежит другой
роли. Роль не меняется. OTP живёт 10 минут, допускает пять неверных попыток;
успешное предъявление одноразовое, новый запрос отменяет предыдущий код всех ролей.
На email: интервал 60 секунд, до пяти выпусков за 15 минут. На IP: 20 выпусков и
60 проверок за 15 минут. Ограничения общие для обеих ролей и атомарны в PostgreSQL.

Email обрезается по краям и приводится к нижнему регистру; домен канонизируется
через IDNA. Quoted/международные local parts не поддерживаются. Адрес и роль не
выдаются публичным подбором; receipt не раскрывает наличие аккаунта. `202` означает
постановку отправки, не доставку письма в почтовый ящик.

## Сессия и границы доступа

JWT подписан HS256, алгоритм строго проверяется; claims: sub, sid, roles,
realm_access.roles, email_verified, iss, aud, iat, exp. В JWT нет email/контактов.
Сессия фиксированная, семь суток; хранится hash случайного 256-битного sid.
Каждый запрос проверяет подпись, роль, issuer/audience/kid и актуальную запись БД.
Logout немедленно отзывает также ранее выданный Bearer JWT.

Cookie: `__Host-comit_candidate_session` или `__Host-comit_employer_session`,
HttpOnly, Secure, SameSite=Lax, Path=/, без Domain. Для локального HTTP при
`AUTH_COOKIE_SECURE=false` используется имя без `__Host-`. Production запрещает
небезопасные cookie. Bearer JWT поддержан для API-клиентов и имеет приоритет:
невалидный Bearer не подменяется рабочей cookie. В браузере предпочтительна cookie,
не хранение JWT в localStorage.

Code/verify требуют Origin из `AUTH_ALLOWED_ORIGINS`. Любые cookie mutations также
требуют этот Origin; Bearer-клиент может опустить Origin, но чужой Origin отклоняется.
CORS для сторонних origins не включён: UI и API предполагаются на одном origin.
`TRUST_PROXY` принимает только явно заданные IP/CIDR; без него X-Forwarded-For не
влияет на лимиты. За nginx задать конкретную доверенную прокси-сеть.

Работодатель получает `/employer/vacancy-drafts`, `/employer/agents/*` — тот же
editorial API, но `X-Owner-ID` сервер устанавливает из account.id. Заголовок клиента
не влияет на владельца. Соискатель получает `/candidate/pipeline` и собственный
`/candidate/resume.pdf`, candidate_id берётся из account.id. Произвольный чужой ID
не принимается. Browser endpoints не позволяют подтверждать grade/evidence через
сырой intake; trusted upstream передаёт уже проверенные данные по internal API.

`/internal/*` по-прежнему требует сервисные токены; JWT не заменяет их.
Существующие legacy candidate/owner UUID автоматически не привязываются по email.
Для новых записей upstream использует account.id; связывание старых проекций —
отдельная контролируемая миграция после проверки владельца, вне данного прохода.

## SMTP и хранение

Используется тот же сервер, что у LCT: `mail.droidje.com`, обычно 587/STARTTLS;
465 использует implicit TLS. Сертификат проверяется, plaintext fallback отсутствует,
socket timeout ограничен. Реализация LCT и настройки его SMTP не изменяются.
TLS соответствует [протоколу Nodemailer](https://nodemailer.com/smtp).

При отсутствии прямого доступа SMTP_HOST/PORT могут указывать на управляемый
SMTP-туннель; SMTP_TLS_SERVERNAME остаётся именем сертификата сервера. Туннель
не создаётся приложением или compose автоматически.

SMTP_USER/SMTP_PASSWORD передаются через env; отдельный From — Comit. Отправка идёт
через durable PostgreSQL outbox: leases, ограниченные попытки, backoff и проверка
актуальности challenge перед отправкой. Постоянный SMTP reject завершает job сразу;
временные ошибки повторяются до пяти попыток. При сбое auth не выдаёт фиктивный код.
SMTP ACK означает принятие сервером. Crash после SMTP до ACK БД может повторить
письмо с тем же кодом: delivery at least once, verify at most once.

OTP хранится как HMAC с серверным ключом; mail payload временно зашифрован AES-GCM
с отдельным purpose key. После доставки/истечения/исчерпания попыток payload
удаляется. Код, JWT, пароли и raw SMTP errors не пишутся в логи/ответы диагностики.
Worker также удаляет OTP challenges и rate-limit events старше суток; сессии —
через 30 дней после истечения. Аккаунты эта уборка не удаляет.
Миграции API-core применяются явно с checksum и advisory lock; serve их не запускает.

AUTH_KEY_ID помечает текущую версию JWT. Ротация AUTH_SECRET/JWT_SECRET инвалидирует
старые JWT и OTP; pending outbox старого ключа не может быть отправлен. Перед
ротацией остановить только Comit mail worker, завершить/отменить его pending jobs,
отозвать сессии и затем поменять env. Это операционный процесс, не изменение LCT.

## Запуск и проверка

```sh
pnpm --filter @comit/api auth:migrate
pnpm --filter @comit/api dev
pnpm --filter @comit/api auth:mail-worker
```

Для контейнеров opt-in `infra/docker-compose.auth.yml` после базового compose и
нужных pipeline/agents overlays. Он добавляет auth migration/mail worker, без
нового SMTP сервера. DATABASE_URL указывает только на Comit PostgreSQL.

Env: AUTH_SECRET (либо JWT_SECRET), AUTH_ALLOWED_ORIGINS, AUTH_COOKIE_SECURE,
AUTH_KEY_ID, ACCOUNT_CONSENT_VERSION, SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS_SERVERNAME.
Без auth-конфигурации login endpoints возвращают 503; остальные internal контуры
продолжают работать. Без SMTP-конфигурации code возвращает 503.

```sh
AUTH_TEST_DATABASE_URL=... AUTH_ROUTE_TEST_DATABASE_URL=... pnpm --filter @comit/api test
pnpm -r typecheck
pnpm -r build
./tests/smoke.sh
```

Обе тестовые БД отдельные и одноразовые: тесты очищают только собственные auth
таблицы. Module/HTTP проверки, SMTP-протокол, реальная доставка и production —
разные контуры, результаты не заменяют друг друга.

См. также: [API](api.md), [данные](data.md), [agents](agents.md),
[контекст](../context/40-architecture.md).
