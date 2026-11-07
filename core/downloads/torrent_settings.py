# SPDX-License-Identifier: GPL-3.0-or-later
"""Torrent settings with defaults. Blank or invalid stored values mean the default.

`torrent_seed_*` are in backups and sync (GENERAL_SETTINGS_KEYS). The speed
limits and port mapping are device-local: network-specific, never exported.
"""
from __future__ import annotations

import sqlite3

from core.downloads.seeding import DEFAULT_RATIO, MODE_ASK, SEED_MODES

SEED_MODE_KEY = "torrent_seed_mode"
SEED_RATIO_KEY = "torrent_seed_ratio"
SEED_HOURS_KEY = "torrent_seed_hours"
DOWNLOAD_LIMIT_KEY = "torrent_download_limit_kib"
UPLOAD_LIMIT_KEY = "torrent_upload_limit_kib"
PORT_MAPPING_KEY = "torrent_port_mapping"

DEVICE_LOCAL_KEYS = (DOWNLOAD_LIMIT_KEY, UPLOAD_LIMIT_KEY, PORT_MAPPING_KEY)


def _get(key: str) -> str:
    from core import database

    try:
        return (database.get_setting(key) or "").strip()
    except (OSError, sqlite3.Error):
        return ""


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value and value >= 0 and value != float("inf") else None


def seed_mode() -> str:
    value = _get(SEED_MODE_KEY)
    return value if value in SEED_MODES else MODE_ASK


def seed_ratio() -> float | None:
    """Ratio target; 0 or less means no ratio limit."""
    text = _get(SEED_RATIO_KEY)
    if not text:
        return DEFAULT_RATIO
    value = _number(text)
    if value is None:
        return DEFAULT_RATIO
    return value or None


def seed_hours() -> float | None:
    """Optional seeding time limit in hours; None means no limit."""
    value = _number(_get(SEED_HOURS_KEY))
    return value or None


def limits_kib() -> tuple[int, int]:
    """(download, upload) limits in KiB/s; 0 means unlimited."""
    return (int(_number(_get(DOWNLOAD_LIMIT_KEY)) or 0), int(_number(_get(UPLOAD_LIMIT_KEY)) or 0))


def port_mapping() -> bool:
    return _get(PORT_MAPPING_KEY) == "true"


def helper_options() -> dict[str, object]:
    download, upload = limits_kib()
    return {"download_kib": download, "upload_kib": upload, "port_mapping": port_mapping()}


def normalize_for_save(key: str, raw: object) -> str | None:
    """A clean stored string for a settings-form value, or None when it is invalid."""
    if key == SEED_MODE_KEY:
        return raw if isinstance(raw, str) and raw in SEED_MODES else None
    if key == PORT_MAPPING_KEY:
        return "true" if raw is True or str(raw).strip().lower() == "true" else "false"
    if isinstance(raw, bool):
        return None
    text = "" if raw is None else str(raw).strip()
    if key == SEED_HOURS_KEY and not text:
        return ""
    value = _number(text)
    if value is None:
        return None
    if key in (DOWNLOAD_LIMIT_KEY, UPLOAD_LIMIT_KEY):
        return str(int(value))
    return f"{value:g}"
