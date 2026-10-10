# data/

Demo and seed data. Ready-made datasets do not exist for the hackathon
(TZ §2): everything here is synthetic, generated or hand-written by the team.

- `seed/` — fixtures used by acceptance scripts against disposable databases
  (companies, vacancies, candidates). FSP histories are served only by the
  integration-engine mock (`fsp-001`, `fsp-002`); seed files carry no `fsp`
  fields yet, so the "candidate WITHOUT FSP history" edge case is exercised
  there and via `fsp-none`, not in these fixtures.
- The synthetic generator (200+ candidates, 10 vacancy descriptions with a
  relevance markup, two employer-agents) lands in sprint S5 (context/70-mvp-scope.md).
  There is no seed loader for a running stack yet — fixtures are acceptance
  inputs, not demo data.

- `seed/candidate-pipeline.json` — four fully fictional upstream profiles for
  cycle-one acceptance: backend junior/senior, data middle, and a draft lacking
  pool consent. Includes confirmed, partial and declared hard/soft claims.
  Used only with a disposable local pipeline database, never imported into prod.

- `seed/vacancy-pipeline.json` — ten fictional sparse vacancy snapshots: class,
  domain, stack, mixed ML, explicit zero, soft-only, bio-only, empty, missing salary,
  and pending. Includes expected eligibility; not a relevance benchmark dataset.
