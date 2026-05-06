#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "Usage: scripts/generate-aur-package.sh <version-or-tag>" >&2
    exit 1
fi

PKGVER="${VERSION#v}"
PKGVER="${PKGVER//-/_}"
if [[ ! "$PKGVER" =~ ^[0-9][0-9A-Za-z._+~]*$ ]]; then
    echo "AUR pkgver must start with a digit; got: $PKGVER" >&2
    exit 1
fi

RELEASE_TAG="${VERSION}"
if [[ "$RELEASE_TAG" != v* ]]; then
    RELEASE_TAG="v${RELEASE_TAG}"
fi

ARTIFACT_VERSION="${VERSION}"
if [ -f "$PROJECT_DIR/dist/wLib-${RELEASE_TAG}-linux-x86_64.tar.gz" ]; then
    ARTIFACT_VERSION="$RELEASE_TAG"
elif [ -f "$PROJECT_DIR/dist/wLib-${PKGVER}-linux-x86_64.tar.gz" ]; then
    ARTIFACT_VERSION="$PKGVER"
fi

TARBALL="$PROJECT_DIR/dist/wLib-${ARTIFACT_VERSION}-linux-x86_64.tar.gz"
if [ ! -f "$TARBALL" ]; then
    echo "Release tarball not found: $TARBALL" >&2
    exit 1
fi

SHA256="$(sha256sum "$TARBALL" | awk '{print $1}')"
MAINTAINER="${WLIB_MAINTAINER:-wLib Maintainers <maintainers@example.invalid>}"
OUT_DIR="$PROJECT_DIR/dist/aur"
PKGBUILD_OUT="$OUT_DIR/PKGBUILD"
SRCINFO_OUT="$OUT_DIR/.SRCINFO"

mkdir -p "$OUT_DIR"

sed_escape() {
    printf '%s' "$1" | sed -e 's/[&|]/\\&/g'
}

sed \
    -e "s|@WLIB_MAINTAINER@|$(sed_escape "$MAINTAINER")|g" \
    -e "s|@PKGVER@|$(sed_escape "$PKGVER")|g" \
    -e "s|@RELEASE_TAG@|$(sed_escape "$RELEASE_TAG")|g" \
    -e "s|@ARTIFACT_VERSION@|$(sed_escape "$ARTIFACT_VERSION")|g" \
    -e "s|@TARBALL_SHA256@|$(sed_escape "$SHA256")|g" \
    "$PROJECT_DIR/packaging/aur/PKGBUILD.in" > "$PKGBUILD_OUT"

cat > "$SRCINFO_OUT" << SRCINFO
pkgbase = wlib-bin
	pkgdesc = Modern Linux game manager for F95Zone
	pkgver = ${PKGVER}
	pkgrel = 1
	url = https://github.com/kirin-3/wLib
	arch = x86_64
	license = MIT
	depends = ca-certificates
	depends = gtk3
	depends = libxkbcommon-x11
	depends = mesa-utils
	depends = wine
	depends = winetricks
	depends = xcb-util-image
	depends = xcb-util-keysyms
	depends = xcb-util-renderutil
	depends = xcb-util-wm
	optdepends = firefox: Firefox extension support
	optdepends = chromium: Chromium extension support
	optdepends = proton-ge-custom-bin: Proton-GE runtime support
	provides = wlib
	conflicts = wlib
	options = !strip
	source = wlib-bin-${PKGVER}.tar.gz::https://github.com/kirin-3/wLib/releases/download/${RELEASE_TAG}/wLib-${ARTIFACT_VERSION}-linux-x86_64.tar.gz
	sha256sums = ${SHA256}

pkgname = wlib-bin
SRCINFO

echo "Generated $PKGBUILD_OUT"
echo "Generated $SRCINFO_OUT"
