#!/usr/bin/env bash
# wLib Launcher — finds or creates a venv, installs deps, and runs the app.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
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
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Install/update deps if needed
if [ ! -f "$VENV_DIR/.deps_installed" ] || [ requirements.txt -nt "$VENV_DIR/.deps_installed" ]; then
    echo "📥 Installing dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt

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

# Run wLib
echo "🚀 Starting wLib..."
export DEV_MODE=0
exec python main.py
