# AGENTS.md — Comit

Rules for any AI agent (Claude Code, Codex, Cursor, ZCode) and human contributor working in this repository.

Team language is Russian; code, comments, identifiers, commits, and PRs are written in English; user- and judge-facing documentation (root `README.md` and `docs/`) is written in Russian.

## Team memory (Obsidian vault + jev-local) — read first

Shared memory of the team: Obsidian vault `/Users/mike/Documents/Main`, project folder `projects/comit/`, entry point `projects/comit/comit.md`. All product context lives in `context/` in this repo — the vault holds process facts and decisions that do not belong in the repo.

Search before large work or context gathering:

```
POST http://127.0.0.1:12444/api/v1/memory-search
{"query": "вопрос", "mode": "hybrid", "limit": 8}
```

Write back on every addition (decision, root cause, milestone, changed fact/plan/status): `projects/comit/tasks.md` gets one append-only line per finished task; real decisions go to `projects/comit/decisions.md` (context → decision → why → alternatives). When a fact changes, add a new entry marked `supersedes: <old fact>` — stale memory is worse than none. If the API is unreachable, fall back to `rg` over the vault and `NAV.md`; never block work on it. Secrets never go into memory or the repo.

## 0. Project Overview

Comit — web platform for hiring IT specialists with **inverse mechanics** (LHH model): candidates are automatically categorized by a objectively verifiable profile (mandatory survey + grade test), and the **employer** browses categorized pools and contacts a specific person with an offer and salary. Hackathon project of LCT 2026, special track by the FSP (Federacija sportivnogo programmirovanija).

Strategy: not "many features" — a demo-ready product where the two judged mechanics (matching 35% + testing/validation 25%) are explainable and alive. Explainability of every selection is a core trust feature.

| Link | What |
|---|---|
| https://github.com/mgg789/comit_2 | This repository (private, primary) |
| `context/README.md` | Context map: TZ copy, research, product vision, mechanics, plans |
| `context/tz/00-fsp-tz-2026.md` | Full TZ (judge canon) — never contradict it |
| `docs/` | Project documentation (see §8) |
| `/home/mgg/comit` on mgg-server | Deploy target (self-hosted runners deploy there, docs/deploy.md) |

Stack: pnpm monorepo — `apps/{candidate,employer,testing,welcome}` (React 19 + TS + Vite + Tailwind 4 + framer-motion when it lands), `apps/api` (Fastify + TypeScript, OpenAPI), `packages/{shared,ui-kit,api-client}`, `services/{matching-engine,testing-engine,integration-engine}` (Python 3.12 + FastAPI), PostgreSQL 16 + pgvector, docker-compose.

## 1. Sources of Truth (priority ladder)

Safety of the system, data, repository, and the demo environment
1. The user's direct request
2. This file
3. `context/` pack — do not re-open decided questions (`context/80-open-questions.md` holds the decision table; `context/90-debates.md` the accepted disputes)
4. The kanban board, if one is added (update this file when it appears)
5. Other project files

If local standards exist for components, layout, API layer, typing, styles, animations, structure — follow them strictly; never introduce a second way of doing the same thing.

## 2. Rule Priority (when in doubt)

1. Safety of system, data, and project boundaries
2. Correct execution of the actual task
3. A complete, working result consistent with the project
4. Adherence to architecture, contracts, and local standards
5. Minimal invasiveness of changes
6. Readability and maintainability

## 3. Scope of Work

Work only in the area your task belongs to (`apps/*`, `packages/*`, `services/*`, `infra/`, `data/`, `tests/`, `docs/`, `context/`); other areas are read-only (read them to understand contracts and integrations). Modifying other areas is forbidden without a direct command.

- `packages/shared` (contracts) is changed only together with every consumer it breaks — types first, then apps/api/services in the same commit or a stacked PR.
- `context/` is the canon: update it in the same commit when behavior deviates from it; do not duplicate its content into `docs/` — link it.
- Server-side infrastructure (mgg-server, nginx, DNS, secrets) is off-limits without an explicit user command; deploys go through the GitHub Actions deploy workflow (docs/deploy.md), interactive server ops are the user's call.

## 4. General Execution Rules

Write only working, complete code — no placeholders, stubs, or fake logic unless explicitly requested (the black-box testing stub of sprint S1 is an explicitly requested, time-boxed exception; replace it in S2, context/70).

