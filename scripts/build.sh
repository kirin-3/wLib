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
cp wlib.desktop "$APPDIR/usr/share/applications/"
cp wlib.desktop "$APPDIR/"

# Create a simple icon if none exists
if [ ! -f "$PROJECT_DIR/wlib.png" ]; then
    # Generate a simple 256x256 icon using Python
    python3 -c "
import struct, zlib
def create_png(width, height, color):
    def chunk(ctype, data):
        return struct.pack('>I', len(data)) + ctype + data + struct.pack('>I', zlib.crc32(ctype + data) & 0xffffffff)
    raw = b''
    for y in range(height):
        raw += b'\x00'
        for x in range(width):
            raw += bytes(color) + b'\xff'
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
with open('wlib.png', 'wb') as f:
    f.write(create_png(256, 256, (70, 100, 220)))
" 2>/dev/null || echo "⚠️  Couldn't generate icon, AppImage will have no icon"
fi

if [ -f "$PROJECT_DIR/wlib.png" ]; then
    cp "$PROJECT_DIR/wlib.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
    cp "$PROJECT_DIR/wlib.png" "$APPDIR/"
    cp "$PROJECT_DIR/wlib.png" "$APPDIR/.DirIcon"
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
