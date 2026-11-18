#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install the wLib catalog builder on the VPS (Rocky Linux). Run as root.
# Safe to run again: it updates the checkout, venv and units in place.
# The timer is installed but left off; enable it after the first publish.
set -euo pipefail

REPO_URL="${WLIB_CATALOG_REPO:-https://github.com/kirin-3/wLib.git}"
BRANCH="${WLIB_CATALOG_BRANCH:-master}"
APP_DIR=/opt/wlib-catalog
DATA_DIR=/var/lib/wlib-catalog
ETC_DIR=/etc/wlib-catalog
# Chromium for Scrapling; the unit sets the same PLAYWRIGHT_BROWSERS_PATH.
BROWSERS_DIR=/opt/wlib-catalog/.browsers
PYTHON="${WLIB_CATALOG_PYTHON:-python3}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

dnf install -y git xz python3 >/dev/null
# Rocky 9 ships Python 3.9 as python3; use its python3.12 package instead.
if [ -z "${WLIB_CATALOG_PYTHON:-}" ] \
    && ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    dnf install -y python3.12 >/dev/null
    PYTHON=python3.12
fi
# Shared libraries that Chromium needs.
dnf install -y nss atk at-spi2-atk cups-libs libdrm libxkbcommon libXcomposite libXdamage \
    libXfixes libXrandr mesa-libgbm pango alsa-lib >/dev/null
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "$PYTHON must be Python 3.12 or newer; set WLIB_CATALOG_PYTHON." >&2
    exit 1
fi

if ! id wlibcat >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_DIR" --shell /sbin/nologin wlibcat
fi
install -d -o wlibcat -g wlibcat -m 0750 "$DATA_DIR"
install -d -o root -g root -m 0755 "$ETC_DIR"
if [ ! -f "$ETC_DIR/r2.env" ]; then
    # Filled in by hand with the bucket-scoped R2 token.
    install -o root -g root -m 0600 /dev/null "$ETC_DIR/r2.env"
fi
if [ ! -f "$ETC_DIR/f95.env" ]; then
    # Optional: WLIB_CATALOG_F95_COOKIE=<value of F95's xf_user cookie>.
    install -o root -g root -m 0600 /dev/null "$ETC_DIR/f95.env"
fi
if [ ! -f "$ETC_DIR/lc.env" ]; then
    # Optional: WLIB_CATALOG_LC_COOKIE=<value of LewdCorner's xf_user cookie>.
    install -o root -g root -m 0600 /dev/null "$ETC_DIR/lc.env"
fi

if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    "$PYTHON" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/catalog_server/requirements.txt"
# Patchright has no Rocky build; it uses its Ubuntu build, which runs here.
PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" "$APP_DIR/.venv/bin/python" -m patchright install chromium
chown -R root:root "$APP_DIR"

install -m 0644 "$APP_DIR/catalog_server/deploy/wlib-catalog.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/catalog_server/deploy/wlib-catalog.timer" /etc/systemd/system/
install -m 0644 "$APP_DIR/catalog_server/deploy/wlib-catalog-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/wlib-catalog.service /etc/systemd/system/wlib-catalog.timer \
    /etc/systemd/system/wlib-catalog-tunnel.service
if systemctl is-enabled --quiet wlib-catalog-tunnel.service; then
    systemctl restart wlib-catalog-tunnel.service
fi

if systemctl is-enabled --quiet wlib-catalog.timer; then
    echo "Installed. The timer is enabled."
else
    echo "Installed. The timer is off; enable it with: systemctl enable --now wlib-catalog.timer"
fi
