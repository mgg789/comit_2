# data/

Demo and seed data. Ready-made datasets do not exist for the hackathon
(TZ §2): everything here is synthetic, generated or hand-written by the team.

- `seed/` — fixtures loaded by the seed step (companies, vacancies, candidates,
  FSP histories incl. candidates WITHOUT FSP history — the mandatory edge case).
- The synthetic generator (200+ candidates, 10 vacancy descriptions with a
  relevance markup, two employer-agents) lands in sprint S5 (context/70-mvp-scope.md).

- `seed/candidate-pipeline.json` — four fully fictional upstream profiles for
  cycle-one acceptance: backend junior/senior, data middle, and a draft lacking
  pool consent. Includes confirmed, partial and declared hard/soft claims.
  Used only with a disposable local pipeline database, never imported into prod.
