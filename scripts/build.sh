#!/usr/bin/env bash
# wLib Build Script — produces tar.gz and AppImage packages using PyInstaller
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

VERSION="${1:-dev}"
APP_NAME="wLib"
BUILD_DIR="$PROJECT_DIR/build"
DIST_DIR="$PROJECT_DIR/dist"
PACKAGE_NAME="${APP_NAME}-${VERSION}-linux-x86_64"

echo "🔨 Building $APP_NAME $VERSION"

# ── Clean ──
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR/$PACKAGE_NAME" "$DIST_DIR"

# ── Build Vue Frontend ──
echo "📦 Building frontend..."
cd "$PROJECT_DIR/ui"
npm ci --silent
npm run build
cd "$PROJECT_DIR"

# ── Ensure PyInstaller & deps are available ──
echo "📦 Installing Python dependencies for build..."
pip install -r requirements.txt

# ── Build Python Backend with PyInstaller ──
echo "🐍 Building Python binary with PyInstaller..."
# We use pywebview, which requires its own assets depending on the engine.
# We explicitly bundle core module and ui/dist.
pyinstaller --noconfirm --onedir \
    --name "wlib-bin" \
    --add-data "core:core" \
    --add-data "ui/dist:ui/dist" \
    --add-data "extension:extension" \
    --add-data "wlib.png:." \
    --hidden-import "core" \
    --hidden-import "playwright" \
    --hidden-import "playwright.sync_api" \
    --hidden-import "pywebview" \
    --hidden-import "PyQt6" \
    --hidden-import "webview.platforms.qt" \
    --exclude-module "gi" \
    --exclude-module "webview.platforms.gtk" \
    --exclude-module "webview.platforms.gtkwebkit2" \
    main.py

# Clean up system libraries bundled by PyInstaller that break host graphics drivers (e.g. Vulkan/OpenGL)
# These MUST use the host's native versions to work with the host's GPU drivers.
echo "🧹 Removing conflicting bundled system libraries..."
find dist/wlib-bin -name "libstdc++.so.6" -exec rm -f {} + || true
find dist/wlib-bin -name "libgcc_s.so.1" -exec rm -f {} + || true
find dist/wlib-bin -name "libxcb*" -exec rm -f {} + || true
find dist/wlib-bin -name "libEGL*" -exec rm -f {} + || true
find dist/wlib-bin -name "libGLESv2*" -exec rm -f {} + || true
find dist/wlib-bin -name "libvulkan*" -exec rm -f {} + || true
find dist/wlib-bin -name "libdrm*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgbm*" -exec rm -f {} + || true

# Remove bundled GLib/GIO/GTK stack so the host runtime provides these libs.
# This avoids GLIBC version mismatches from host-built artifacts on older distros.
find dist/wlib-bin -name "libglib-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgobject-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgio-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgthread-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgmodule-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgirepository-2.0.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgtk-3.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgdk-3.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libgdk_pixbuf-2.0.so*" -exec rm -f {} + || true
rm -rf dist/wlib-bin/_internal/gi dist/wlib-bin/_internal/gi_typelibs dist/wlib-bin/_internal/gio_modules || true

