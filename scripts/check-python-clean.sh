#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

resolve_python() {
    local requested="${WLIB_PYTHON_BIN:-}"

    if [[ -n "$requested" ]]; then
        echo "$requested"
        return
    fi

    for candidate in python3.12 python3; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi

        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
            echo "$candidate"
            return
        fi
    done

    return 1
}

PYTHON_BIN="$(resolve_python)" || {
    echo "Python 3.12 is required for clean backend verification." >&2
    exit 1
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
VENV_DIR="$WORK_DIR/.venv"

echo "==> Creating clean Python 3.12 environment"
"$PYTHON_BIN" -m venv "$VENV_DIR"

"$VENV_DIR/bin/python" --version
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements-dev.txt

PYTHON_BIN="$VENV_DIR/bin/python" \
PIP_BIN="$VENV_DIR/bin/pip" \
BASEDPYRIGHT_BIN="$VENV_DIR/bin/basedpyright" \
PYTEST_BIN="$VENV_DIR/bin/pytest" \
    bash scripts/check-python.sh
