#!/usr/bin/env bash
# wLib Launcher — finds or creates a venv, installs deps, and runs the app.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wLib"
mkdir -p "$DATA_DIR"

if [ -w "$SCRIPT_DIR" ]; then
    VENV_DIR="$SCRIPT_DIR/venv"
else
    VENV_DIR="$DATA_DIR/venv"
fi
PYTHON=""

# Find a suitable Python 3.x, preferring the supported 3.12 toolchain.
for candidate in python3.12 python3.13 python3.14 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Error: No Python 3 installation found."
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

    # Install Playwright browsers if not present
    if ! python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
        echo "🌐 Installing Playwright browser..."
        playwright install chromium
    fi

    touch "$VENV_DIR/.deps_installed"
fi

# Build frontend if dist doesn't exist
if [ ! -d "$SCRIPT_DIR/ui/dist" ]; then
    if command -v npm &>/dev/null; then
        echo "🔨 Building frontend..."
        cd "$SCRIPT_DIR/ui"
        npm install --silent
        npm run build
        cd "$SCRIPT_DIR"
    else
        echo "⚠️  Warning: npm not found, cannot build frontend."
        echo "   Install Node.js 18+ or pre-build with: cd ui && npm install && npm run build"
    fi
fi

CRASH_GUARD_FILE="$DATA_DIR/.gpu_crash_guard"
GPU_SOFTWARE_FROM_CRASH_GUARD=0

detect_gpu() {
    local glxinfo_output=""
    local renderer_line=""
    local direct_line=""
    local vendor_id=""
    local vendor_path=""

    if [ "${LIBGL_ALWAYS_SOFTWARE:-}" = "1" ]; then
        export QT_QUICK_BACKEND="software"
        return
    fi

    case "${GALLIUM_DRIVER:-}" in
        llvmpipe|softpipe)
            export QT_QUICK_BACKEND="software"
            return
            ;;
    esac

    if [ -f "$CRASH_GUARD_FILE" ]; then
        GPU_SOFTWARE_FROM_CRASH_GUARD=1
        export QT_QUICK_BACKEND="software"
        return
    fi

    if command -v glxinfo >/dev/null 2>&1; then
        glxinfo_output="$(glxinfo -B 2>/dev/null || true)"
        renderer_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'OpenGL renderer string:' || true)"
        direct_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'direct rendering:' || true)"

        if printf '%s\n' "$renderer_line" | grep -qiE 'llvmpipe|softpipe|swrast|d3d12|svga3d'; then
            export QT_QUICK_BACKEND="software"
            return
        fi

        if [ -n "$renderer_line" ] && printf '%s\n' "$direct_line" | grep -qi 'yes'; then
            export QT_QUICK_BACKEND="opengl"
            return
        fi
    fi

    if ls /dev/dri/renderD* >/dev/null 2>&1; then
        export QT_QUICK_BACKEND="opengl"
        return
    fi

    for vendor_path in /sys/class/drm/card*/device/vendor; do
        [ -f "$vendor_path" ] || continue
        vendor_id="$(tr '[:upper:]' '[:lower:]' < "$vendor_path" 2>/dev/null || true)"
        case "$vendor_id" in
            0x10de|0x1002|0x8086)
                export QT_QUICK_BACKEND="opengl"
                return
                ;;
        esac
    done

    export QT_QUICK_BACKEND="software"
}

# Run wLib
echo "🚀 Starting wLib..."
detect_gpu
export DEV_MODE=0
echo "   QT_QUICK_BACKEND=$QT_QUICK_BACKEND"

if [ "$QT_QUICK_BACKEND" = "opengl" ]; then
    touch "$CRASH_GUARD_FILE"
fi

set +e
python main.py
APP_EXIT_CODE=$?
set -e

if [ "$APP_EXIT_CODE" -eq 0 ] && [ "$QT_QUICK_BACKEND" = "opengl" ]; then
    rm -f "$CRASH_GUARD_FILE"
fi

if [ "$APP_EXIT_CODE" -eq 0 ] && [ "$GPU_SOFTWARE_FROM_CRASH_GUARD" = "1" ]; then
    rm -f "$CRASH_GUARD_FILE"
fi

exit "$APP_EXIT_CODE"