# Move the built binary to the package folder
cp -r dist/wlib-bin/* "$BUILD_DIR/$PACKAGE_NAME/"

# ── Assemble Package ──
echo "📋 Assembling package..."

# Desktop file + icon
cp wlib.desktop "$BUILD_DIR/$PACKAGE_NAME/"
if [ -f "wlib.png" ]; then cp wlib.png "$BUILD_DIR/$PACKAGE_NAME/"; fi

# Clean up pyinstaller temp files
rm -rf build/wlib-bin dist/wlib-bin wlib-bin.spec

# ── Create tar.gz ──
echo "📦 Creating tar.gz..."
cd "$BUILD_DIR"
tar -czf "$DIST_DIR/${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"
echo "   ✅ $DIST_DIR/${PACKAGE_NAME}.tar.gz"

# ── Create AppImage ──
echo "🖼  Creating AppImage..."

APPDIR="$BUILD_DIR/${APP_NAME}.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy the full app into the AppDir
cp -r "$BUILD_DIR/$PACKAGE_NAME"/* "$APPDIR/usr/bin/"

# Desktop file + icon
cp "$PROJECT_DIR/wlib.desktop" "$APPDIR/usr/share/applications/"
cp "$PROJECT_DIR/wlib.desktop" "$APPDIR/"

# Use the official SVG icon
if [ -f "$PROJECT_DIR/icon.svg" ]; then
    mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
    cp "$PROJECT_DIR/icon.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/wlib.svg"
    cp "$PROJECT_DIR/icon.svg" "$APPDIR/wlib.svg"
    cp "$PROJECT_DIR/icon.svg" "$APPDIR/.DirIcon"
fi

# AppRun — the entry point for the AppImage
cat > "$APPDIR/AppRun" << 'APPRUN_EOF'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
export PATH="$SELF_DIR/usr/bin:$PATH"
cd "$SELF_DIR/usr/bin"

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/wLib"
LOG_FILE="$DATA_DIR/appimage-launch.log"

mkdir -p "$DATA_DIR"

restore_host_library_path() {
    if [ -n "${LD_LIBRARY_PATH_ORIG:-}" ]; then
        export LD_LIBRARY_PATH="$LD_LIBRARY_PATH_ORIG"
    else
        unset LD_LIBRARY_PATH
    fi
}

log_launch_context() {
    {
        printf '\n=== %s ===\n' "$(date -Is 2>/dev/null || date)"
        printf 'launcher=AppImage\n'
        printf 'session_type=%s\n' "${XDG_SESSION_TYPE:-}"
        printf 'display=%s\n' "${DISPLAY:-}"
        printf 'wayland_display=%s\n' "${WAYLAND_DISPLAY:-}"
        printf 'qt_qpa_platform=%s\n' "${QT_QPA_PLATFORM:-<unset>}"
        printf 'qt_quick_backend=%s\n' "${QT_QUICK_BACKEND:-<unset>}"
        printf 'qt_opengl=%s\n' "${QT_OPENGL:-<unset>}"
        printf 'qtwebengine_flags=%s\n' "${QTWEBENGINE_CHROMIUM_FLAGS:-<unset>}"
        printf 'ld_library_path=%s\n' "${LD_LIBRARY_PATH:-<unset>}"
        printf 'ld_library_path_orig=%s\n' "${LD_LIBRARY_PATH_ORIG:-<unset>}"
    } >> "$LOG_FILE"
}

GPU_CRASH_GUARD="${XDG_DATA_HOME:-$HOME/.local/share}/wLib/.gpu_crash_guard"
GPU_SOFTWARE_FROM_CRASH_GUARD=0

detect_gpu() {
    local glxinfo_output=""
    local renderer_line=""
    local direct_line=""
    local vendor_id=""
    local vendor_path=""

    if [ "${LIBGL_ALWAYS_SOFTWARE:-}" = "1" ]; then
        export QT_QUICK_BACKEND=software
        return
    fi

    case "${GALLIUM_DRIVER:-}" in
        llvmpipe|softpipe)
            export QT_QUICK_BACKEND=software
            return
            ;;
    esac

    if [ -f "$GPU_CRASH_GUARD" ]; then
        GPU_SOFTWARE_FROM_CRASH_GUARD=1
        export QT_QUICK_BACKEND=software
        return
    fi

    if command -v glxinfo >/dev/null 2>&1; then
        glxinfo_output="$(glxinfo -B 2>/dev/null || true)"
        renderer_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'OpenGL renderer string:' || true)"
        direct_line="$(printf '%s\n' "$glxinfo_output" | grep -i 'direct rendering:' || true)"

        if printf '%s\n' "$renderer_line" | grep -qiE 'llvmpipe|softpipe|swrast|d3d12|svga3d'; then
            export QT_QUICK_BACKEND=software
            return
        fi

        if [ -n "$renderer_line" ] && printf '%s\n' "$direct_line" | grep -qi 'yes'; then
            export QT_QUICK_BACKEND=opengl
            return
        fi
    fi

    if ls /dev/dri/renderD* >/dev/null 2>&1; then
        export QT_QUICK_BACKEND=opengl
        return
    fi

    for vendor_path in /sys/class/drm/card*/device/vendor; do
        [ -f "$vendor_path" ] || continue
        vendor_id="$(tr '[:upper:]' '[:lower:]' < "$vendor_path" 2>/dev/null || true)"
        case "$vendor_id" in
            0x10de|0x1002|0x8086)
                export QT_QUICK_BACKEND=opengl
                return
                ;;
        esac
    done

    export QT_QUICK_BACKEND=software
}

detect_gpu
if [ "$QT_QUICK_BACKEND" = "opengl" ]; then
    touch "$GPU_CRASH_GUARD"
fi

# Let the app set QT_QPA_PLATFORM based on the active desktop session.
# Forcing xcb inside Wayland sessions can make Qt windows disappear on focus changes.

# Ensure the bundled Qt does not shadow the host's native GPU libraries.
restore_host_library_path
export WLIB_APPIMAGE_LAUNCH_LOG="$LOG_FILE"
log_launch_context

# Playwright browsers are installed to ~/.cache/ms-playwright at first run.
# This is handled automatically by the app.

if [ -t 1 ] || [ -t 2 ]; then
    ./wlib-bin "$@" > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
else
    ./wlib-bin "$@" >> "$LOG_FILE" 2>&1
fi
WLIB_EXIT=$?
printf 'exit_code=%s\n' "$WLIB_EXIT" >> "$LOG_FILE"
if [ "$WLIB_EXIT" -eq 0 ] && { [ "$QT_QUICK_BACKEND" = "opengl" ] || [ "$GPU_SOFTWARE_FROM_CRASH_GUARD" = "1" ]; }; then
    rm -f "$GPU_CRASH_GUARD"
fi
exit "$WLIB_EXIT"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# Download appimagetool if not present
APPIMAGETOOL="$BUILD_DIR/appimagetool"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "   Downloading appimagetool..."
    ARCH=$(uname -m)
    curl -sSL "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# Build the AppImage
cd "$DIST_DIR"
ARCH=$(uname -m) "$APPIMAGETOOL" "$APPDIR" "${PACKAGE_NAME}.AppImage" 2>/dev/null || {
    # If FUSE is not available (common in CI), extract and run
    echo "   FUSE not available, extracting appimagetool..."
    cd "$BUILD_DIR"
    "$APPIMAGETOOL" --appimage-extract >/dev/null 2>&1
    cd "$DIST_DIR"
    ARCH=$(uname -m) "$BUILD_DIR/squashfs-root/AppRun" "$APPDIR" "${PACKAGE_NAME}.AppImage"
}
echo "   ✅ $DIST_DIR/${PACKAGE_NAME}.AppImage"

echo ""
echo "🎉 Build complete! Artifacts in $DIST_DIR/"
ls -lh "$DIST_DIR/"
