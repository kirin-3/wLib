# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-local file-host account settings.

These keys live in the settings table but are deliberately absent from the
backup/sync allowlists, so they never leave this device.
"""
from __future__ import annotations

import sqlite3

PIXELDRAIN_API_KEY = "pixeldrain_api_key"
PIXELDRAIN_USERNAME = "pixeldrain_username"
MEGA_SESSION = "mega_session"
MEGA_EMAIL = "mega_email"
RPDL_USERNAME = "rpdl_username"
RPDL_PASSWORD = "rpdl_password"
RPDL_TOKEN = "rpdl_token"


def get(key: str) -> str:
    from core import database

    try:
        return database.get_setting(key) or ""
    except (OSError, sqlite3.Error):
        return ""


def put(values: dict[str, str]) -> None:
    from core import database

    database.update_settings(values)
