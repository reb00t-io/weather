#!/usr/bin/env bash
set -euo pipefail

# Deploy script for weather app
# Builds, uploads, and starts the Docker container on the remote host.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

REMOTE_HOST="test.k3rnel-pan1c.com"
REMOTE_PORT=2223
REMOTE_USER="marko"
IMAGE_NAME="weather"
REMOTE="$REMOTE_USER@$REMOTE_HOST"

# Persistent SSH multiplexed connection
SSH_CONTROL_DIR=$(mktemp -d /tmp/deploy-ssh.XXXXXX)
SSH_CONTROL_PATH="$SSH_CONTROL_DIR/s"
SSH_OPTS=(-p "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)
SCP_OPTS=(-P "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)

cleanup() {
  local rc=$?
  if (( rc != 0 )); then
    notify_deploy_result "failed"
  fi
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
  rm -rf "$SSH_CONTROL_DIR"
}
trap cleanup EXIT

retry_cmd() {
  local max=$1 backoff=$2; shift 2
  local attempt=1
  while true; do
    if "$@"; then return 0; fi
    if (( attempt >= max )); then return 1; fi
    echo " (attempt $attempt/$max failed, retrying in ${backoff}s...)"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    attempt=$(( attempt + 1 ))
  done
}

deploy_step="init"

notify_deploy_result() {
  local status="$1"
  local short_sha
  short_sha=$(git rev-parse --short HEAD 2>/dev/null || echo "?")

  local repo_url=""
  if remote=$(git remote get-url origin 2>/dev/null); then
    repo_url="${remote%.git}"
    repo_url="${repo_url/git@github.com:/https://github.com/}"
  fi

  local app_link="[${IMAGE_NAME}](${PUBLIC_URL:-#})"
  local commit_link="${short_sha}"
  if [ -n "$repo_url" ] && [ "$short_sha" != "?" ]; then
    commit_link="[${short_sha}](${repo_url}/commit/${short_sha})"
  fi

  local subject
  if [ "$status" = "succeeded" ]; then
    subject="✅ ${app_link}: deployed ${commit_link}"
  else
    subject="❌ ${app_link}: deploy FAILED at \`${deploy_step}\` (${commit_link})"
  fi
  "${SCRIPT_DIR}/notify.sh" "$subject" || true
}

# ---- required environment ------------------------------------------------
: "${PORT:?PORT must be set}"
: "${PUBLIC_URL:?PUBLIC_URL must be set}"
: "${API_KEY:?API_KEY must be set}"

print_remote_diagnostics() {
  echo "    remote diagnostics:"
  ssh "${SSH_OPTS[@]}" "$REMOTE" "
    set +e
    cd ~/${IMAGE_NAME} 2>/dev/null || true
    echo '--- docker compose ps ---'
    docker compose ps 2>&1 || true
    echo
    echo '--- container state ---'
    docker inspect ${IMAGE_NAME} --format '{{json .State}}' 2>&1 || true
    echo
    echo '--- container logs (stdout + stderr, last 200 lines) ---'
    docker compose logs --tail 200 2>&1 || docker logs --tail 200 ${IMAGE_NAME} 2>&1 || true
  " || true
}

# ---- build ---------------------------------------------------------------
deploy_step="build"
printf "==> building image (%s, linux/amd64)..." "$IMAGE_NAME"
if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh linux/amd64 > /dev/null 2>&1
fi
echo "ok"

# ---- save & upload image -------------------------------------------------
deploy_step="upload"
printf "==> saving image..."
docker save "$IMAGE_NAME" | gzip > /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

printf "==> uploading to %s..." "$REMOTE_HOST"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" /tmp/"${IMAGE_NAME}".tar.gz "$REMOTE":/tmp/"${IMAGE_NAME}".tar.gz
rm /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

printf "==> loading image on remote..."
ssh -p "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=24 "$REMOTE" "
  docker load < /tmp/${IMAGE_NAME}.tar.gz
  rm /tmp/${IMAGE_NAME}.tar.gz
" > /dev/null 2>&1
echo "ok"

# ---- upload compose file -------------------------------------------------
deploy_step="compose-upload"
printf "==> uploading compose file..."
retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ~/${IMAGE_NAME}"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" docker-compose.yml "$REMOTE":~/"${IMAGE_NAME}"/docker-compose.yml
echo "ok"

# ---- write .env on remote ------------------------------------------------
deploy_step="env-setup"
printf "==> writing remote .env..."
printf -v port_q '%q' "$PORT"
printf -v api_key_q '%q' "$API_KEY"

retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" 'bash -se' <<EOF
cat > ~/${IMAGE_NAME}/.env <<'ENVEOF'
PORT=$port_q
API_KEY=$api_key_q
ENVEOF
EOF
echo "ok"

# ---- start services ------------------------------------------------------
printf "==> removing stray container (if any)..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  if docker inspect ${IMAGE_NAME} >/dev/null 2>&1; then
    project_label=\$(docker inspect ${IMAGE_NAME} --format '{{ index .Config.Labels \"com.docker.compose.project\" }}')
    if [ -z \"\$project_label\" ] || [ \"\$project_label\" != \"${IMAGE_NAME}\" ]; then
      docker rm -f ${IMAGE_NAME} >/dev/null
    fi
  fi
" 2>/dev/null || true
echo "ok"

deploy_step="start-services"
printf "==> starting services..."
compose_up_log=$(mktemp)
if ! retry_cmd 3 4 ssh "${SSH_OPTS[@]}" "$REMOTE" "
  cd ~/${IMAGE_NAME}
  docker compose up -d --remove-orphans
" >"$compose_up_log" 2>&1; then
  echo "FAIL"
  echo "    docker compose up output:"
  sed 's/^/    /' "$compose_up_log"
  rm -f "$compose_up_log"
  print_remote_diagnostics
  exit 1
fi
rm -f "$compose_up_log"
echo "ok"

# ---- wait for server -----------------------------------------------------
deploy_step="wait-for-server"
printf "==> waiting for server..."
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-120}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
WAIT_DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
server_ready=false

while (( $(date +%s) < WAIT_DEADLINE )); do
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "curl -sf --max-time 3 http://localhost:${PORT}/ > /dev/null" 2>/dev/null; then
    server_ready=true
    break
  fi
  sleep "$WAIT_INTERVAL_SECONDS"
done

if [[ "$server_ready" != true ]]; then
  echo "FAIL"
  echo "    server did not start within ${WAIT_TIMEOUT_SECONDS}s"
  print_remote_diagnostics
  exit 1
fi
echo "ok"

# ---- public smoke check --------------------------------------------------
deploy_step="smoke-check"
printf "==> checking public endpoint (%s)..." "$PUBLIC_URL"
if ! body=$(curl -sfL --max-time 10 "$PUBLIC_URL"); then
  echo "FAIL"
  echo "    could not reach $PUBLIC_URL"
  exit 1
fi

if ! echo "$body" | grep -q "Wetter"; then
  echo "FAIL"
  echo "    $PUBLIC_URL response did not look right"
  echo "    $body" | head -20
  exit 1
fi
echo "ok"

./scripts/get_logs.sh

notify_deploy_result "succeeded"
echo "==> deployed $IMAGE_NAME to $PUBLIC_URL"
