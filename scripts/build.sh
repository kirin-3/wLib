#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# wLib Build Script — produces tar.gz and AppImage packages using PyInstaller
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

VERSION="${1:-dev}"
SIGNED_FIREFOX_XPI="${WLIB_SIGNED_FIREFOX_XPI:-${2:-}}"
APP_NAME="wLib"
BUILD_DIR="$PROJECT_DIR/build"
DIST_DIR="$PROJECT_DIR/dist"
STAGED_EXTENSION_DIR="$BUILD_DIR/extension"
PACKAGE_NAME="${APP_NAME}-${VERSION}-linux-x86_64"
PACKAGE_VERSION="${VERSION#v}"
WLIB_BUILD_NATIVE_PACKAGES="${WLIB_BUILD_NATIVE_PACKAGES:-auto}"
WLIB_BUILD_AUR="${WLIB_BUILD_AUR:-auto}"
WLIB_PACKAGE_RELEASE="${WLIB_PACKAGE_RELEASE:-1}"

echo "🔨 Building $APP_NAME $VERSION"

is_valid_package_version() {
    [[ "$PACKAGE_VERSION" =~ ^[0-9][0-9A-Za-z._+~-]*$ ]]
}

aur_pkgver() {
    printf '%s' "$PACKAGE_VERSION" | tr '-' '_'
}

require_package_metadata_if_needed() {
    if [ "${WLIB_REQUIRE_PACKAGE_METADATA:-0}" = "1" ] && [ -z "${WLIB_MAINTAINER:-}" ]; then
        echo "WLIB_MAINTAINER is required for release package metadata." >&2
        exit 1
    fi
}

should_build_native_packages() {
    case "$WLIB_BUILD_NATIVE_PACKAGES" in
        1|true|yes)
            return 0
            ;;
        0|false|no)
            return 1
            ;;
        auto)
            command -v nfpm >/dev/null 2>&1 && is_valid_package_version
            ;;
        *)
            echo "Invalid WLIB_BUILD_NATIVE_PACKAGES value: $WLIB_BUILD_NATIVE_PACKAGES" >&2
            exit 1
            ;;
    esac
}

should_generate_aur_metadata() {
    case "$WLIB_BUILD_AUR" in
        1|true|yes)
            return 0
            ;;
        0|false|no)
            return 1
            ;;
        auto)
            is_valid_package_version
            ;;
        *)
            echo "Invalid WLIB_BUILD_AUR value: $WLIB_BUILD_AUR" >&2
            exit 1
            ;;
    esac
}

build_native_packages() {
    if ! should_build_native_packages; then
        echo "📦 Skipping .deb/.rpm package build (WLIB_BUILD_NATIVE_PACKAGES=$WLIB_BUILD_NATIVE_PACKAGES)"
        return
    fi

    if ! command -v nfpm >/dev/null 2>&1; then
        echo "nFPM is required to build native packages." >&2
        exit 1
    fi

    if ! is_valid_package_version; then
        echo "Native package versions must start with a digit; got: $PACKAGE_VERSION" >&2
        exit 1
    fi

    require_package_metadata_if_needed

    echo "📦 Creating .deb and .rpm packages..."
    export WLIB_PACKAGE_SOURCE="$BUILD_DIR/$PACKAGE_NAME"
    export WLIB_PACKAGE_VERSION="$PACKAGE_VERSION"
    export WLIB_PACKAGE_RELEASE
    local nfpm_config="$BUILD_DIR/nfpm.yaml"
    local maintainer="${WLIB_MAINTAINER:-wLib Maintainers <maintainers@example.invalid>}"
    local escaped_maintainer
    escaped_maintainer="$(printf '%s' "$maintainer" | sed -e 's/[&|]/\\&/g')"
    sed \
        -e "s|^maintainer: .*|maintainer: \"$escaped_maintainer\"|" \
        "$PROJECT_DIR/packaging/nfpm.yaml" > "$nfpm_config"

    (
        cd "$PROJECT_DIR"
        nfpm package \
            --config "$nfpm_config" \
            --packager deb \
            --target "$DIST_DIR/${PACKAGE_NAME}.deb"

        nfpm package \
            --config "$nfpm_config" \
            --packager rpm \
            --target "$DIST_DIR/${PACKAGE_NAME}.rpm"
    )

    echo "   ✅ $DIST_DIR/${PACKAGE_NAME}.deb"
    echo "   ✅ $DIST_DIR/${PACKAGE_NAME}.rpm"
}

