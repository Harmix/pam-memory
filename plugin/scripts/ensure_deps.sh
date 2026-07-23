#!/usr/bin/env bash
# Install hook runtime deps into a persistent venv under CLAUDE_PLUGIN_DATA.
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.pam/plugin-data}"
VENV_DIR="${DATA_DIR}/venv"
REQ_SRC="${PLUGIN_ROOT}/requirements.txt"
REQ_STAMP="${DATA_DIR}/requirements.txt"
PYTHON="${VENV_DIR}/bin/python3"

mkdir -p "${DATA_DIR}"

install_with_python() {
  local py="$1"
  "${py}" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "${py}" -m pip install --quiet -r "${REQ_SRC}" >/dev/null 2>&1
}

if [ ! -x "${PYTHON}" ] || ! diff -q "${REQ_SRC}" "${REQ_STAMP}" >/dev/null 2>&1; then
  if python3 -m venv "${VENV_DIR}" >/dev/null 2>&1 && [ -x "${PYTHON}" ]; then
    if install_with_python "${PYTHON}"; then
      cp "${REQ_SRC}" "${REQ_STAMP}"
    else
      echo "pam-memory plugin: failed to install Python dependencies in venv" >&2
    fi
  elif install_with_python python3; then
    cp "${REQ_SRC}" "${REQ_STAMP}"
  else
    echo "pam-memory plugin: failed to install Python dependencies" >&2
  fi
fi
