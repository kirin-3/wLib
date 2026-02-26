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

# Find a suitable Python 3.x
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Error: No Python 3 installation found."
    echo "   Please install Python 3.11+ and the venv module using your system package manager."
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
GPU_SELECTION_SOURCE=""

detect_gpu() {
    local renderer=""
    local renderer_lc=""
    local qt_override="${QT_QUICK_BACKEND:-}"

    if [ -n "$qt_override" ]; then
        qt_override="${qt_override,,}"
        case "$qt_override" in
            opengl|software)
                export QT_QUICK_BACKEND="$qt_override"
                GPU_SELECTION_SOURCE="env"
                return
                ;;
        esac
    fi

    if [ -f "$CRASH_GUARD_FILE" ]; then
        export QT_QUICK_BACKEND="software"
        GPU_SELECTION_SOURCE="crash_guard"
        return
    fi

    if command -v glxinfo >/dev/null 2>&1; then
        while IFS= read -r line; do
            case "$line" in
                OpenGL\ renderer\ string:*)
                    renderer="${line#OpenGL renderer string: }"
                    break
                    ;;
            esac
        done < <(glxinfo -B 2>/dev/null || true)

        if [ -n "$renderer" ]; then
            renderer_lc="${renderer,,}"
            case "$renderer_lc" in
                *llvmpipe*|*softpipe*|*swrast*|*d3d12*|*svga3d*)
                    export QT_QUICK_BACKEND="software"
                    GPU_SELECTION_SOURCE="glxinfo_software"
                    return
                    ;;
                *)
                    export QT_QUICK_BACKEND="opengl"
                    GPU_SELECTION_SOURCE="glxinfo_hardware"
                    return
                    ;;
            esac
        fi
    fi

    if [ -d /dev/dri ]; then
        for vendor_file in /sys/class/drm/card*/device/vendor; do
            [ -r "$vendor_file" ] || continue
            vendor_id="$(tr '[:upper:]' '[:lower:]' < "$vendor_file" 2>/dev/null || true)"
            case "$vendor_id" in
                0x8086|0x1002|0x1022|0x10de|0x1af4)
                    export QT_QUICK_BACKEND="opengl"
                    GPU_SELECTION_SOURCE="sysfs_vendor"
                    return
                    ;;
            esac
        done

        export QT_QUICK_BACKEND="opengl"
        GPU_SELECTION_SOURCE="dri_device"
        return
    fi

    export QT_QUICK_BACKEND="software"
    GPU_SELECTION_SOURCE="fallback_software"
}

# Run wLib
echo "🚀 Starting wLib..."
export DEV_MODE=0
detect_gpu
echo "   QT_QUICK_BACKEND=$QT_QUICK_BACKEND (source: $GPU_SELECTION_SOURCE)"

if [ "$QT_QUICK_BACKEND" = "opengl" ] && [ "$GPU_SELECTION_SOURCE" != "env" ]; then
    touch "$CRASH_GUARD_FILE"
fi

set +e
python main.py
APP_EXIT_CODE=$?
set -e

if [ "$APP_EXIT_CODE" -eq 0 ] && [ "$QT_QUICK_BACKEND" = "opengl" ] && [ "$GPU_SELECTION_SOURCE" != "env" ]; then
    rm -f "$CRASH_GUARD_FILE"
fi

if [ "$APP_EXIT_CODE" -eq 0 ] && [ "$GPU_SELECTION_SOURCE" = "crash_guard" ]; then
    rm -f "$CRASH_GUARD_FILE"
fi

exit "$APP_EXIT_CODE"
