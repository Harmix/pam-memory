#!/usr/bin/env bash
# Hook launcher: resolve bundled SDK + deps, always fail-open (exit 0).
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPTS_DIR="${PLUGIN_ROOT}/scripts"
VENDOR_DIR="${PLUGIN_ROOT}/vendor"
MONOREPO_SDK="${PLUGIN_ROOT}/../pam-python-sdk/src"

"${SCRIPTS_DIR}/ensure_deps.sh" >/dev/null 2>&1 || true

PYTHONPATH_PARTS=("${SCRIPTS_DIR}" "${VENDOR_DIR}")
if [ -d "${MONOREPO_SDK}/pam" ]; then
  PYTHONPATH_PARTS+=("${MONOREPO_SDK}")
fi
export PYTHONPATH
PYTHONPATH=$(IFS=:; echo "${PYTHONPATH_PARTS[*]}${PYTHONPATH:+:${PYTHONPATH}}")

DATA_DIR="${CLAUDE_PLUGIN_DATA:-${HOME}/.pam/plugin-data}"
VENV_PYTHON="${DATA_DIR}/venv/bin/python3"
if [ -x "${VENV_PYTHON}" ]; then
  PYTHON="${VENV_PYTHON}"
else
  PYTHON="$(command -v python3 || command -v python || echo python3)"
fi

"${PYTHON}" "${SCRIPTS_DIR}/retrieve_memory.py" "$@" || exit 0
exit 0
