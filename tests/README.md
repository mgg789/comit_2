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
