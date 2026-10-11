# Внешняя верификация кандидата

Контур внешней верификации отделяет provider facts от identity и решений matching. Кандидат вызывает API от своей authenticated candidate-сессии; account id берётся только из session и никогда не передаётся в `services/verification-engine`.

## Провайдеры

`services/verification-engine` (внутренний порт `8105`; на хосте только loopback `127.0.0.1:${VERIFICATION_PORT:-28105}`) предоставляет:

- `POST /verify/fsp` — demo origin `mock` обращается к существующему integration-engine FSP registry. `live_unbound` возвращает `pending` с `fsp_origin_unbound`: реальный FSP identity binding не выдумывается.
- Demo authorization flow использует только fixture identities `fsp-001`/`fsp-002`: code short-lived и одноразовый, state связан с candidate account и выбранным fixture на стороне API, callback allowlist фиксирован. Token exchange требует отдельный `FSP_DEMO_INTERNAL_TOKEN`; registry bulk импортирует stable achievement claim IDs с immutable `provider_version`. Импортированный achievement получает `confirmed` только внутри synthetic demo registry, отдельный FSP binding остаётся `partial`; карточка помечает это как `Демо-реестр ФСП`, а ownership реального FSP аккаунта остаётся `false`.
- `POST /verify/repository` — разрешены публичные GitHub, GitLab, Bitbucket, GitVerse и SourceCraft URL. Connector выполняет bounded shallow bare clone, `ls-tree` реального Git дерева и проверяет непустой blob; HTML existence не принимается. Поле `authorship_proven` всегда `false`; contribution narrative остаётся заявленным текстом кандидата и попадает в resume только вместе с этим статусом.
- `POST /verify/hh` — read-only `GET /resumes/{id}` с OAuth credential только из provider environment. Без `HH_API_TOKEN` отвечает `503 hh_token_unavailable`; токен не логируется и не заменяется demo-identity. Ответ может сопоставиться только с текущим experience claim и не является ownership proof. Контракт HH описан в [официальной документации API](https://api.hh.ru/openapi/redoc).

Provider response не содержит candidate id. API вычисляет SHA-256 от canonical input, сохраняет его в `candidate_profile_evidence` и атомарно повышает source revision, чтобы matching получил новый snapshot через существующий outbox. При изменении исходного profile input hash меняется, а старое подтверждение становится `self_reported`.

## API-контуры

Демо-привязка начинается с `POST /candidate/profile/fsp/demo/authorize`: `expected_revision` и `demo_fixture_id` выбирают синтетический профиль реестра. Ответ содержит `authorize_url`, `state`, срок действия и callback. Будущий клиент открывает этот URL как отдельный контур ФСП; реестр имитирует вход выбранного демонстрационного пользователя и выдаёт одноразовый код. `POST /candidate/profile/fsp/demo/callback` принимает `expected_revision`, `state` и `code`, проверяет владельца состояния, обменивает код с реестром и атомарно сохраняет ФСП ID и все достижения. Повторный импорт сохраняет идентификаторы достижений. Для смены уже связанного ФСП профиля сначала требуется отвязка; при отвязке импортированные подтверждения перестают действовать.

`POST /candidate/profile/verifications/fsp`, `/repository` и `/hh` требуют `expected_revision`, а ownership проверяется candidate session. FSP evidence привязан к текущему `fields.fsp_id`; repository evidence привязан к `claim_kind`/`claim_id` и canonical URL/description. Любое изменение или удаление claim отбрасывает старую запись при следующей projection. Provider token между API и verification-engine обязателен и проверяется fail-closed через `X-Internal-Token`.

`POST /candidate/profile/verification-documents` принимает bounded canonical base64 payload для `claim_kind`/`claim_id`, сохраняет AES-256-GCM ciphertext при наличии `VERIFICATION_DOCUMENT_KEY` и возвращает только metadata со статусом `pending`; upload сам по себе не подтверждает документ. GET списка и download доступны только владельцу.

Migration `apps/api/migrations/007_candidate_verification.sql` добавляет encrypted document storage; API006 остаётся зарезервированной operations migration. Migration runner применяет 007 checksum-safe способом вместе с остальными API migrations.

Matching projection переносит только allowlisted FSP/repository/HH fields в public profile и PDF: synthetic FSP rank/rating/events, repository URL/nonempty fact/contribution narrative и ограниченные HH resume/experience facts. Contacts, provenance, account identity и authorship assertion не попадают в employer projection.

## Проверка

```bash
PYTHONPATH=services/verification-engine/src pytest -q services/verification-engine/tests
pnpm --filter @comit/shared typecheck
pnpm --filter @comit/api typecheck
pnpm --filter @comit/api exec tsx --test tests/candidate-verification-schema.test.ts
```

PostgreSQL-проверки используют отдельную синтетическую БД через `VERIFICATION_TEST_DATABASE_URL`. Сквозной HTTP-сценарий `tests/fsp_demo_acceptance.py` использует тот же изолированный стенд и переменные, что [контур ввода профиля](../tests/candidate_profile_acceptance.py): реальные OTP-сессии, demo state/code exchange, импорт всех достижений, outbox, Harrier 640, карточку и PDF. Проверка прошла для двух достижений: чужой аккаунт и повтор кода отклоняются, повторный импорт не создаёт дублей, PDF содержит отметку демо-реестра и скрывает контакты.

Контейнер проверен отдельно: health, отказ без сервисного токена, обращение к демо-ФСП, явная недоступность HH без токена и shallow Git-проверка реального публичного репозитория. Контейнер содержит Git и CA-сертификаты; Git-процесс ограничен временем, размером временного каталога и объёмом вывода.

В compose API направляет запросы через `VERIFICATION_ENGINE_URL=http://verification-engine:8105`, а verification-engine обращается к `INTEGRATION_ENGINE_URL=http://integration-engine:8103`. Без service token, `FSP_DEMO_INTERNAL_TOKEN`, `INTEGRATION_ENGINE_URL`, `HH_API_TOKEN` или `VERIFICATION_DOCUMENT_KEY` соответствующие paths безопасно деградируют в явные 503; demo mock использует только synthetic registry integration-engine.
