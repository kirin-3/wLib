#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# wLib Launcher — finds or creates a venv, installs deps, and runs the app.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -n "${WLIB_DATA_DIR:-}" ]; then
    DATA_DIR="$WLIB_DATA_DIR"
    # Match get_data_dir(): expand a leading ~ before using the path.
    case "$DATA_DIR" in
        "~") DATA_DIR="$HOME" ;;
        "~/"*) DATA_DIR="$HOME/${DATA_DIR#\~/}" ;;
    esac
else
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wLib"
    # Same fallback as get_data_dir(): keep a pre-XDG library. Resolve before the
    # mkdir below, or creating the XDG folder would hide the existing library.
    if [ ! -d "$DATA_DIR" ] && [ -d "$HOME/.local/share/wLib" ]; then
        DATA_DIR="$HOME/.local/share/wLib"
    fi
fi
export WLIB_DATA_DIR="$DATA_DIR"
mkdir -p "$DATA_DIR"

if [ -w "$SCRIPT_DIR" ]; then
    VENV_DIR="$SCRIPT_DIR/venv"
else
    VENV_DIR="$DATA_DIR/venv"
fi
PYTHON=""

# Find a supported Python (3.12 or newer, matching CI and releases). Older
# interpreters cannot import modules such as typing.override.
for candidate in python3.12 python3.13 python3.14 python3; do
    if ! command -v "$candidate" &>/dev/null; then
        continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Error: No supported Python installation found (3.12 or newer)."
    echo "   Please install Python 3.12+ and the venv module using your system package manager."
    exit 1
fi

echo "🎮 wLib — Game Manager"
echo "   Using Python: $PYTHON"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    "$PYTHON" -m venv --system-site-packages "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Install/update deps if needed
if [ ! -f "$VENV_DIR/.deps_installed" ] || [ requirements.txt -nt "$VENV_DIR/.deps_installed" ]; then
    echo "📥 Installing dependencies..."
    pip install --quiet --upgrade pip
    if ! pip install --quiet -r requirements.txt; then
        echo "❌ Error: Failed to install Python dependencies."
        echo "   If PyGObject or PyQt failed to build, you may be missing system UI headers."
        echo "   Please install them via your distribution's package manager:"
        echo ""
        echo "   Ubuntu/Debian: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-pyqt6"
        echo "   Fedora/RHEL:   sudo dnf install python3-gobject gtk3 python3-qt6"
        echo "   Arch/Manjaro:  sudo pacman -S python-gobject gtk3 python-pyqt6"
        echo ""
        exit 1
    fi

    touch "$VENV_DIR/.deps_installed"
fi

# Build the frontend when it is missing or older than its sources (after a
# git pull, for example). A checkout without ui/src (a packaged copy) only
# needs dist to exist.
frontend_is_stale() {
    local built="$SCRIPT_DIR/ui/dist/index.html"
    [ -f "$built" ] || return 0
    [ -d "$SCRIPT_DIR/ui/src" ] || return 1
    local newer
    newer="$(find "$SCRIPT_DIR/ui/src" "$SCRIPT_DIR/ui/public" \
        "$SCRIPT_DIR/ui/index.html" "$SCRIPT_DIR/ui/package-lock.json" \
        "$SCRIPT_DIR/ui/vite.config.js" -newer "$built" -print -quit 2>/dev/null)"
    [ -n "$newer" ]
}

if frontend_is_stale; then
    if command -v npm &>/dev/null; then
        echo "🔨 Building frontend..."
        cd "$SCRIPT_DIR/ui"
        npm ci --silent
        npm run build
        cd "$SCRIPT_DIR"
    else
        echo "⚠️  Warning: npm not found, cannot build frontend."
        echo "   Install Node.js 22.12+ or pre-build with: cd ui && npm ci && npm run build"
    fi
fi

CRASH_GUARD_FILE="$DATA_DIR/.gpu_crash_guard"
GPU_SOFTWARE_FROM_CRASH_GUARD=0
GPU_RENDER_MODE="software"
GPU_DETECTION_REASON="uninitialized"
GPU_DETECTED_RENDERER=""
GPU_DIRECT_RENDERING=""
GPU_VENDOR_MATCH=""

# The guard holds the PID of the launcher whose GPU run is in progress. A
# second launch (which only focuses the running window and exits) must not
# read a live instance's guard as a crash, nor delete it on its way out.
guard_owned_by_other_instance() {
    local pid=""
    pid="$(head -n 1 "$1" 2>/dev/null)" || return 1
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$pid" != "$$" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    grep -qa wlib "/proc/$pid/cmdline" 2>/dev/null
}

