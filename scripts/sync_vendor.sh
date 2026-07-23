#!/usr/bin/env bash
# Copy sdk/src/pam into plugin/vendor/pam (Claude plugin runtime).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/sdk/src/pam"
DEST="${ROOT}/plugin/vendor/pam"

if [ ! -d "${SRC}" ]; then
  echo "sync_vendor: missing ${SRC}" >&2
  exit 1
fi

mkdir -p "${DEST}"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${SRC}/" "${DEST}/"

echo "Synced ${SRC} -> ${DEST}"
