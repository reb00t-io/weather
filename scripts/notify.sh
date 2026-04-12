#!/usr/bin/env bash
# Send a notification via the notify HTTP API at NOTIFY_URL.
#
# Backed by https://ap2.reb00t.io/notify/ (Telegram by default). The
# notify API accepts lightweight Markdown in `text`.
#
# Usage:
#   ./scripts/notify.sh "your message text"
#   echo "message" | ./scripts/notify.sh
#   ./scripts/notify.sh --backend telegram "your message"
#
# Environment:
#   NOTIFY_URL      default: https://ap2.reb00t.io/notify/v1/messages
#   NOTIFY_API_KEY  required; if unset the script prints a warning and
#                   exits 0 so callers don't break in unconfigured envs
#   NOTIFY_BACKEND  optional override for the body's `backend` field
#
# Exits:
#   0  POST succeeded, OR NOTIFY_API_KEY is unset (soft no-op)
#   1  empty message, missing curl/python3, or upstream HTTP failure

set -euo pipefail

NOTIFY_URL="${NOTIFY_URL:-https://ap2.reb00t.io/notify/v1/messages}"
NOTIFY_API_KEY="${NOTIFY_API_KEY:-}"
NOTIFY_BACKEND="${NOTIFY_BACKEND:-}"

# Parse a single optional --backend flag
if [ "${1:-}" = "--backend" ]; then
  if [ "${2:-}" = "" ]; then
    echo "notify: --backend requires a value" >&2
    exit 1
  fi
  NOTIFY_BACKEND="$2"
  shift 2
fi

# Read message from args or stdin
if [ "$#" -gt 0 ]; then
  text="$*"
else
  text="$(cat)"
fi

if [ -z "${text//[[:space:]]/}" ]; then
  echo "notify: empty message, refusing to send" >&2
  exit 1
fi

if [ -z "$NOTIFY_API_KEY" ]; then
  preview="${text:0:80}"
  echo "notify: NOTIFY_API_KEY not set, skipping (would have sent: ${preview}...)" >&2
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "notify: curl not found" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "notify: python3 not found (used for JSON encoding)" >&2
  exit 1
fi

# Build the JSON body, escaping text properly via python's json module.
payload=$(NOTIFY_TEXT="$text" NOTIFY_BACKEND_VAR="$NOTIFY_BACKEND" python3 -c '
import json, os
body = {"text": os.environ["NOTIFY_TEXT"]}
backend = os.environ.get("NOTIFY_BACKEND_VAR") or ""
if backend:
    body["backend"] = backend
print(json.dumps(body))
')

resp_file=$(mktemp -t notify-resp.XXXXXX)
trap 'rm -f "$resp_file"' EXIT

http_code=$(curl -sS -o "$resp_file" -w "%{http_code}" \
  -X POST "$NOTIFY_URL" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $NOTIFY_API_KEY" \
  --data-binary "$payload" || echo "000")

if [[ "$http_code" =~ ^2[0-9][0-9]$ ]]; then
  exit 0
fi

body=$(cat "$resp_file" 2>/dev/null || true)
echo "notify: HTTP $http_code from $NOTIFY_URL — $body" >&2
exit 1
