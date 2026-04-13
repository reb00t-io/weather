#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${1:-}"
BUILD_ARGS=(--build-arg "DEPLOY_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)" -t weather)

if [ -n "$PLATFORM" ]; then
  BUILD_ARGS+=(--platform "$PLATFORM")
fi

docker build "${BUILD_ARGS[@]}" .
