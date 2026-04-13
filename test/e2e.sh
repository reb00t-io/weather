#!/usr/bin/env bash
set -euo pipefail

: "${PORT:?PORT must be set}"

# Generate a random API key for e2e testing if none is provided
if [ -z "${API_KEY:-}" ]; then
  API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  export API_KEY
  echo "generated ephemeral API_KEY for e2e"
fi

if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh
fi
docker compose up -d
trap 'docker compose down' EXIT

echo "waiting for server..."
wait_timeout_seconds=120
wait_interval_seconds=2
deadline=$((SECONDS + wait_timeout_seconds))
attempt=0
last_status=""

while (( SECONDS < deadline )); do
  attempt=$((attempt + 1))
  status=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${PORT}" || true)
  last_status="$status"

  if [ "$status" = "200" ]; then
    echo "server is up (attempt ${attempt})"
    break
  fi

  if [[ "$status" == 5* ]]; then
    echo "FAIL: server returned HTTP ${status} while starting (attempt ${attempt})"
    docker compose logs --tail 50 || true
    exit 1
  fi

  if [ -z "$status" ] || [ "$status" = "000" ]; then
    echo "waiting... attempt ${attempt}/${wait_timeout_seconds}s (server not reachable yet)"
  else
    echo "waiting... attempt ${attempt}/${wait_timeout_seconds}s (HTTP ${status})"
  fi

  sleep "$wait_interval_seconds"
done

if [ "$last_status" != "200" ]; then
  echo "FAIL: server did not become ready within ${wait_timeout_seconds}s (last status: ${last_status:-none})"
  docker compose logs --tail 50 || true
  exit 1
fi

echo "checking response..."
body=$(curl -sf http://localhost:"$PORT")

if ! echo "$body" | grep -q "Wetter"; then
  echo "FAIL: response does not contain 'Wetter'"
  echo "$body" | head -20
  exit 1
fi

echo "checking API endpoints..."
# API requires Bearer auth
geocode=$(curl -sf -H "Authorization: Bearer ${API_KEY}" "http://localhost:${PORT}/api/geocode?q=Berlin")
if ! echo "$geocode" | grep -q "Berlin"; then
  echo "FAIL: geocode API did not return Berlin"
  echo "$geocode"
  exit 1
fi

echo "checking API auth..."
status=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/api/geocode?q=Berlin")
if [ "$status" != "401" ]; then
  echo "FAIL: API without auth should return 401, got ${status}"
  exit 1
fi

echo "e2e test passed"
