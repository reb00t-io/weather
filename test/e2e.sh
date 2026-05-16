#!/usr/bin/env bash
set -euo pipefail

if [ -z "${PORT:-}" ]; then
  PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
  export PORT
  echo "allocated free PORT=${PORT} for e2e"
fi

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

# If we're running inside a docker container (e.g. docker-in-docker or a
# devcontainer) host port mappings live on the parent host rather than on
# our localhost. Detect that and reach the weather container by name on
# its compose network instead.
self_cid=""
joined_network=""
if [ -f /.dockerenv ]; then
  self_cid=$(awk -F/ '/docker/ {print $NF; exit}' /proc/self/cgroup 2>/dev/null || true)
fi

cleanup() {
  if [ -n "$joined_network" ] && [ -n "$self_cid" ]; then
    docker network disconnect "$joined_network" "$self_cid" >/dev/null 2>&1 || true
  fi
  docker compose down
}
trap cleanup EXIT

host="localhost"
probe="http://localhost:${PORT}"

# Quick probe: if localhost mapping isn't reachable and we're in a container,
# join the weather compose network so we can reach the service by name.
if [ -n "$self_cid" ]; then
  if ! curl -sS -o /dev/null --max-time 2 "$probe" >/dev/null 2>&1; then
    if docker network connect weather_default "$self_cid" >/dev/null 2>&1; then
      joined_network="weather_default"
      host="weather"
      probe="http://weather:${PORT}"
      echo "host port mapping unreachable; using compose network as ${probe}"
    fi
  fi
fi

base_url="http://${host}:${PORT}"

echo "waiting for server..."
wait_timeout_seconds=120
wait_interval_seconds=2
deadline=$((SECONDS + wait_timeout_seconds))
attempt=0
last_status=""

while (( SECONDS < deadline )); do
  attempt=$((attempt + 1))
  status=$(curl -sS -o /dev/null -w "%{http_code}" "$base_url" || true)
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
body_file=$(mktemp)
curl -sf "$base_url" > "$body_file"

if ! grep -q "Wetter" "$body_file"; then
  echo "FAIL: response does not contain 'Wetter'"
  head -20 "$body_file"
  rm -f "$body_file"
  exit 1
fi
rm -f "$body_file"

echo "checking API endpoints..."
# API requires Bearer auth
geocode=$(curl -sf -H "Authorization: Bearer ${API_KEY}" "${base_url}/api/geocode?q=Berlin")
if ! echo "$geocode" | grep -q "Berlin"; then
  echo "FAIL: geocode API did not return Berlin"
  echo "$geocode"
  exit 1
fi

echo "checking API auth..."
status=$(curl -sS -o /dev/null -w "%{http_code}" "${base_url}/api/geocode?q=Berlin")
if [ "$status" != "401" ]; then
  echo "FAIL: API without auth should return 401, got ${status}"
  exit 1
fi

echo "e2e test passed"
