# Durable feed refill

Лента работодателя остаётся сессией фиксированного источника. Первые карточки
сохраняют main/rerank provenance, а пополнение добавляет отдельный immutable
batch в конец. Уже выданные позиции и курсоры не меняются.

Для каждой вакансии сохраняется strict-compatible snapshot `pool-n1-v1`:
кандидат, revision и strict schema identity. Изменение считается как доля
изменённых, добавленных и удалённых IDs в объединении старого и нового
снимков. Версионированный K1 равен `0.10`; при `fraction >= K1` пополнение
ставится в durable job даже если текущий хвост ещё не достиг low-water.

Обычный low-water равен 4 необслуженным карточкам, размер refill batch — 12.
Сначала используются уже оценённые невидимые reserve-карточки того же
rerank-run. Если их недостаточно, worker ставит свежую пару combine/rerank в
очередь и повторяет job после её готовности. В batch попадают только карты
этого согласованного rerank-run, включая официальный `main_fallback`, если
именно такой mode опубликован reranker-ом; worker не пересчитывает хвост,
score или порядок самостоятельно.

`feed_pipeline.refill_jobs` имеет частичный unique index на queued/running/
retrying session jobs. Worker использует PostgreSQL lease, поэтому повторные
запросы страниц и рестарты не создают duplicate batch. `feed_pipeline.batches`
и поля lineage на каждой exposure связывают выдачу с `main_run_id`,
`rerank_run_id`, `pool_generation` и snapshot hash; learning export переносит
эту связь в anonymized observations.

Saved, visited и pending исключаются. `visited_updated` допускается после
изменения проверяемого профиля. Исчерпание фиксируется после успешного пустого
batch и не порождает бесконечные пустые jobs; новый strict-compatible кандидат
или изменение пула снимает состояние exhausted.

Для saved/visited используется отдельный keyset cursor `base64url(JSON)` с
видом списка, snapshot timestamp, последним candidate ID и vacancy revision.
Он не кодирует offset, ограничен 1024 байтами, не принимается вместе с legacy
offset и даёт 409 при смене vacancy source revision. Старые offset-вызовы
сохраняются для совместимости.

Локальный worker:

```bash
DATABASE_URL=... PYTHONPATH=services/matching-engine/src \
  python -m matching_engine feed-refill-worker --once
```

Migration `014_feed_refill.sql` применяется обычным `matching_engine migrate`.
