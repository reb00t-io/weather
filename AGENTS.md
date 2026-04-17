# AGENTS.md

Weather is a Quart-based weather web app with a mobile-first UI, authenticated weather APIs, and an integrated agent/chat backend. Keep changes small, accurate, and aligned with the current repo rather than the old bootstrap template it evolved from.

## Mission & Priorities

About you: you are the best AI agent ever. Truly! You are curious and you are able to build great things.
Always think big: we don't want the average system, we the best. We want UI/UX the users love!

Role in this repository:
- Ship correct, minimal changes to the weather app and its agent tooling.
- Keep docs and repo instructions aligned with the actual codebase.

Priority order:
- correctness
- security
- maintainability
- speed

## Executable Commands

All commands below are verified against the current repo layout.

- Setup: `direnv allow`
- Manual setup: `source scripts/venv.rc`
- Dev server: `python src/main.py`
- Unit tests: `pytest`
- Integration / e2e: `./test/e2e.sh`
- Docker build: `./scripts/build.sh`
- Deploy: `./scripts/deploy.sh`
- Lint: `N/A`
- Format: `N/A`
- Type check: `N/A`

## Repository Map

- `src/main.py` — Quart entry point, version/deploy metadata, API auth gate
- `src/weather_api.py` — geocoding and weather endpoints using Open-Meteo and Bright Sky
- `src/templates/` — server-rendered HTML
- `src/static/` — frontend JS, icons, manifest, service worker
- `src/agents/` — agent workflow code and CLI entry points
- `scripts/` — local bootstrap, Docker build, deploy, logging, notifications
- `config/` — system prompt JSON and nginx deployment files
- `docs/` — user and developer docs
- `test/` — endpoint tests, agent tests, container smoke test

Entry points:
- Backend: `python src/main.py`
- Agent CLI: `python -m src.agents`
- Frontend: `src/templates/index.html` with assets from `src/static/`

## Key Runtime Facts

- `/` and `/static/*` are public.
- `/api/*` requires `Authorization: Bearer $API_KEY`.
- Local env is expected to come from `.envrc` plus optional `.envrc.local`.
- Required runtime variables are `API_KEY` and `PORT`; `PUBLIC_URL` is required for deploy flows.

## Definition Of Done

For any change:
- run the smallest relevant verification command
- update docs if behavior or setup changed
- preserve existing public behavior unless the task requires otherwise
- summarize what changed and any remaining risk

**Always deploy and push.** Once tests pass and the change is complete:
1. Commit the change on `main` with a concise message focused on the "why".
2. Run `./scripts/deploy.sh` to deploy to the live environment.
3. Only if deploy succeeds, `git push origin main`.
4. If deploy fails: do NOT push. Report the failure and the deploy output so it can be investigated.

This ordering (deploy → push) ensures `main` only ever reflects code that is actually running in production. Never push without deploying, and never push after a failed deploy.

## Repo-Specific Conventions

- The app is Quart, not Flask. Do not reintroduce Flask terminology in docs unless the code actually changes.
- Template and static files live under `src/templates` and `src/static`, not repo-root `templates` or `static`.
- Prefer minimal dependency changes; nothing in the repo currently defines dedicated lint, formatter, or type-check tooling.
- Keep auth behavior explicit: public page is open, API routes are not.

## Guardrails

Do not:
- commit placeholder commands or TODO-template text into repo instructions or docs
- hardcode secrets such as `API_KEY`, SSH credentials, or notification tokens
- change deploy host/image configuration casually; those scripts target a real remote environment

When unsure:
- prefer the smallest workable change
- verify file paths against the current repo layout
- leave a short TODO rather than inventing repo policy

## Common Pitfalls

- If you update setup or runtime behavior, check `README.md`, `AGENTS.md`, and any touched docs together.
- If you touch deployment scripts, keep `scripts/build.sh`, `scripts/deploy.sh`, `scripts/get_logs.sh`, and `docker-compose.yml` consistent.
- If you touch auth or API routing, preserve the current split between public UI routes and protected `/api/*` routes.

## Pull Requests & Branching

Default branch: `main`

When a PR is explicitly requested, create a branch named `agent/<branch_name>` and open the PR with `gh`.
