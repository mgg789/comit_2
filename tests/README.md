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

- `vacancy_pool_acceptance.py` — real gateway/PG/both workers/Harrier verification
  for ten sparse vacancy snapshots, explicit-zero masks, query/document space,
  shared schema backfill and status withdrawal/reopen. Requires a separate fresh
  local database; writes `artifacts/vacancy-pool-acceptance.json` (ignored).

- `candidate_profile_acceptance.py` — authenticated source entry to employer
  main top-k, using the real encrypted OTP mailbox, projection worker, Harrier,
  PDFs, vacancy publication and event delivery. Checks consent revoke/regrant,
  ownership and declaration-only claims on a fresh loopback acceptance DB.
  `profile_auth_mailbox.mjs` is an IPC-only synthetic mailbox, never an SMTP
  service or public endpoint. See `docs/candidate-profile.md` for setup and
  environment contracts; reports under `artifacts/` never include credentials.

- `candidate_privacy_acceptance.py` — extends the completed candidate profile
  fixture on the same isolated PROFILE_ACCEPTANCE_* stack. Checks actual owner
  PDF/role isolation, no training features without opt-in, separate version/time,
  source-only training revocation/export and continued top-2 participation.
  Run immediately after `candidate_profile_acceptance.py`; uses the synthetic
  encrypted mailbox and never prints credentials or profile contents.
