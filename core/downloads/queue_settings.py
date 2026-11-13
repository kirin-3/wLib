# SPDX-License-Identifier: GPL-3.0-or-later
"""Download queue settings with defaults. Blank or invalid stored values mean the default.

All four keys are device-local: never exported or synced (they are not in
GENERAL_SETTINGS_KEYS).
"""
from __future__ import annotations

import sqlite3

LIMIT_KEY = "download_limit_kib"
MAX_ACTIVE_KEY = "download_max_active"
PAUSE_WHILE_PLAYING_KEY = "download_pause_while_playing"
PAUSE_ON_BATTERY_KEY = "download_pause_on_battery"
KEYS = (LIMIT_KEY, MAX_ACTIVE_KEY, PAUSE_WHILE_PLAYING_KEY, PAUSE_ON_BATTERY_KEY)

# Read once to seed LIMIT_KEY; an older version still finds it where it was.
LEGACY_TORRENT_LIMIT_KEY = "torrent_download_limit_kib"

DEFAULT_MAX_ACTIVE = 2
MIN_ACTIVE, MAX_ACTIVE = 1, 5
DEFAULT_PAUSE_WHILE_PLAYING = True
DEFAULT_PAUSE_ON_BATTERY = False
# 10 GiB/s; anything above is a typing mistake, not a limit.
MAX_LIMIT_KIB = 10 * 1024 * 1024


def _raw(key: str) -> str | None:
    from core import database

    try:
        value = database.get_setting(key)
    except (OSError, sqlite3.Error):
        return None
    return None if value is None else value.strip()


def _int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")) or value != int(value):
        return None
    return int(value)


def _bool(text: str | None, default: bool) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    return default


def limit_kib() -> int:
    """Shared download speed limit in KiB/s; 0 means unlimited."""
    value = _int(_raw(LIMIT_KEY))
    return value if value is not None and 0 <= value <= MAX_LIMIT_KIB else 0


def max_active() -> int:
    value = _int(_raw(MAX_ACTIVE_KEY))
    return value if value is not None and MIN_ACTIVE <= value <= MAX_ACTIVE else DEFAULT_MAX_ACTIVE


def pause_while_playing() -> bool:
    return _bool(_raw(PAUSE_WHILE_PLAYING_KEY), DEFAULT_PAUSE_WHILE_PLAYING)


def pause_on_battery() -> bool:
    return _bool(_raw(PAUSE_ON_BATTERY_KEY), DEFAULT_PAUSE_ON_BATTERY)


def values() -> dict[str, object]:
    """The four settings as the frontend sees them."""
    return {
        LIMIT_KEY: limit_kib(),
        MAX_ACTIVE_KEY: max_active(),
        PAUSE_WHILE_PLAYING_KEY: pause_while_playing(),
        PAUSE_ON_BATTERY_KEY: pause_on_battery(),
    }


def normalize_for_save(key: str, raw: object) -> str | None:
    """A clean stored string for a settings-form value, or None when it is invalid."""
    if key in (PAUSE_WHILE_PLAYING_KEY, PAUSE_ON_BATTERY_KEY):
        if isinstance(raw, bool):
            return "true" if raw else "false"
        text = str(raw).strip().lower() if isinstance(raw, str) else ""
        return text if text in ("true", "false") else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    value = _int(str(raw).strip())
    if value is None:
        return None
    if key == LIMIT_KEY:
        return str(value) if 0 <= value <= MAX_LIMIT_KIB else None
    if key == MAX_ACTIVE_KEY:
        return str(value) if MIN_ACTIVE <= value <= MAX_ACTIVE else None
    return None


def migrate_legacy_limit() -> bool:
    """Copy the torrent download limit to the shared limit once; True when copied.

    Runs only while the shared limit has never been saved. The old key stays
    in place and is no longer read.
    """
    if _raw(LIMIT_KEY) is not None:
        return False
    legacy = _int(_raw(LEGACY_TORRENT_LIMIT_KEY))
    from core import database

    value = legacy if legacy is not None and 0 <= legacy <= MAX_LIMIT_KIB else 0
    try:
        database.update_setting(LIMIT_KEY, str(value))
    except (OSError, sqlite3.Error):
        return False
    return value > 0
