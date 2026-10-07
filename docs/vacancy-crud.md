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
| `DELETE /employer/vacancies/:draft_id` | Архивировать `{expected_revision}`, сохраняя аудит |

Создание и редактирование не публикуют автоматически. Для первоначальной публикации и для новой версии нужны существующие approve → summary → publish/Create. Контракт полей остаётся `AgentsDraftFields`; смена owner и произвольные поля не принимаются.

PATCH опубликованной записи переводит её в редактирование с новой ревизией. Ранее принятый matching-снимок остаётся опубликованным до подтверждения новой версии; прямые действия работодателя через исходный черновик требуют актуального состояния `created/accepted`. Старый маршрут `/vacancy-drafts/:id` сохраняет ограничение на редактирование уже опубликованной записи; для ревизии используется новый `/vacancies/:id`.

Закрытие фиксирует бизнес-статус `closed` сразу в источнике и создаёт существующее durable publication job с более высокой `source_revision` и `status=closed`. Снимок строится из последней опубликованной версии; незавершённые правки в него не попадают. Matching закроет своё представление после обработки очереди. Если публикация уже выполняется или завершилась ошибкой, закрытие возвращает 409: сначала нужно завершить или повторить существующую job. Ошибка закрытия сохраняет доступный для повторения журнал; записи не удаляются физически.

DELETE архивирует запись и одновременно ставит закрытие предыдущей публикации в очередь. Архив исчезает из списков, но остаётся доступным владельцу по UUID для проверки статуса и аудита. Его нельзя редактировать или публиковать. Для непубликовавшегося черновика matching job не требуется. Существующие приглашения, saved и visited не удаляются. Мутации используют CAS; после сетевого сбоя нужно прочитать запись и сверить ревизию. Повтор прежнего CAS может вернуть 409, не создавая вторую мутацию. Публикация сохраняет существующую идемпотентность по exact summary.

## Страницы и поиск витрины

`GET /employer/vacancies?limit=50&cursor=<opaque>` использует keyset `(created_at DESC, draft_id DESC)`. Ключи создания неизменяемы, поэтому правки записи не перемещают её между страницами. Ответ: `{items,limit,has_more,next_cursor}`. Cursor привязан к owner, неправильный cursor или сочетание cursor с ненулевым offset возвращают 409. Новые записи перед границей не добавляются в продолжение старого обхода. Для обратной совместимости принимается `offset` до 1 000 000; старый список `/employer/vacancy-drafts` также сохраняется.

В `POST /employer/candidates/search` поле `filters.vacancy_id` принимает canonical UUID собственной открытой опубликованной вакансии. Gateway проверяет исходный `created/accepted`, `business_status=open`, отсутствие архива и matching readiness с тем же owner. Затем передаёт owner из сессии matching-сервису, который применяет фильтры вакансии. Общий поиск без vacancy_id по-прежнему не требует вакансии.

Миграция агента: `004_vacancy_management.sql`. Генератор контрактов: `PYTHONPATH=services/agents-service/src python tests/generate_agents_contract.py`. Изменения DTO внесены через Pydantic, TypeScript/OpenAPI генерируются.

Проверки БД и lifecycle: `PYTHONPATH=services/agents-service/src AGENTS_TEST_DATABASE_URL=<disposable-db> python -m pytest services/agents-service/tests/test_vacancy_management.py services/agents-service/tests/test_store.py services/agents-service/tests/test_api.py -q`.
