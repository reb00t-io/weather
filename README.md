# Bootstrap Template

A template for bootstrapping new Python web apps. Copy all files into an empty repo and you're ready to go.

## What's Included

- **Flask + Uvicorn** web server (Python 3.13)
- **Docker** build with docker-compose
- **CI/CD** via GitHub Actions (build → e2e → deploy)
- **Deploy script** that builds, uploads, and runs the container on a remote host via SSH
- **E2e tests** that verify the running container
- **AI chat panel** — a built-in assistant that knows about your app, powered by any OpenAI-compatible endpoint
- **VS Code** launch config

## Quick Start

```bash
# 1. Copy all files into your new repo

# 2. Allow direnv to load the environment (creates venv, installs deps, sets PORT)
direnv allow

# 3. Run locally
python src/main.py
# → http://localhost:$PORT

# 4. Or run with Docker
./scripts/build.sh
docker compose up
```

## Project Structure

```
config/
  system_prompt.json   # AI assistant persona + instructions template
docs/
  docs.md              # App documentation injected into the AI system prompt
src/
  main.py              # Flask app entry point
  templates/
    index.html         # Page shell + chat panel HTML/CSS
  static/
    chat/
      chat.js          # Chat panel logic (ES module)
scripts/
  venv.rc              # Create/activate venv and install deps
  build.sh             # Docker build
  deploy.sh            # Build, upload, and deploy to remote host
  get_logs.sh          # Fetch container logs from remote
test/
  e2e.sh               # End-to-end smoke test
.github/workflows/
  ci.yml               # CI pipeline: build → e2e → deploy
.envrc                 # direnv: venv setup + PORT config
Dockerfile
docker-compose.yml
pyproject.toml
VERSION
```

## Customization

After copying, you'll want to:

1. Rename the project in `pyproject.toml` and `VERSION`
2. Update the Docker image name in `scripts/build.sh` and `scripts/deploy.sh`
3. Set your remote host/port/user in `scripts/deploy.sh`
4. Add your `DEPLOY_SSH_KEY` secret in GitHub repo settings
5. Change `PORT` in `.envrc` if needed
6. Replace `src/templates/index.html` and the `/` route with your app

## AI Chat

The chat panel connects to an OpenAI-compatible endpoint. Configure the URL and model in `src/static/chat/chat.js`:

```js
const CHAT_API_BASE = 'http://localhost:8080/v1';
const CHAT_MODEL    = 'gpt-oss-120b';
```

Customise what the assistant knows by editing `docs/docs.md` (app documentation) and `config/system_prompt.json` (persona and instructions). Both are loaded from disk on every request — no restart needed.

To remove the chat feature entirely, delete the marked sections in `src/templates/index.html` and `src/static/chat/chat.js`.
