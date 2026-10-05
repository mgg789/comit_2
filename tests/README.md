# tests/

Cross-service verification. Per-module tests live next to the code
(`apps/**` via vitest when they appear, `services/*/tests` via pytest);
this directory holds only what spans services.

- `smoke.sh` — the smoke gate: waits for every service `/healthz`, checks the
  nginx routes and the demo contracts (blueprint list, FSP mock 404 semantics).
  CI runs it with isolated ports; the deploy workflow runs it against prod
  defaults (docs/ci.md, docs/deploy.md).

Run locally after `make up`:

```sh
./tests/smoke.sh
```

- `candidate_pool_acceptance.py` — real API-core → PostgreSQL → worker → Harrier
  → dual vectors + PDF. Uses `data/seed/candidate-pipeline.json` and an isolated,
  fresh local database. Adds a strict coordinate and withdraws one consent.
  Requires `CANDIDATE_PIPELINE_TOKEN`, real worker, and `PIPELINE_ACCEPTANCE_URL`
  (defaults to loopback port 38080). Writes ignored reports to `tests/artifacts/`.
- `generate_candidate_contract.py` — regenerate the gateway OpenAPI source from
  the FastAPI app, using the matching service environment and PYTHONPATH.

Never run integration cleanup against a live pool; see `docs/runbook.md` for
the separate unit, PostgreSQL, real-model and smoke verification contours.
