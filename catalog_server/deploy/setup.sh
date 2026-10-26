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
PYTHON="${WLIB_CATALOG_PYTHON:-python3}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

dnf install -y git xz python3 >/dev/null
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
    # Filled in by hand with the bucket-scoped token; see docs/catalog.md.
    install -o root -g root -m 0600 /dev/null "$ETC_DIR/r2.env"
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
chown -R root:root "$APP_DIR"

install -m 0644 "$APP_DIR/catalog_server/deploy/wlib-catalog.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/catalog_server/deploy/wlib-catalog.timer" /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/wlib-catalog.service /etc/systemd/system/wlib-catalog.timer

echo "Installed. The timer is off; enable it with: systemctl enable --now wlib-catalog.timer"
