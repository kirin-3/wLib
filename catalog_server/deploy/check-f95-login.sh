#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Check the F95 login cookie in /etc/wlib-catalog/f95.env. Run as root.
# It runs like wlib-catalog.service, so the cookie never shows in a command line.
set -euo pipefail
exec systemd-run --quiet --wait --pipe --collect \
    -p User=wlibcat -p Group=wlibcat \
    -p WorkingDirectory=/opt/wlib-catalog \
    -p EnvironmentFile=-/etc/wlib-catalog/catalog.env \
    -p EnvironmentFile=-/etc/wlib-catalog/f95.env \
    -E WLIB_CATALOG_DATA_DIR=/var/lib/wlib-catalog \
    -E PLAYWRIGHT_BROWSERS_PATH=/opt/wlib-catalog/.browsers \
    -E PYTHONUNBUFFERED=1 \
    /opt/wlib-catalog/.venv/bin/python -m catalog_server check-f95-login