generate_aur_metadata() {
    if ! should_generate_aur_metadata; then
        echo "📦 Skipping AUR metadata generation (WLIB_BUILD_AUR=$WLIB_BUILD_AUR)"
        return
    fi

    local pkgver
    pkgver="$(aur_pkgver)"
    if [[ ! "$pkgver" =~ ^[0-9][0-9A-Za-z._+~]*$ ]]; then
        echo "AUR pkgver contains unsupported characters after normalization; got: $pkgver" >&2
        exit 1
    fi

    require_package_metadata_if_needed

    echo "📦 Generating AUR wlib-bin metadata..."
    "$PROJECT_DIR/scripts/generate-aur-package.sh" "$VERSION"
}

# ── Clean ──
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR/$PACKAGE_NAME" "$DIST_DIR"

# ── Stage Browser Extension ──
echo "🧩 Staging browser extension..."
cp -R "$PROJECT_DIR/extension" "$STAGED_EXTENSION_DIR"
rm -rf "$STAGED_EXTENSION_DIR/firefox"

if [ -n "$SIGNED_FIREFOX_XPI" ]; then
    if [ ! -f "$SIGNED_FIREFOX_XPI" ]; then
        echo "Signed Firefox XPI not found: $SIGNED_FIREFOX_XPI" >&2
        exit 1
    fi

    mkdir -p "$STAGED_EXTENSION_DIR/firefox"
    cp "$SIGNED_FIREFOX_XPI" "$STAGED_EXTENSION_DIR/firefox/wLib.xpi"
fi

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
export QT_API="${QT_API:-pyqt6}"
# We use pywebview, which requires its own assets depending on the engine.
# We explicitly bundle core module and ui/dist.
pyinstaller --noconfirm --onedir \
    --name "wlib-bin" \
    --add-data "core:core" \
    --add-data "ui/dist:ui/dist" \
    --add-data "$STAGED_EXTENSION_DIR:extension" \
    --add-data "wlib.png:." \
    --collect-data "certifi" \
    --hidden-import "core" \
    --hidden-import "certifi" \
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
find dist/wlib-bin -name "libpcre2*.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libreadline*.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libhistory*.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libncurses*.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libtinfo*.so*" -exec rm -f {} + || true
find dist/wlib-bin -name "libedit*.so*" -exec rm -f {} + || true
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
if [ -f "icon.svg" ]; then cp icon.svg "$BUILD_DIR/$PACKAGE_NAME/"; fi
cp LICENSE "$BUILD_DIR/$PACKAGE_NAME/"
install -m 755 packaging/wlib-launcher "$BUILD_DIR/$PACKAGE_NAME/wlib"

# Clean up pyinstaller temp files
rm -rf build/wlib-bin dist/wlib-bin wlib-bin.spec

# ── Create tar.gz ──
echo "📦 Creating tar.gz..."
cd "$BUILD_DIR"
tar -czf "$DIST_DIR/${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"
echo "   ✅ $DIST_DIR/${PACKAGE_NAME}.tar.gz"

build_native_packages
generate_aur_metadata

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
export WLIB_APP_DIR="$SELF_DIR/usr/bin"
export WLIB_LAUNCHER_NAME="AppImage"
export WLIB_LAUNCH_LOG="${XDG_DATA_HOME:-$HOME/.local/share}/wLib/appimage-launch.log"
exec "$SELF_DIR/usr/bin/wlib" "$@"
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