Before changing anything: understand the task, affected modules, existing patterns, impact on adjacent scenarios.

Reuse first — search for existing components, styles, hooks, services, API layers, design tokens; create new only when reuse is impossible. Follow existing architecture and style exactly.

Minimal sufficient diff; do not touch unrelated code; no drive-by refactoring.

Between a quick hack and a robust in-scope solution — choose the robust one. If a plan implies a weak/unsafe/poor solution — do not silently comply: briefly object and propose better.

Stuck > ~30 minutes — stop rabbit-holing: report the blocker to the human and ask.

Every external call (LLM, FSP mock registries, future connectors) must be cached and degrade gracefully — the demo contour must work fully offline.

Visibility rules of TZ §2.2 are law: salary range mandatory in vacancy and invitation; candidate contacts hidden until acceptance; statuses visible to both sides; candidate sees only own records.

## 5. Branch Management

`dev` is the default working branch: start sessions with `git checkout dev && git pull`, branch feature work off it, merge back into it.

`main` is the demo/deploy mirror: the deploy workflow runs on every push to main. Preferred path to main is a PR (`dev` → `main`) once the state is stable. main is intentionally NOT branch-protected (team decision 2026-10-05): in an emergency the team lead may push directly — this is a rescue exception, not a habit. Never force-push or rewrite history on `main` or `dev`.

Naming: `feat/<area>-<slug>`, `fix/<area>-<slug>`, `chore/<slug>`, `docs/<slug>`, `ci/<slug>`, `refactor/<area>-<slug>`. Branch off `dev`.

Short-lived branches (≤ 1–2 days); rebase onto `dev` before merging; merge with a green smoke gate (§11); clear merge commit or fast-forward — history is a demo artifact, do not squash away checkpoints.

One branch = one logical task; no mixing unrelated work.

If your branch changes `packages/shared` types — update all consumers in the same commit, or stack the PR.

## 6. Commits

Conventional Commits, imperative, English — `<type>(<scope>): <summary>` (+ optional body: why, what, side effects). Types: feat fix docs style refactor perf test build ci chore. Scopes: web, api, matching, testing, integration, shared, ui, infra, data, docs, context.

Examples:
- `feat(matching): add strict pre-filter with visited/pending exclusion`
- `fix(web): keep invitation salary visible before chat starts`
- `docs(context): record decision Q5 hybrid vectors`

Commit at meaningful checkpoints — a working increment a teammate could build on — and green: never leave uncommitted work others can't build on, never commit red code to a shared branch. Push after every commit session.

Messages explain why, not just what; a commit that changes validation results or scoring weights must say so.

Never commit: secrets, tokens, `.env`, build artifacts, `node_modules`, `.mimosa/` state. A leaked secret is rotated immediately and reported.

## 7. Working with Tasks

No board is connected yet. Task source: the user's direct request, then `context/70-mvp-scope.md` (sprint plan) and `context/80-open-questions.md` (open decisions). Board empty / no task given — say so explicitly; do not invent work. If a kanban board is added, replace this section.

No silent scope growth: new ideas become explicit tasks for the human to prioritize (a line in `context/80-open-questions.md`).

Completed work is logged to the vault: one append-only line in `projects/comit/tasks.md` (`- YYYY-MM-DD HH:MM — what was done (scope)`).

## 8. Documentation (mandatory, /docs)

Every exported function, class, hook, module carries a doc comment (JSDoc/docstring): purpose, params, return, non-obvious constraints (units, time zones, side effects). Comments explain constraints, not mechanics.

`/docs` — living per-subsystem documentation, same commit as the code: `architecture.md`, `api.md`, `matching.md`, `testing-engine.md`, `integration.md`, `data.md`, `deploy.md`, `ci.md`, `runbook.md`. Docs are part of Definition of Done: code without its docs update is incomplete.

Every schema/contract change is documented in `docs/api.md` / `docs/data.md` and reflected in `packages/shared` types and OpenAPI (Fastify schemas, FastAPI pydantic models).

Context pack (`context/`): `README.md` routes to the document; check INDEX first — update the existing file instead of duplicating; must not contradict the canon (`context/tz/00-fsp-tz-2026.md`, decisions in `context/80`/`90`) — divergences are fixed or explicitly status-marked.

