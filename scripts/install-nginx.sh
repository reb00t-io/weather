#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC_CONFIGURE="$REPO_ROOT/config/nginx/configure-nginx-weather.reb00t.io.sh"
SRC_CONF="$REPO_ROOT/config/nginx/weather.reb00t.io.conf"

if [ $# -ne 1 ]; then
  echo "Usage: $0 <target-folder>"
  exit 1
fi

TARGET="$1"

if [ ! -d "$TARGET" ]; then
  echo "Error: $TARGET is not a directory"
  exit 1
fi

copy_file() {
  local src="$1" dst="$2"
  if [ -f "$dst" ]; then
    read -r -p "$(basename "$dst") already exists. Overwrite? [Y/n] " answer
    answer="${answer:-Y}"
    if [[ ! "$answer" =~ ^[Yy] ]]; then
      echo "Skipping $(basename "$dst")"
      return 1
    fi
  fi
  cp "$src" "$dst"
  echo "Copied $(basename "$dst")"
}

# Copy configure script to target folder
copy_file "$SRC_CONFIGURE" "$TARGET/configure-nginx-weather.reb00t.io.sh"

# Copy nginx conf to target/remote/nginx
mkdir -p "$TARGET/remote/nginx"
copy_file "$SRC_CONF" "$TARGET/remote/nginx/weather.reb00t.io.conf"

# Run the configure script
cd "$TARGET"
echo "Running configure script in $TARGET ..."
bash configure-nginx-weather.reb00t.io.sh
