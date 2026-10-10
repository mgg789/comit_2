# Управление вакансиями работодателя

Публичные маршруты `/employer/vacancies` используют ту же запись `agents_service.drafts`, что форма и AI-агент. Второго хранилища вакансий, второго расчёта готовности или скоринга нет. Собственник определяется по сессии работодателя, а не по телу запроса или клиентскому `X-Owner-ID`. UUID вакансии и её исходного черновика совпадают.

Во внутреннем gateway API редакторские CRUD-маршруты имеют namespace `/internal/agents/vacancies`, поскольку `/internal/vacancies` принадлежит matching-проекции. Gateway переводит редакторский namespace в `/internal/vacancies` только при отправке напрямую в agents-service. Публичные `/employer/vacancies` остаются прежними. Все методы, включая DELETE, поддержаны; `/internal/vacancies/:id/ready` продолжает обслуживаться matching.

| Метод и путь | Действие |
|---|---|
| `POST /employer/vacancies` | Создать черновик `{fields}` |
| `GET /employer/vacancies` | Страница собственных неархивных записей |
| `GET /employer/vacancies/:draft_id` | Прочитать собственную запись и состояние публикации |
| `PATCH /employer/vacancies/:draft_id` | Правки `{expected_revision, fields}` |
| `POST /employer/vacancies/:draft_id/approve` | Проверить ревизию и получить summary |
| `POST /employer/vacancies/:draft_id/publish` | Подтвердить `{expected_revision, summary_hash}` и поставить публикацию в очередь |
| `POST /employer/vacancies/:draft_id/close` | Закрыть `{expected_revision}` |
| `POST /employer/vacancies/:draft_id/withdraw` | Снять открытую вакансию в черновик `{expected_revision}` |
| `DELETE /employer/vacancies/:draft_id` | Физически удалить черновик или закрытую вакансию `{expected_revision}` |

Создание и редактирование не публикуют автоматически. Для первоначальной публикации и для новой версии нужны существующие approve → summary → publish/Create. Контракт полей остаётся `AgentsDraftFields`; смена owner и произвольные поля не принимаются.

PATCH опубликованной записи переводит её в редактирование с новой ревизией. Ранее принятый matching-снимок остаётся опубликованным до подтверждения новой версии; прямые действия работодателя через исходный черновик требуют актуального состояния `created/accepted`. Старый маршрут `/vacancy-drafts/:id` сохраняет ограничение на редактирование уже опубликованной записи; для ревизии используется новый `/vacancies/:id`.

Снятие (`withdraw`) переводит `open` в `draft`, увеличивает CAS-ревизию и отменяет устаревшие публикационные задания. Сохранённые кандидаты и предыдущие показы ленты остаются. Новую ленту и refill для неактивной вакансии matching не создаёт. Повторная публикация проходит approve → summary → publish; approve неизменённого снятого черновика создаёт новую ревизию, строго новее inactive fence.

Закрытие (`close`) окончательно: запрещены редактирование, approve и publish через любой редакторский маршрут, включая agent tools. Закрытие допускает незавершённую публикацию и инвалидирует её lease; новый durable job содержит `status=closed` и более высокую `source_revision`. Снимок строится из последней опубликованной версии, если она есть.

DELETE разрешён только для `draft` и `closed`. Он удаляет исходную запись, связанные vacancy-agent sessions, сообщения, tool journal и jobs. Ответ совместим с `DraftView`: финальный снимок с `archived=true` и следующей ревизией; дальнейший GET возвращает 404. Withdraw/close сохраняют exact owner-scoped ответ CAS в `vacancy_lifecycle_receipts`; повтор того же expected revision и action возвращает этот ответ. `GET /internal/vacancies/:id/lifecycle/:expected_revision/:action` позволяет прочитать подтверждение без вывода из текущего статуса. Receipts удаляются вместе с вакансией. Сервис хранит только tombstone (`vacancy_id`, owner, source revision, время), без полей вакансии. `GET /internal/vacancies/:id/deletion` возвращает владельцу `{vacancy_id,source_revision,deleted:true}` для восстановления durable операции после неизвестного сетевого результата.

Matching имеет отдельные owner-scoped маршруты: `POST /internal/vacancies/:id/deactivate` с `{owner_id,source_revision,status: "draft" | "closed"}` и `DELETE /internal/vacancies/:id` с `{owner_id,source_revision}`. Они используют общий pipeline lock, монотонный fence и идемпотентный результат. Deactivate убирает ready snapshot и leases, сохраняя saved/exposures. DELETE удаляет весь vacancy-owned graph matching/rerank/feed/preferences/hypotheses/learning и оставляет минимальный tombstone; старые intake и in-flight jobs не могут воскресить запись. Общие кандидаты, registry, модели и artifact receipts не удаляются. Gateway координирует сервисы через HTTP и durable retries; прямых межсервисных SQL-мутаций нет.

Миграции lifecycle: agents `010_vacancy_lifecycle.sql`, matching `016_vacancy_lifecycle.sql`. Их требуется применить перед включением новых операций. API-owned recruitment/chat очищаются в gateway; agents/matching не обращаются к их таблицам.

## Страницы и поиск витрины

`GET /employer/vacancies?limit=50&cursor=<opaque>` использует keyset `(created_at DESC, draft_id DESC)`. Ключи создания неизменяемы, поэтому правки записи не перемещают её между страницами. Ответ: `{items,limit,has_more,next_cursor}`. Cursor привязан к owner, неправильный cursor или сочетание cursor с ненулевым offset возвращают 409. Новые записи перед границей не добавляются в продолжение старого обхода. Для обратной совместимости принимается `offset` до 1 000 000; старый список `/employer/vacancy-drafts` также сохраняется.

В `POST /employer/candidates/search` поле `filters.vacancy_id` принимает canonical UUID собственной открытой опубликованной вакансии. Gateway проверяет исходный `created/accepted`, `business_status=open`, отсутствие архива и matching readiness с тем же owner. Затем передаёт owner из сессии matching-сервису, который применяет фильтры вакансии. Общий поиск без vacancy_id по-прежнему не требует вакансии.

Миграция агента: `004_vacancy_management.sql`. Генератор контрактов: `PYTHONPATH=services/agents-service/src python tests/generate_agents_contract.py`. Изменения DTO внесены через Pydantic, TypeScript/OpenAPI генерируются.

Проверки БД и lifecycle: `PYTHONPATH=services/agents-service/src AGENTS_TEST_DATABASE_URL=<disposable-db> python -m pytest services/agents-service/tests/test_vacancy_management.py services/agents-service/tests/test_store.py services/agents-service/tests/test_api.py -q`.