## 9. Code Style and Reuse

TypeScript strict: no `any`, no unsafe casts, no suppressed errors unless truly unavoidable (with a comment why). Strictly typed DTOs, props, state, form values. Shared types live in `packages/shared`.

`apps/api` (Fastify): ESM with NodeNext — relative imports carry the `.js` extension; route schemas define the OpenAPI contract (no undocumented endpoints); `PORT` from env; structured logging via the Fastify logger.

Python services (FastAPI): type hints on public functions; ruff-clean; pydantic models for request/response contracts; app built via `create_app()` factory so tests use `TestClient`; service package is importable (`src/` layout, `python -m <package>` entrypoint).

UI: design tokens from `@comit/ui-kit` (`tokens.css`) only; established Tailwind patterns; no inline colors or ad-hoc spacing outside the kit; interactions 120–180 ms, reveals 250–350 ms once framer-motion lands; responsive from the start; empty/error/loading states are part of the component.

Matching/scoring: weights and formulas live in one place (`services/matching-engine`), never re-implemented in apps; every selection must carry structured explanation factors (context/50) — never hand-written strings.

LLM never makes hiring/selection decisions; it may summarize and explain computed structures, with validation and a manual fallback.

Match surrounding naming, comment density, idiom; comments state constraints the code can't show.

## 10. Backend Contracts Missing / Not Ready

Do not pretend the backend exists; do not fake data.

Prepare the frontend fully for integration (types, API layer, integration points, all UI states) so no later rework is needed.

List every missing contract in the report: endpoint, method, request/response DTO, where the frontend expects it.

## 11. Testing

Smoke gate: `./tests/smoke.sh` (via `make smoke`) — every service answers `/healthz`, nginx serves all four apps, demo contracts respond (blueprint list, FSP 404 semantics). Must be green before any merge; if tooling is missing, create it — never skip the gate.

Every new module ships with at least one test; bug fixes ship with a regression test (red → green). A module without tests is an incomplete module.

Scoring/testing mechanics: the synthetic validation suite is the arbiter (context/50 §validation, context/60 §validation) — deviations must be intentional and documented in the commit body.

## 12. Verification and Honesty of Results

Never claim verification that did not happen: reports distinguish verified in practice (command + result), verified logically (reasoning), not verified. A proxy signal (green CI, "should work") is not proof of user-facing correctness.

Final report structure: what was done; affected files/modules; risks and limitations; missing contracts; verified / not verified.

## 13. Safety and Restrictions

No changes to system settings, Docker daemon, network config, CI/CD secrets, global env, production configs, or external infrastructure without a direct command.

Compliance frame (TZ §3.5): 152-ФЗ — consent for profile data processing/publication, role separation, safe storage of passwords/tokens; open-source licenses (MIT/Apache-2.0/BSD) for dependencies.

Containers: normal restart is the default; full rebuild (`down -v`) only when dependencies changed or explicitly requested. Do not edit compose/Dockerfiles unless it is the task.

If the environment does not start — report the blocker; do not escalate to destructive system actions.

Secrets live in `.env` (git-ignored) and on the server only — never in the repo, memory, or logs.

Server work (mgg-server, deploy target `/home/mgg/comit`): only on an explicit user command; inspect first, explain the deploy path before executing.

## 14. Agent Work Format (per session/task)

First: task, affected modules, main assumptions.

Then: short plan (2–5 bullets).

Then: changes with green-checkpoint commits (§6) on the branch (§5).

Finally: summary per §12; vault `tasks.md` line (§7).

## 15. Session Checklist

Start: this file → `context/README.md` (route to your zone) → `git checkout dev && git pull` → pick/update your task → branch off `dev`.

End: smoke green → docs + context updated → commits pushed → branch merged into `dev`, pushed → vault `tasks.md` line → memory write-back (see "Team memory" above).

## 16. Definition of Done

- Task acceptance criteria met
- Tests: smoke green + module tests + regression test for the fixed bug
- Docs: doc comments on new public API + `docs/` updated + `context/` reconciled
- Commits: green checkpoints, conventional messages, pushed
- Visibility rules of TZ §2.2 intact; demo contour works offline
- Vault `tasks.md` updated
