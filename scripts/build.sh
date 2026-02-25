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
    --hidden-import "pywebview" \
    --hidden-import "PyQt6" \
    --hidden-import "webview.platforms.qt" \
    main.py

# Clean up system libraries bundled by PyInstaller that break host graphics drivers (e.g. Vulkan/OpenGL)
echo "🧹 Removing conflicting bundled system libraries..."
find dist/wlib-bin -name "libstdc++.so.6" -exec rm -f {} + || true
find dist/wlib-bin -name "libgcc_s.so.1" -exec rm -f {} + || true
find dist/wlib-bin -name "libxcb*" -exec rm -f {} + || true

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

# Fallback to X11 if Wayland EGL initialization fails
export QT_QPA_PLATFORM="xcb;wayland"

# Install playwright browsers if not present.
# Playwright uses its own cache logic.
./wlib-bin --install-playwright-if-needed || true

exec ./wlib-bin "$@"
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
