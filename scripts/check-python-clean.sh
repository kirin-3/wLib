#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required for clean backend verification: https://docs.astral.sh/uv/" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
VENV_DIR="$WORK_DIR/.venv"

echo "==> Creating clean Python 3.12 environment from uv.lock"
# WLIB_PYTHON_BIN picks an interpreter; uv otherwise uses .python-version.
UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv sync --locked \
    ${WLIB_PYTHON_BIN:+--python "$WLIB_PYTHON_BIN"}

"$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else "Python 3.12 is required for clean backend verification.")'

PYTHON_BIN="$VENV_DIR/bin/python" \
BASEDPYRIGHT_BIN="$VENV_DIR/bin/basedpyright" \
BASEDPYRIGHT_VENV_PATH="$WORK_DIR" \
RUFF_BIN="$VENV_DIR/bin/ruff" \
PYTEST_BIN="$VENV_DIR/bin/pytest" \
    bash scripts/check-python.sh
