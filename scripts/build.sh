#!/usr/bin/env bash
# wLib Build Script — produces tar.gz and AppImage packages
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

# ── Assemble Package ──
echo "📋 Assembling package..."

# Core Python application
cp main.py "$BUILD_DIR/$PACKAGE_NAME/"
cp requirements.txt "$BUILD_DIR/$PACKAGE_NAME/"
cp wlib.sh "$BUILD_DIR/$PACKAGE_NAME/"
cp wlib.desktop "$BUILD_DIR/$PACKAGE_NAME/"
if [ -f "wlib.png" ]; then cp wlib.png "$BUILD_DIR/$PACKAGE_NAME/"; fi
chmod +x "$BUILD_DIR/$PACKAGE_NAME/wlib.sh"

# Core modules
cp -r core "$BUILD_DIR/$PACKAGE_NAME/"

# Built frontend
cp -r ui/dist "$BUILD_DIR/$PACKAGE_NAME/ui_dist"
# Also include ui/package.json etc. so the launcher knows it's pre-built
mkdir -p "$BUILD_DIR/$PACKAGE_NAME/ui"
cp -r ui/dist "$BUILD_DIR/$PACKAGE_NAME/ui/dist"

# Browser extension
cp -r extension "$BUILD_DIR/$PACKAGE_NAME/"

# Remove any __pycache__
find "$BUILD_DIR/$PACKAGE_NAME" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Remove any .pyc files
find "$BUILD_DIR/$PACKAGE_NAME" -name "*.pyc" -delete 2>/dev/null || true

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
exec bash wlib.sh "$@"
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
