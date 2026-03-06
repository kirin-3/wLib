#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
PIP_BIN="${PIP_BIN:-pip}"
BASEDPYRIGHT_BIN="${BASEDPYRIGHT_BIN:-basedpyright}"
PYTEST_BIN="${PYTEST_BIN:-pytest}"

require_tool() {
    local tool="$1"

    if [[ "$tool" == */* ]]; then
        if [[ ! -x "$tool" ]]; then
            echo "Missing executable: $tool" >&2
            exit 1
        fi
        return
    fi

    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required tool on PATH: $tool" >&2
        exit 1
    fi
}

require_tool "$PYTHON_BIN"
require_tool "$PIP_BIN"
require_tool "$BASEDPYRIGHT_BIN"
require_tool "$PYTEST_BIN"

echo "==> Python version"
"$PYTHON_BIN" --version

echo "==> Installed pip"
"$PIP_BIN" --version

echo "==> basedpyright"
"$BASEDPYRIGHT_BIN"

echo "==> Backend smoke check"
"$PYTHON_BIN" scripts/smoke_backend.py

echo "==> pytest"
"$PYTEST_BIN"