detect_gpu() {
    local glxinfo_output=""
    local renderer_line=""
    local direct_line=""
    local vendor_id=""
    local vendor_path=""

    if [ "${LIBGL_ALWAYS_SOFTWARE:-}" = "1" ]; then
        GPU_RENDER_MODE="software"
        GPU_DETECTION_REASON="env:LIBGL_ALWAYS_SOFTWARE"
        export QT_QUICK_BACKEND="software"
        return
    fi

    case "${GALLIUM_DRIVER:-}" in
        llvmpipe|softpipe)
            GPU_RENDER_MODE="software"
            GPU_DETECTION_REASON="env:GALLIUM_DRIVER=${GALLIUM_DRIVER}"
            export QT_QUICK_BACKEND="software"
            return
            ;;
    esac

    if [ -f "$CRASH_GUARD_FILE" ] && ! guard_owned_by_other_instance "$CRASH_GUARD_FILE"; then
        GPU_SOFTWARE_FROM_CRASH_GUARD=1
        GPU_RENDER_MODE="software"
        GPU_DETECTION_REASON="crash-guard"
        export QT_QUICK_BACKEND="software"
        return
    fi

    if command -v glxinfo >/dev/null 2>&1; then
        glxinfo_output="$(glxinfo -B 2>/dev/null || true)"
        renderer_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'OpenGL renderer string:' || true)"
        direct_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'direct rendering:' || true)"
        GPU_DETECTED_RENDERER="${renderer_line#*: }"
        GPU_DIRECT_RENDERING="${direct_line#*: }"

        if printf '%s\n' "$renderer_line" | grep -qiE 'llvmpipe|softpipe|swrast|d3d12|svga3d'; then
            GPU_RENDER_MODE="software"
            GPU_DETECTION_REASON="glxinfo:software-renderer"
            export QT_QUICK_BACKEND="software"
            return
        fi

        if [ -n "$renderer_line" ] && printf '%s\n' "$direct_line" | grep -qi 'yes'; then
            GPU_RENDER_MODE="auto"
            GPU_DETECTION_REASON="glxinfo:direct-rendering"
            unset QT_QUICK_BACKEND
            return
        fi
    fi

    if ls /dev/dri/renderD* >/dev/null 2>&1; then
        GPU_RENDER_MODE="auto"
        GPU_DETECTION_REASON="drm:render-node"
        unset QT_QUICK_BACKEND
        return
    fi

    for vendor_path in /sys/class/drm/card*/device/vendor; do
        [ -f "$vendor_path" ] || continue
        vendor_id="$(tr '[:upper:]' '[:lower:]' < "$vendor_path" 2>/dev/null || true)"
        case "$vendor_id" in
            0x10de|0x1002|0x8086)
                GPU_VENDOR_MATCH="$vendor_id"
                GPU_RENDER_MODE="auto"
                GPU_DETECTION_REASON="drm:vendor=${vendor_id}"
                unset QT_QUICK_BACKEND
                return
                ;;
        esac
    done

    GPU_RENDER_MODE="software"
    GPU_DETECTION_REASON="fallback:software"
    export QT_QUICK_BACKEND="software"
}

# Run wLib
echo "🚀 Starting wLib..."
detect_gpu
export DEV_MODE=0
echo "   QT_QUICK_BACKEND=$QT_QUICK_BACKEND"
echo "   Renderer mode: $GPU_RENDER_MODE"
echo "   Renderer probe: reason=$GPU_DETECTION_REASON crash_guard=$GPU_SOFTWARE_FROM_CRASH_GUARD"
if [ -n "$GPU_DETECTED_RENDERER" ]; then
    echo "   OpenGL renderer: $GPU_DETECTED_RENDERER"
fi
if [ -n "$GPU_DIRECT_RENDERING" ]; then
    echo "   Direct rendering: $GPU_DIRECT_RENDERING"
fi
if [ -n "$GPU_VENDOR_MATCH" ]; then
    echo "   DRM vendor match: $GPU_VENDOR_MATCH"
fi

if [ "$GPU_RENDER_MODE" = "auto" ] && ! guard_owned_by_other_instance "$CRASH_GUARD_FILE"; then
    printf '%s\n' "$$" > "$CRASH_GUARD_FILE"
fi

set +e
# Run in the background so this script can pass on a stop signal (logout,
# `kill`, a closing terminal): wLib then closes its window and saves playtime
# and the last sync instead of being cut off.
python main.py &
APP_PID=$!
trap 'kill -TERM "$APP_PID" 2>/dev/null' TERM HUP
trap '' INT  # Ctrl+C already reaches wLib through the process group.
while kill -0 "$APP_PID" 2>/dev/null; do
    wait "$APP_PID"
done
wait "$APP_PID"
APP_EXIT_CODE=$?
trap - TERM HUP INT
set -e

if [ "$APP_EXIT_CODE" -eq 0 ] \
    && { [ "$GPU_RENDER_MODE" = "auto" ] || [ "$GPU_SOFTWARE_FROM_CRASH_GUARD" = "1" ]; } \
    && ! guard_owned_by_other_instance "$CRASH_GUARD_FILE"; then
    rm -f "$CRASH_GUARD_FILE"
fi

exit "$APP_EXIT_CODE"
