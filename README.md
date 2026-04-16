# Weather

A Quart-based weather web app with a mobile-first UI, place search, current conditions, hourly and daily forecasts, and an integrated chat/agent backend.

The public page at `/` is open. API routes are protected with Bearer auth via `API_KEY`.

## Features

- Current weather with a preference for Bright Sky / DWD observations
- Hourly and 7-day forecasts from Open-Meteo
- Place search via Open-Meteo geocoding
- Mobile-oriented frontend with PWA assets
- Docker-based local run and deployment
- Remote deploy and log collection scripts
- Test coverage for weather endpoints and agent workflow code

## Requirements

- Python 3.12 or newer
- `direnv` if you want automatic environment loading
- Docker and Docker Compose for containerized runs

## Local Setup

The repository is set up to bootstrap a local virtual environment through `.envrc` and `scripts/venv.rc`.

```bash
direnv allow
```

That will:

- create `./.venv` if needed
- activate it
- install the project in editable mode with dev dependencies
- export `PORT` and `PUBLIC_URL` from `.envrc`

Create a local secrets file before running the app:

```bash
cat > .envrc.local <<'EOF'
export API_KEY='replace-me'
EOF
```

Then start the app:

```bash
python src/main.py
```

The server listens on `http://localhost:$PORT`.

If you do not use `direnv`, you can bootstrap manually:

```bash
source scripts/venv.rc
export PORT=31030
export PUBLIC_URL='http://localhost:31030'
export API_KEY='replace-me'
python src/main.py
```

## Environment Variables

- `API_KEY`: required; used to protect `/api/*` routes with `Authorization: Bearer <key>`
- `PORT`: required; server listen port
- `PUBLIC_URL`: used by deployment and public smoke checks
- `DEPLOY_DATE`: optional; shown in the UI, normally injected at Docker build time
- `NOTIFY_API_KEY`: optional; enables deploy notifications in `scripts/notify.sh`

`.envrc` loads `.envrc.local` automatically if present, which is the intended place for local secrets.

## API

The app exposes two authenticated API routes:

- `GET /api/geocode?q=Berlin`
- `GET /api/weather?lat=52.52&lon=13.41`

Example:

```bash
curl -H "Authorization: Bearer $API_KEY" \
  "http://localhost:$PORT/api/geocode?q=Berlin"

curl -H "Authorization: Bearer $API_KEY" \
  "http://localhost:$PORT/api/weather?lat=52.52&lon=13.41"
```

Unauthenticated requests to `/api/*` return `401`. The home page and static assets remain public.

## Docker

Build the image:

```bash
./scripts/build.sh
```

Run it with Compose:

```bash
export PORT=31030
export API_KEY='replace-me'
docker compose up
```

The Compose setup expects `PORT` and `API_KEY` in the environment.

## Tests

Run the Python test suite:

```bash
pytest
```

Run the end-to-end container smoke test:

```bash
export PORT=31030
export API_KEY='replace-me'
./test/e2e.sh
```

If `API_KEY` is unset for the e2e test, the script generates a temporary one automatically.

## Deployment

The deploy flow is script-driven and ships a Docker image to a remote host over SSH.

```bash
./scripts/deploy.sh
```

The script expects these variables in the environment:

- `PORT`
- `PUBLIC_URL`
- `API_KEY`

Useful related commands:

```bash
./scripts/get_logs.sh
./scripts/build.sh linux/amd64
```

Remote host details, SSH port, and image name are currently configured directly in the deploy scripts.

## Project Layout

```text
src/
  main.py               Quart app entry point and auth gate
  weather_api.py        Weather and geocoding endpoints
  templates/            Server-rendered HTML
  static/               Frontend JS, icons, manifest, service worker
  agents/               Agent-related backend code
scripts/
  venv.rc               Local venv bootstrap
  build.sh              Docker build helper
  deploy.sh             Remote deployment over SSH
  get_logs.sh           Remote container log fetcher
  init.sh               Repo/project retargeting helper
config/
  dev_system_prompt.json
  user_system_prompt.json
  nginx/
docs/
  dev_docs.md
  user_docs.md
test/
  test_weather_api.py   API unit tests
  test_improve.py       Agent workflow tests
  e2e.sh                Docker-based smoke test
```

## Notes

- The package metadata in `pyproject.toml` still has a placeholder description.
- Some documentation files under `docs/` still contain bootstrap-era content and may need the same cleanup as this README.
